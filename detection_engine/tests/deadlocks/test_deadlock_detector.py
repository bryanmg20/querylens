"""Clasificacion de los abrazos mortales, sin clasificar y memoria entre
snapshots."""
from datetime import timedelta

from deadlock_builders import (
    DETECTED,
    PG_DETECTED,
    T0,
    mysql_ct01,
    mysql_ct02,
    pg_ct01,
    pg_ct02,
    pg_report,
    record_lock,
    report,
    scrambled,
    snapshot,
)

from deadlocks import ct01_inconsistent_order, ct02_mode_conversion, unclassified_deadlock
from deadlocks.deadlock_detector import classify, detect_deadlocks
from deadlocks.state import DEADLOCK_SEEN_RETENTION, ContentionState, DeadlockMemory

CT01 = ct01_inconsistent_order.ANTIPATRON
CT02 = ct02_mode_conversion.ANTIPATRON
UNCLASSIFIED = unclassified_deadlock.ANTIPATRON


# --------------------------------------------------------- clasificacion

def test_each_fixture_gets_its_pattern():
    assert classify(report(mysql_ct01())) == CT01
    assert classify(report(mysql_ct02())) == CT02
    assert classify(report(pg_ct01())) == CT01
    assert classify(report(pg_ct02())) == CT02
    assert classify(report(pg_report())) == UNCLASSIFIED


def test_gap_deadlocks_get_no_pattern_yet():
    # CT-03: con el detalle completo no es un abrazo "sin clasificar"
    raw = mysql_ct01(
        first={"holds": [record_lock(2, "X,GAP")], "waits_for": record_lock(3, "X,GAP,INSERT_INTENTION")},
        second={"holds": [record_lock(3, "X,GAP")], "waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    assert classify(report(raw)) is None


def test_one_unknown_row_makes_the_report_unclassified():
    assert classify(report(pg_report(rows=("row 1", None)))) == UNCLASSIFIED


def test_known_holds_without_targets_are_unclassified():
    raw = mysql_ct01()
    for trx in raw["transactions"]:
        trx["waits_for"]["targets"] = None
    assert classify(report(raw)) == UNCLASSIFIED


def test_classification_does_not_depend_on_the_engine():
    for raw in (mysql_ct01(), mysql_ct02(), pg_ct01(), pg_ct02(), pg_report()):
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
    assert set(memory.seen) == {DETECTED, PG_DETECTED}


def test_incomplete_and_unmatched_reports_are_remembered_without_finding():
    incomplete = {"detected_at": "2026-10-22 00:59:20.100000", "source": "mysql_error_log", "complete": False}
    gap = mysql_ct01(
        first={"holds": [record_lock(2, "X,GAP")], "waits_for": record_lock(3, "X,GAP,INSERT_INTENTION")},
        second={"holds": [record_lock(3, "X,GAP")], "waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    findings, memory = detect_deadlocks(snapshot(gap, incomplete), T0, DeadlockMemory())
    assert findings == []
    assert memory.seen == {DETECTED: "", "2026-10-22 00:59:20.100000": ""}
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
