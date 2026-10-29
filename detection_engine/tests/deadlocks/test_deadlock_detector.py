"""Clasificacion de los abrazos mortales, sin clasificar y memoria entre
snapshots."""
from datetime import timedelta

from deadlock_builders import (
    DETECTED,
    PG_DETECTED,
    T0,
    index_row,
    mysql_ct01,
    mysql_ct02,
    mysql_ct03,
    mysql_ct04,
    pg_ct01,
    pg_ct02,
    pg_report,
    record_lock,
    report,
    scrambled,
    snapshot,
)

from deadlocks import (
    ct01_inconsistent_order,
    ct02_mode_conversion,
    ct03_range_contention,
    ct04_lock_amplification,
    unclassified_deadlock,
)
from deadlocks.deadlock_detector import classify, detect_deadlocks
from deadlocks.state import DEADLOCK_SEEN_RETENTION, ContentionState, DeadlockMemory

CT01 = ct01_inconsistent_order.ANTIPATRON
CT02 = ct02_mode_conversion.ANTIPATRON
CT03 = ct03_range_contention.ANTIPATRON
CT04 = ct04_lock_amplification.ANTIPATRON
UNCLASSIFIED = unclassified_deadlock.ANTIPATRON


# --------------------------------------------------------- clasificacion

def test_each_fixture_gets_its_pattern():
    assert classify(report(mysql_ct01())) == CT01
    assert classify(report(mysql_ct02())) == CT02
    assert classify(report(pg_ct01())) == CT01
    assert classify(report(pg_ct02())) == CT02
    assert classify(report(pg_report())) == UNCLASSIFIED
    assert classify(report(mysql_ct03())) == CT03
    assert classify(report(mysql_ct04()), snapshot(indexes=[index_row("id")]).indexes) == CT04


def test_gap_deadlocks_are_ct03():
    raw = mysql_ct01(
        first={"holds": [record_lock(2, "X,GAP")], "waits_for": record_lock(3, "X,GAP,INSERT_INTENTION")},
        second={"holds": [record_lock(3, "X,GAP")], "waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    assert classify(report(raw)) == CT03


def test_amplification_with_a_usable_index_gets_no_pattern():
    # la tabla tiene indice por cliente: los bloqueos de mas vienen de otra cosa
    indexes = snapshot(indexes=[index_row("id"), index_row("cliente", name="ix_cliente")]).indexes
    assert classify(report(mysql_ct04()), indexes) is None


def test_one_unknown_row_makes_the_report_unclassified():
    assert classify(report(pg_report(rows=("row 1", None)))) == UNCLASSIFIED


def test_known_holds_without_targets_are_unclassified():
    raw = mysql_ct01()
    for trx in raw["transactions"]:
        trx["waits_for"]["targets"] = None
    assert classify(report(raw)) == UNCLASSIFIED


def test_rows_from_the_ctid_classify_like_rows_from_the_literals():
    assert classify(report(pg_ct01(targets_source="ctid"))) == CT01
    assert classify(report(pg_ct02(targets_source="ctid"))) == CT02


def test_rows_from_the_ctid_are_noted_in_the_evidence():
    for raw, antipatron in ((pg_ct01(targets_source="ctid"), CT01), (pg_ct02(targets_source="ctid"), CT02)):
        (finding,), _ = detect_deadlocks(snapshot(raw), T0, DeadlockMemory())
        assert finding.antipatron == antipatron
        limitations = finding.evidencia["limitations"]
        assert len(limitations) == 2
        assert "ctid" in limitations[-1]
        assert finding.evidencia["transactions"][0]["waits_for"]["targets_source"] == "ctid"


def test_rows_from_the_literals_add_no_ctid_note():
    (finding,), _ = detect_deadlocks(snapshot(pg_ct01()), T0, DeadlockMemory())
    assert not any("ctid" in limitation for limitation in finding.evidencia["limitations"])


def test_classification_does_not_depend_on_the_engine():
    for raw in (mysql_ct01(), mysql_ct02(), mysql_ct03(), mysql_ct04(), pg_ct01(), pg_ct02(), pg_report()):
        assert classify(report(scrambled(raw))) == classify(report(raw))


# ------------------------------------------------------- sin clasificar

def test_unclassified_finding_explains_both_causes_and_how_to_tell():
    findings, _ = detect_deadlocks(snapshot(pg_report()), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == UNCLASSIFIED
    assert finding.table_name == "ql_ct01_cuentas"
    assert finding.query_id == 1693770756014189287
    assert "CT-01" in finding.explicacion and "CT-02" in finding.explicacion
    assert "SELECT ... FOR UPDATE" in finding.recomendacion
    assert "log_lock_waits" in finding.recomendacion
    assert "SQLSTATE 40P01" in finding.recomendacion
    (limitation,) = finding.evidencia["limitations"]
    assert "sentencias preparadas" in limitation


def test_unclassified_of_an_unknown_engine_has_generic_texts():
    findings, _ = detect_deadlocks(snapshot(scrambled(pg_report())), T0, DeadlockMemory())
    (finding,) = findings
    assert finding.evidencia["limitations"] == [unclassified_deadlock._WHY_UNKNOWN_DEFAULT]
    assert "log_lock_waits" not in finding.recomendacion


# ----------------------------------------------------------- memoria

def test_same_report_in_the_next_snapshot_is_not_repeated():
    snap = snapshot(mysql_ct01())
    _, memory = detect_deadlocks(snap, T0, DeadlockMemory())
    findings, _ = detect_deadlocks(snap, T0 + timedelta(seconds=10), memory)
    assert findings == []


def test_recurring_signature_counts_and_raises_severity():
    memory = DeadlockMemory()
    severities = []
    for i in range(3):
        detected = f"2026-10-22 00:5{i}:26.000000"
        findings, memory = detect_deadlocks(snapshot(mysql_ct01(detected_at=detected)), T0, memory)
        severities.append((findings[0].evidencia["occurrences"], findings[0].severidad))
    assert severities == [(1, "medium"), (2, "medium"), (3, "high")]
    assert findings[0].evidencia["first_seen_at"] == "2026-10-22 00:50:26.000000"


def test_each_pattern_counts_its_own_recurrence():
    # las mismas sentencias, una vez como CT-01 y otra como CT-02
    findings, memory = detect_deadlocks(snapshot(pg_ct01()), T0, DeadlockMemory())
    later = pg_ct02(detected_at="2026-10-22 00:47:01.000000")
    second, memory = detect_deadlocks(snapshot(later), T0, memory)
    assert [f.antipatron for f in findings + second] == [CT01, CT02]
    assert second[0].evidencia["occurrences"] == 1
    assert len(memory.signatures) == 2


def test_both_patterns_in_one_snapshot():
    findings, memory = detect_deadlocks(snapshot(mysql_ct02(), pg_ct01()), T0, DeadlockMemory())
    assert sorted(f.antipatron for f in findings) == [CT01, CT02]
    assert set(memory.seen) == {f"{DETECTED}|237", f"{PG_DETECTED}|790"}


def test_two_deadlocks_in_the_same_millisecond_are_both_reported():
    # el log de Postgres tiene milisegundos: la hora sola no los distingue
    detected = "2026-10-22 00:59:30.123000"
    other = pg_ct01(detected_at=detected)
    for trx in other["transactions"]:
        trx["process_id"] += 100
        trx["blocked_by"] = [pid + 100 for pid in trx["blocked_by"]]
    both = snapshot(pg_ct01(detected_at=detected), other)
    findings, memory = detect_deadlocks(both, T0, DeadlockMemory())
    assert len(findings) == 2
    assert set(memory.seen) == {f"{detected}|790", f"{detected}|890"}

    again, _ = detect_deadlocks(both, T0 + timedelta(seconds=10), memory)
    assert again == []


def test_header_without_detail_of_a_report_already_seen_is_not_counted():
    # MySQL: el abrazo llego completo y en el ciclo siguiente, ya pisado en
    # INNODB STATUS por otro, solo su encabezado de error_log
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())
    header = {"detected_at": DETECTED, "source": "mysql_error_log", "complete": False}
    findings, after = detect_deadlocks(snapshot(header), T0 + timedelta(seconds=10), memory)
    assert findings == []
    assert after.seen == memory.seen


def test_report_seen_with_header_time_is_not_new_without_header():
    # MySQL: el primer ciclo trae el encabezado de error_log y el informe sale
    # con su hora; en el siguiente innodb_print_all_deadlocks esta apagado y
    # el mismo informe llega con la hora de INNODB STATUS, sin fracciones
    events = []
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory(), events)
    without_header = mysql_ct01(detected_at="2026-10-22 00:59:26.000000")
    findings, after = detect_deadlocks(snapshot(without_header), T0 + timedelta(seconds=10), memory, events)
    assert findings == []
    assert len(events) == 1
    assert after.seen == memory.seen

    # y al reves: primero sin encabezado, despues con el
    _, memory = detect_deadlocks(snapshot(without_header), T0, DeadlockMemory())
    findings, _ = detect_deadlocks(snapshot(mysql_ct01()), T0 + timedelta(seconds=10), memory)
    assert findings == []


def test_same_victim_with_two_exact_times_is_two_reports():
    # la misma conexion reintenta y vuelve a ser victima un segundo despues
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())
    retry = mysql_ct01(detected_at="2026-10-22 00:59:27.502113")
    findings, _ = detect_deadlocks(snapshot(retry), T0 + timedelta(seconds=10), memory)
    assert len(findings) == 1


def test_incomplete_and_unmatched_reports_are_remembered_without_finding():
    incomplete = {"detected_at": "2026-10-22 00:59:20.100000", "source": "mysql_error_log", "complete": False}
    # ampliacion de bloqueos con un indice que la sentencia podia usar: ningun patron
    unmatched = mysql_ct04()
    indexes = [index_row("id"), index_row("cliente", name="ix_cliente")]
    findings, memory = detect_deadlocks(snapshot(unmatched, incomplete, indexes=indexes), T0, DeadlockMemory())
    assert findings == []
    assert memory.seen == {f"{DETECTED}|447": "", "2026-10-22 00:59:20.100000": ""}
    assert memory.signatures == {}


def test_memory_forgets_old_reports():
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())
    later = T0 + DEADLOCK_SEEN_RETENTION + timedelta(seconds=11)
    assert memory.pruned(later).seen == {}
    # la firma se recuerda mas tiempo: el conteo de recurrencia sigue
    assert memory.pruned(later).signatures


def test_memory_round_trips_through_json():
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())
    assert DeadlockMemory.from_json(memory.to_json()) == memory
    assert DeadlockMemory.from_json(None) == DeadlockMemory()


# -------------------------------------------- estado entre snapshots y reintentos

def test_retry_of_the_same_snapshot_recomputes_against_the_previous_memory():
    snap = snapshot(mysql_ct02())
    state = ContentionState()
    findings, memory = detect_deadlocks(snap, T0, state.deadlocks_for(T0))
    state = state.advance(T0, {}, memory)
    assert len(findings) == 1

    # el job se reintenta (el hallazgo no llego a guardarse): mismo captured_at
    retry, memory = detect_deadlocks(snap, T0, state.deadlocks_for(T0))
    state = state.advance(T0, {}, memory)
    assert len(retry) == 1
    assert retry[0].evidencia["occurrences"] == 1

    # el snapshot siguiente ya lo vio
    t1 = T0 + timedelta(seconds=10)
    later, _ = detect_deadlocks(snap, t1, state.deadlocks_for(t1))
    assert later == []


def test_advance_without_deadlocks_keeps_the_memory():
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())
    state = ContentionState().advance(T0, {}, memory)
    t1 = T0 + timedelta(seconds=10)
    assert state.advance(t1, {}).deadlocks == memory


# ------------------------------------------------- eventos (deadlock_events)

def test_every_new_report_is_an_event_with_or_without_finding():
    incomplete = {"detected_at": "2026-10-22 00:59:20.100000", "source": "mysql_error_log", "complete": False}
    unmatched = mysql_ct04(detected_at="2026-10-22 00:59:22.000000")
    indexes = [index_row("id"), index_row("cliente", name="ix_cliente")]
    events = []
    findings, _ = detect_deadlocks(
        snapshot(mysql_ct01(), unmatched, incomplete, indexes=indexes), T0, DeadlockMemory(), events
    )
    assert len(findings) == 1
    # en orden de detected_at, como los procesa el detector
    assert [(e.detected_at, e.source, e.complete, e.antipatron) for e in events] == [
        ("2026-10-22 00:59:20.100000", "mysql_error_log", False, None),
        ("2026-10-22 00:59:22.000000", "innodb_status", True, None),
        (DETECTED, "innodb_status", True, CT01),
    ]
    assert events[2].report_key == f"{DETECTED}|265"


def test_a_report_already_seen_is_not_a_new_event():
    events = []
    _, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory(), events)
    header = {"detected_at": DETECTED, "source": "mysql_error_log", "complete": False}
    detect_deadlocks(snapshot(mysql_ct01(), header), T0 + timedelta(seconds=10), memory, events)
    assert len(events) == 1


# ------------------------------------------------- base del abrazo

def test_finding_and_event_name_the_schema_of_the_deadlock_not_the_registered_one():
    # MySQL: la conexion ve todo el servidor; el abrazo es de ql_otra aunque el
    # registro diga ql_demo
    raw = mysql_ct01()
    raw["database_name"] = "ql_otra"
    events = []
    findings, _ = detect_deadlocks(snapshot(raw), T0, DeadlockMemory(), events)
    assert [f.database_name for f in findings] == ["ql_otra"]
    assert [e.database_name for e in events] == ["ql_otra"]


def test_header_without_detail_has_no_database():
    header = {"detected_at": DETECTED, "source": "mysql_error_log", "complete": False}
    events = []
    detect_deadlocks(snapshot(header), T0, DeadlockMemory(), events)
    assert [e.database_name for e in events] == [None]
