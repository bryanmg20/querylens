"""CT-01: abrazo mortal por orden inconsistente de acceso.

Los informes tienen la forma que deja DeadlocksStage en snapshot.deadlocks a
partir de los abrazos medidos en vivo contra ql_mysql y ql_postgres (los
fixtures de telemetry_pipeline/tests/golden/deadlocks/).
"""
from datetime import datetime, timedelta, timezone

from querylens_contract import SnapshotPayload

from deadlocks.ct01_inconsistent_order import (
    ANTIPATRON,
    deadlock_signature,
    detect_inconsistent_order,
    is_inconsistent_order,
)
from deadlocks.state import DEADLOCK_SEEN_RETENTION, ContentionState, DeadlockMemory

T0 = datetime(2026, 10, 22, 0, 59, 36, tzinfo=timezone.utc)
DETECTED = "2026-10-22 00:59:26.415843"

UPDATE_PLUS = "UPDATE ql_ct01_cuentas SET saldo = saldo + $1 WHERE id = $2"
UPDATE_MINUS = "UPDATE ql_ct01_cuentas SET saldo = saldo - $1 WHERE id = $2"


def _record_lock(heap, mode="X,REC_NOT_GAP", index="PRIMARY", table="ql_ct01_cuentas"):
    return {
        "schema_name": "ql_demo",
        "table_name": table,
        "index_name": index,
        "lock_type": "record",
        "lock_mode": mode,
        "resources": [f"12:4:{heap}"],
    }


def _mysql_trx(pid, holds, waits, blocked_by, victim=False, rows_locked=2, undo=1, query=UPDATE_PLUS):
    return {
        "process_id": pid,
        "transaction_id": str(63900 + pid),
        "canonic_query": query,
        "holds": holds,
        "waits_for": waits,
        "blocked_by": blocked_by,
        "rows_locked": rows_locked,
        "undo_log_entries": undo,
        "victim": victim,
    }


def _mysql_ct01(detected_at=DETECTED, **overrides):
    """Las filas 1 (heap 2) y 2 (heap 3) en orden opuesto, como el fixture."""
    first = _mysql_trx(264, [_record_lock(2)], _record_lock(3), [265])
    second = _mysql_trx(265, [_record_lock(3)], _record_lock(2), [264], victim=True)
    first.update(overrides.get("first", {}))
    second.update(overrides.get("second", {}))
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [first, second],
    }


def _mysql_ct02(detected_at=DETECTED):
    """FOR SHARE sobre la fila 1 y luego UPDATE: mismo registro, S que pasa a X."""
    return {
        "detected_at": detected_at,
        "source": "innodb_status",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [
            _mysql_trx(236, [_record_lock(2, "S,REC_NOT_GAP")], _record_lock(2), [237], undo=0, query=UPDATE_MINUS),
            _mysql_trx(237, [_record_lock(2, "S,REC_NOT_GAP")], _record_lock(2), [236], victim=True, undo=0, query=UPDATE_MINUS),
        ],
    }


def _pg_report(detected_at="2026-10-22 00:46:01.669000", same_target=False):
    def waits(xid):
        return {
            "lock_mode": "ShareLock",
            "lock_type": "transactionid",
            "resources": [f"transaction {xid}"],
            "table_name": "ql_ct01_cuentas",
        }
    return {
        "detected_at": detected_at,
        "source": "postgres_log",
        "database_name": "ql_demo",
        "complete": True,
        "transactions": [
            {"process_id": 790, "transaction_id": "67256", "query_id": 1693770756014189287,
             "canonic_query": UPDATE_PLUS, "waits_for": waits(67257), "blocked_by": [797], "victim": True},
            {"process_id": 797, "canonic_query": UPDATE_PLUS,
             "waits_for": waits(67257 if same_target else 67256), "blocked_by": [790]},
        ],
    }


def _snapshot(*reports):
    return SnapshotPayload.model_validate({
        "db_id": "db-1",
        "statements": [],
        "locks": [],
        "active_queries": [],
        "indexes": [],
        "tables": [],
        "columns": [],
        "server_start_timestamp": [],
        "deadlocks": list(reports),
    })


def _report(raw):
    return _snapshot(raw).deadlocks[0]


# --------------------------------------------------------------- la regla

def test_mysql_opposite_order_is_ct01():
    assert is_inconsistent_order(_report(_mysql_ct01()))


def test_mysql_next_key_exclusive_locks_count_as_exclusive():
    report = _mysql_ct01(
        first={"holds": [_record_lock(2, "X")], "waits_for": _record_lock(3, "X")},
        second={"holds": [_record_lock(3, "X")], "waits_for": _record_lock(2, "X")},
    )
    assert is_inconsistent_order(_report(report))


def test_mysql_mode_conversion_on_the_same_record_is_not_ct01():
    assert not is_inconsistent_order(_report(_mysql_ct02()))


def test_mysql_shared_hold_on_the_other_record_is_not_ct01():
    # cada una espera un registro distinto, pero quien la bloquea lo tiene en S
    report = _mysql_ct01(
        first={"holds": [_record_lock(2, "S,REC_NOT_GAP")]},
        second={"holds": [_record_lock(3, "S,REC_NOT_GAP")]},
    )
    assert not is_inconsistent_order(_report(report))


def test_mysql_insert_intention_on_a_gap_is_not_ct01():
    # CT-03: la espera es una insercion en un hueco bloqueado
    report = _mysql_ct01(
        first={"holds": [_record_lock(2, "X,GAP")], "waits_for": _record_lock(3, "X,GAP,INSERT_INTENTION")},
        second={"holds": [_record_lock(3, "X,GAP")], "waits_for": _record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    assert not is_inconsistent_order(_report(report))


def test_mysql_lock_amplification_is_left_for_ct04():
    # UPDATE sin indice: 200 registros bloqueados para 1 modificado
    report = _mysql_ct01(first={"rows_locked": 200, "undo_log_entries": 1})
    assert not is_inconsistent_order(_report(report))
    # 9 a 1 todavia no es ampliacion
    assert is_inconsistent_order(_report(_mysql_ct01(first={"rows_locked": 9, "undo_log_entries": 1})))


def test_mysql_three_transaction_cycle_is_ct01():
    report = _mysql_ct01()
    report["transactions"] = [
        _mysql_trx(1, [_record_lock(2)], _record_lock(3), [2]),
        _mysql_trx(2, [_record_lock(3)], _record_lock(4), [3]),
        _mysql_trx(3, [_record_lock(4)], _record_lock(2), [1], victim=True),
    ]
    assert is_inconsistent_order(_report(report))


def test_without_a_cycle_inside_the_report_is_not_ct01():
    report = _mysql_ct01(second={"blocked_by": [999]})
    assert not is_inconsistent_order(_report(report))


def test_postgres_waits_on_distinct_transactions_is_ct01():
    assert is_inconsistent_order(_report(_pg_report()))


def test_postgres_both_waiting_the_same_resource_is_not_ct01():
    assert not is_inconsistent_order(_report(_pg_report(same_target=True)))


def test_incomplete_report_is_not_classified():
    report = {"detected_at": DETECTED, "source": "mysql_error_log", "complete": False, "transactions": []}
    assert not is_inconsistent_order(_report(report))


# ------------------------------------------------------------ la firma

def test_signature_ignores_which_transaction_was_the_victim_and_the_order():
    a = _report(_mysql_ct01())
    swapped = _mysql_ct01()
    swapped["transactions"].reverse()
    for trx in swapped["transactions"]:
        trx["victim"] = not trx["victim"]
    assert deadlock_signature(a) == deadlock_signature(_report(swapped))


def test_signature_changes_with_the_statement_table_index_or_mode():
    base = deadlock_signature(_report(_mysql_ct01()))
    other_query = _mysql_ct01(first={"canonic_query": "UPDATE ql_ct01_cuentas SET saldo = $1 WHERE id = $2"})
    other_index = _mysql_ct01(first={"waits_for": _record_lock(3, index="k_1")})
    assert deadlock_signature(_report(other_query)) != base
    assert deadlock_signature(_report(other_index)) != base


def test_signature_does_not_depend_on_the_rows():
    # mismas sentencias sobre otras filas: el mismo defecto de la aplicacion
    other_rows = _mysql_ct01(
        first={"holds": [_record_lock(7)], "waits_for": _record_lock(8)},
        second={"holds": [_record_lock(8)], "waits_for": _record_lock(7)},
    )
    assert deadlock_signature(_report(other_rows)) == deadlock_signature(_report(_mysql_ct01()))


# ------------------------------------------------------ hallazgo y memoria

def test_finding_identifies_the_cycle_and_recommends_a_single_order():
    findings, memory = detect_inconsistent_order(_snapshot(_mysql_ct01()), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.severidad == "medium"
    assert finding.table_name == "ql_demo.ql_ct01_cuentas"
    evidence = finding.evidencia
    assert evidence["cycle_size"] == 2
    assert evidence["victim_process_id"] == 265
    assert [t["process_id"] for t in evidence["transactions"]] == [264, 265]
    assert evidence["transactions"][0]["waits_for"]["resources"] == ["12:4:3"]
    assert evidence["occurrences"] == 1
    assert evidence["detection_delay_s"] == 9.6
    assert "limitations" not in evidence
    assert "orden de acceso unico" in finding.recomendacion
    assert "en modo exclusivo" in finding.explicacion
    assert memory.seen == {DETECTED: evidence["signature"]}


def test_postgres_finding_notes_that_ct02_looks_the_same():
    findings, _ = detect_inconsistent_order(_snapshot(_pg_report()), T0, DeadlockMemory())
    (finding,) = findings
    assert finding.query_id == 1693770756014189287
    assert finding.table_name == "ql_ct01_cuentas"
    assert "CT-02" in finding.evidencia["limitations"][0]
    assert "en modo exclusivo" not in finding.explicacion


def test_same_report_in_the_next_snapshot_is_not_repeated():
    snapshot = _snapshot(_mysql_ct01())
    _, memory = detect_inconsistent_order(snapshot, T0, DeadlockMemory())
    findings, _ = detect_inconsistent_order(snapshot, T0 + timedelta(seconds=10), memory)
    assert findings == []


def test_recurring_signature_counts_and_raises_severity():
    memory = DeadlockMemory()
    severities = []
    for i in range(3):
        detected = f"2026-10-22 00:5{i}:26.000000"
        findings, memory = detect_inconsistent_order(
            _snapshot(_mysql_ct01(detected_at=detected)), T0, memory
        )
        severities.append((findings[0].evidencia["occurrences"], findings[0].severidad))
    assert severities == [(1, "medium"), (2, "medium"), (3, "high")]
    assert findings[0].evidencia["first_seen_at"] == "2026-10-22 00:50:26.000000"


def test_ct02_and_incomplete_reports_are_remembered_without_finding():
    incomplete = {"detected_at": "2026-10-22 00:59:20.100000", "source": "mysql_error_log", "complete": False}
    findings, memory = detect_inconsistent_order(_snapshot(_mysql_ct02(), incomplete), T0, DeadlockMemory())
    assert findings == []
    assert set(memory.seen) == {DETECTED, "2026-10-22 00:59:20.100000"}
    assert memory.signatures == {}


def test_memory_forgets_old_reports():
    _, memory = detect_inconsistent_order(_snapshot(_mysql_ct01()), T0, DeadlockMemory())
    later = T0 + DEADLOCK_SEEN_RETENTION + timedelta(seconds=11)
    assert memory.pruned(later).seen == {}
    # la firma se recuerda mas tiempo: el conteo de recurrencia sigue
    assert memory.pruned(later).signatures


def test_memory_round_trips_through_json():
    _, memory = detect_inconsistent_order(_snapshot(_mysql_ct01()), T0, DeadlockMemory())
    assert DeadlockMemory.from_json(memory.to_json()) == memory
    assert DeadlockMemory.from_json(None) == DeadlockMemory()


# -------------------------------------------- estado entre snapshots y reintentos

def test_retry_of_the_same_snapshot_recomputes_against_the_previous_memory():
    snapshot = _snapshot(_mysql_ct01())
    state = ContentionState()
    findings, memory = detect_inconsistent_order(snapshot, T0, state.deadlocks_for(T0))
    state = state.advance(T0, {}, memory)
    assert len(findings) == 1

    # el job se reintenta (el hallazgo no llego a guardarse): mismo captured_at
    retry, memory = detect_inconsistent_order(snapshot, T0, state.deadlocks_for(T0))
    state = state.advance(T0, {}, memory)
    assert len(retry) == 1
    assert retry[0].evidencia["occurrences"] == 1

    # el snapshot siguiente ya lo vio
    t1 = T0 + timedelta(seconds=10)
    later, _ = detect_inconsistent_order(snapshot, t1, state.deadlocks_for(t1))
    assert later == []


def test_advance_without_deadlocks_keeps_the_memory():
    _, memory = detect_inconsistent_order(_snapshot(_mysql_ct01()), T0, DeadlockMemory())
    state = ContentionState().advance(T0, {}, memory)
    t1 = T0 + timedelta(seconds=10)
    assert state.advance(t1, {}).deadlocks == memory
