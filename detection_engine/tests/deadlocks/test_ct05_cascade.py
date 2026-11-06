"""CT-05: cascada de bloqueo por transaccion larga.

Los snapshots son minimos y se validan con el contrato, igual que los que
llegan por la cola. La cadena 1814 -> 1813 -> 1812 es la que se midio en vivo
contra ql_postgres: blocking_pids apunta a la sesion de delante en la cola,
no a la raiz.
"""
from datetime import datetime, timedelta, timezone

from querylens_contract import SnapshotPayload

from deadlocks.ct05_long_transaction_cascade import ANTIPATRON, detect_long_transaction_cascade
from deadlocks.state import MAX_SAMPLE_GAP, CascadeRoot, ContentionState
from deadlocks.wait_graph import build_wait_graph

T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _snapshot(captured_at, root_age_s=40, wait_s=10, waiters=2, root_state="idle in transaction", chain=True):
    root_start = captured_at - timedelta(seconds=root_age_s)
    active = [{
        "process_id": 100,
        "query_id": 1,
        "query_text": "UPDATE cuentas SET saldo = saldo - $1 WHERE id = $2",
        "transaction_start_time": _ts(root_start),
        "session_state": root_state,
        "blocking_pids": [],
    }]
    for i in range(waiters):
        pid = 101 + i
        # en cadena cada una espera a la de delante, como reporta el motor
        blocker = 100 if (i == 0 or not chain) else pid - 1
        active.append({
            "process_id": pid,
            "query_id": 2,
            "query_text": "UPDATE cuentas SET saldo = saldo + $1 WHERE id = $2",
            "transaction_start_time": _ts(captured_at - timedelta(seconds=wait_s)),
            "session_state": "active",
            "wait_start_time": _ts(captured_at - timedelta(seconds=wait_s)),
            "blocking_pids": [blocker],
        })
    return SnapshotPayload.model_validate({
        "db_id": "db-1",
        "statements": [],
        "locks": [
            {"process_id": 100, "table_name": "cuentas", "lock_mode": "RowExclusiveLock", "is_granted": True},
            {"process_id": 101, "table_name": None, "lock_mode": "ShareLock", "is_granted": False},
        ],
        "active_queries": active,
        "indexes": [],
        "tables": [],
        "columns": [],
        "server_start_timestamp": [],
        "capture_timestamp": [{"captured_at": _ts(captured_at)}],
    })


def _two_samples(**kwargs):
    t1 = T0 + timedelta(seconds=10)
    _, prev = detect_long_transaction_cascade(_snapshot(T0, **kwargs), T0, {})
    findings, roots = detect_long_transaction_cascade(_snapshot(t1, **{**kwargs, "root_age_s": kwargs.get("root_age_s", 40) + 10}), t1, prev)
    return findings, roots


def test_wait_graph_follows_the_queue_to_the_root():
    graph = build_wait_graph(_snapshot(T0, waiters=3).active_queries)
    assert graph.roots() == [100]
    assert graph.blocked_under(100) == {101, 102, 103}


def test_wait_graph_cycle_has_no_root_and_does_not_loop():
    snap = _snapshot(T0)
    rows = snap.active_queries
    rows[0].blocking_pids = [102]
    graph = build_wait_graph(rows)
    assert graph.roots() == []
    assert graph.blocked_under(100) == {101, 102}


def test_first_sample_does_not_report_yet():
    findings, roots = detect_long_transaction_cascade(_snapshot(T0), T0, {})
    assert findings == []
    assert list(roots) == [(100, _ts(T0 - timedelta(seconds=40)))]


def test_second_consecutive_sample_reports_root_and_chain():
    findings, _ = _two_samples(waiters=3)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.antipatron == ANTIPATRON
    assert finding.query_id == 1
    assert finding.table_name == "cuentas"
    root = finding.evidencia["root"]
    assert root["process_id"] == 100
    assert root["session_state"] == "idle in transaction"
    assert root["transaction_age_s"] == 50
    assert [s["process_id"] for s in finding.evidencia["blocked_sessions"]] == [101, 102, 103]
    assert finding.evidencia["blocked_waiting_5s"] == 3
    assert "idle in transaction" in finding.explicacion
    assert "pg_terminate_backend(100)" in finding.recomendacion


def test_reported_root_is_not_repeated_while_open():
    findings, roots = _two_samples()
    assert len(findings) == 1
    t2 = T0 + timedelta(seconds=20)
    again, roots = detect_long_transaction_cascade(_snapshot(t2, root_age_s=60), t2, roots)
    assert again == []
    assert all(root.reported for root in roots.values())


def test_young_root_does_not_qualify():
    findings, roots = _two_samples(root_age_s=15)
    assert findings == [] and roots == {}


def test_one_blocked_session_is_plain_contention():
    findings, roots = _two_samples(waiters=1)
    assert findings == [] and roots == {}


def test_short_waits_do_not_count():
    findings, roots = _two_samples(wait_s=2)
    assert findings == [] and roots == {}


def test_missing_wait_start_time_does_not_count():
    snap = _snapshot(T0)
    for row in snap.active_queries[1:]:
        row.wait_start_time = None
    _, roots = detect_long_transaction_cascade(snap, T0, {})
    assert roots == {}


def test_reused_pid_is_a_different_root():
    """Mismo pid con otra transaccion: no es consecutiva, empieza de cero."""
    prev = {(100, "2026-10-14 11:00:00.000000"): CascadeRoot(100, "2026-10-14 11:00:00.000000", T0)}
    findings, roots = detect_long_transaction_cascade(_snapshot(T0), T0, prev)
    assert findings == []
    assert all(not root.reported for root in roots.values())


def test_impact_is_accumulated_wait_and_sets_severity():
    """Seccion 3.3: el impacto de contencion es la espera acumulada de las
    sesiones afectadas; con 5 sesiones de 25 s (125 s) es alta."""
    findings, _ = _two_samples(waiters=5, wait_s=25)
    assert findings[0].evidencia["impact_wait_s"] == 125
    assert findings[0].severidad == "high"
    findings, _ = _two_samples(waiters=2, wait_s=10)
    assert findings[0].evidencia["impact_wait_s"] == 20
    assert findings[0].severidad == "medium"


def test_finding_declares_risk():
    findings, _ = _two_samples()
    assert "revierte" in findings[0].evidencia["risk"]


def test_state_skips_out_of_order_snapshot():
    state = ContentionState(captured_at=T0)
    assert state.previous_for(T0 - timedelta(seconds=10)) is None


def test_state_gap_breaks_consecutiveness():
    root = CascadeRoot(100, "x", T0)
    state = ContentionState(captured_at=T0, roots={root.key: root})
    assert state.previous_for(T0 + MAX_SAMPLE_GAP + timedelta(seconds=1)) == {}
    assert state.previous_for(T0 + timedelta(seconds=10)) == {root.key: root}


def test_state_retry_recomputes_against_same_previous_sample():
    """Si el job se reintenta (el hallazgo no se guardo), el estado ya avanzo:
    tiene que volver a comparar contra la muestra anterior y reportar igual."""
    t1 = T0 + timedelta(seconds=10)
    state = ContentionState()
    _, roots0 = detect_long_transaction_cascade(_snapshot(T0), T0, state.previous_for(T0))
    state = state.advance(T0, roots0)
    first, roots1 = detect_long_transaction_cascade(_snapshot(t1, root_age_s=50), t1, state.previous_for(t1))
    state = state.advance(t1, roots1)
    retry, _ = detect_long_transaction_cascade(_snapshot(t1, root_age_s=50), t1, state.previous_for(t1))
    assert len(first) == 1 and len(retry) == 1


def _session(pid, blockers, captured_at, age_s=10, state="active"):
    start = _ts(captured_at - timedelta(seconds=age_s))
    return {
        "process_id": pid,
        "query_id": pid,
        "transaction_start_time": start,
        "session_state": state,
        "wait_start_time": start if blockers else None,
        "blocking_pids": blockers,
    }


def _raw_snapshot(captured_at, sessions):
    return SnapshotPayload.model_validate({
        "db_id": "db-1",
        "statements": [],
        "locks": [],
        "active_queries": sessions,
        "indexes": [],
        "tables": [],
        "columns": [],
        "server_start_timestamp": [],
        "capture_timestamp": [{"captured_at": _ts(captured_at)}],
    })


def _two_roots_samples(build):
    t1 = T0 + timedelta(seconds=10)
    _, prev = detect_long_transaction_cascade(_raw_snapshot(T0, build(T0, 0)), T0, {})
    return detect_long_transaction_cascade(_raw_snapshot(t1, build(t1, 10)), t1, prev)


def test_independent_cascades_report_one_finding_per_root():
    """A retiene un recurso y B otro: dos arboles separados, dos hallazgos."""
    def build(at, elapsed):
        return [
            _session(100, [], at, age_s=40 + elapsed, state="idle in transaction"),
            _session(101, [100], at, age_s=20 + elapsed),
            _session(102, [101], at, age_s=20 + elapsed),
            _session(200, [], at, age_s=35 + elapsed, state="idle in transaction"),
            _session(201, [200], at, age_s=15 + elapsed),
            _session(202, [201], at, age_s=15 + elapsed),
            _session(203, [202, 201], at, age_s=15 + elapsed),
        ]

    findings, roots = _two_roots_samples(build)
    by_root = {f.evidencia["root"]["process_id"]: f for f in findings}
    assert set(by_root) == {100, 200}
    assert {s["process_id"] for s in by_root[100].evidencia["blocked_sessions"]} == {101, 102}
    assert {s["process_id"] for s in by_root[200].evidencia["blocked_sessions"]} == {201, 202, 203}
    assert {key[0] for key in roots} == {100, 200}


def test_session_waiting_on_two_roots_appears_under_both():
    """101 espera a A y a B a la vez (p. ej. FOR SHARE de ambas sobre la misma
    fila): cuenta en el arbol de cada raiz y su espera entra en ambos impactos."""
    def build(at, elapsed):
        return [
            _session(100, [], at, age_s=40 + elapsed, state="idle in transaction"),
            _session(200, [], at, age_s=40 + elapsed, state="idle in transaction"),
            _session(101, [100, 200], at, age_s=20 + elapsed),
            _session(102, [100], at, age_s=20 + elapsed),
            _session(201, [200], at, age_s=20 + elapsed),
        ]

    findings, _ = _two_roots_samples(build)
    by_root = {f.evidencia["root"]["process_id"]: f for f in findings}
    assert set(by_root) == {100, 200}
    assert {s["process_id"] for s in by_root[100].evidencia["blocked_sessions"]} == {101, 102}
    assert {s["process_id"] for s in by_root[200].evidencia["blocked_sessions"]} == {101, 201}
    assert by_root[100].evidencia["impact_wait_s"] == 60
    assert by_root[200].evidencia["impact_wait_s"] == 60


def test_only_the_root_that_meets_the_rule_reports():
    """Dos raices, pero una sola tiene >= 2 bloqueadas: un solo hallazgo."""
    def build(at, elapsed):
        return [
            _session(100, [], at, age_s=40 + elapsed, state="idle in transaction"),
            _session(101, [100], at, age_s=20 + elapsed),
            _session(102, [100], at, age_s=20 + elapsed),
            _session(200, [], at, age_s=40 + elapsed, state="idle in transaction"),
            _session(201, [200], at, age_s=20 + elapsed),
        ]

    findings, roots = _two_roots_samples(build)
    assert [f.evidencia["root"]["process_id"] for f in findings] == [100]
    assert {key[0] for key in roots} == {100}


def _with_lock_databases(snap, root_db, waiter_db):
    for lock in snap.locks:
        lock.database_name = root_db if lock.process_id == 100 else waiter_db
    return snap


def test_finding_names_the_database_where_the_sessions_wait():
    t1 = T0 + timedelta(seconds=10)
    _, prev = detect_long_transaction_cascade(_with_lock_databases(_snapshot(T0), "ql_otra", "ql_otra"), T0, {})
    findings, _ = detect_long_transaction_cascade(
        _with_lock_databases(_snapshot(t1, root_age_s=50), "ql_otra", "ql_otra"), t1, prev
    )
    assert [f.database_name for f in findings] == ["ql_otra"]


def test_finding_falls_back_to_the_root_session_database():
    findings, _ = _two_samples()
    assert findings and findings[0].database_name is None
