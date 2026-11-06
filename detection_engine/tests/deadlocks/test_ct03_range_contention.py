"""CT-03: abrazo mortal por contencion de rango."""
from deadlock_builders import (
    T0,
    mysql_ct01,
    mysql_ct02,
    mysql_ct03,
    pg_ct01,
    pg_ct02,
    pg_report,
    record_lock,
    report,
    scrambled,
    snapshot,
)

from deadlocks.ct03_range_contention import ANTIPATRON, is_range_contention
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory

# --------------------------------------------------------------- la regla


def test_mysql_both_insert_into_the_gap_the_other_holds_is_ct03():
    assert is_range_contention(report(mysql_ct03()))


def test_mysql_gap_at_the_end_of_the_index_is_ct03():
    # el supremum se bloquea como next-key, no como gap
    assert is_range_contention(report(mysql_ct03(heap=1, hold="X")))


def test_mysql_next_key_hold_on_a_record_counts():
    # un UPDATE/DELETE por rango deja next-key: tambien frena la insercion
    assert is_range_contention(report(mysql_ct03(hold="X")))


def test_mysql_one_insert_in_the_cycle_is_enough():
    # la otra espera una fila comun: el ciclo pasa igual por un hueco
    raw = mysql_ct03(second={
        "holds": [record_lock(3, "X,GAP", table="ql_ct03_reservas")],
        "waits_for": record_lock(2, table="ql_ct03_reservas"),
    })
    raw["transactions"][0]["holds"].append(record_lock(2, table="ql_ct03_reservas"))
    assert is_range_contention(report(raw))


def test_mysql_holder_with_only_the_row_is_not_ct03():
    # REC_NOT_GAP no bloquea el hueco: la insercion no espera por eso
    raw = mysql_ct03(hold="X,REC_NOT_GAP")
    assert not is_range_contention(report(raw))


def test_mysql_holder_with_the_gap_of_another_record_is_not_ct03():
    raw = mysql_ct03(
        first={"holds": [record_lock(5, "X,GAP", table="ql_ct03_reservas")]},
        second={"holds": [record_lock(5, "X,GAP", table="ql_ct03_reservas")]},
    )
    assert not is_range_contention(report(raw))


def test_mysql_lock_amplification_is_left_for_ct04():
    raw = mysql_ct03(first={"rows_locked": 200, "undo_log_entries": 1})
    assert not is_range_contention(report(raw))


def test_mysql_row_cycles_are_not_ct03():
    assert not is_range_contention(report(mysql_ct01()))
    assert not is_range_contention(report(mysql_ct02()))


def test_without_a_cycle_inside_the_report_is_not_ct03():
    raw = mysql_ct03(second={"blocked_by": [999]})
    assert not is_range_contention(report(raw))


def test_without_holds_is_not_ct03():
    raw = mysql_ct03()
    for trx in raw["transactions"]:
        trx["holds"] = None
    assert not is_range_contention(report(raw))


def test_postgres_never_is_ct03():
    for raw in (pg_ct01(), pg_ct02(), pg_report()):
        assert not is_range_contention(report(raw))


def test_the_rule_reads_only_the_common_vocabulary():
    assert is_range_contention(report(scrambled(mysql_ct03())))


# ------------------------------------------------------------- hallazgo

def test_finding_names_the_index_and_the_gap():
    findings, _ = detect_deadlocks(snapshot(mysql_ct03()), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.severidad == "medium"
    assert finding.table_name == "ql_demo.ql_ct03_reservas"
    evidence = finding.evidencia
    assert evidence["gaps"] == [{
        "table": "ql_demo.ql_ct03_reservas",
        "index": "PRIMARY",
        "position": "before_record",
        "record": "12:4:3",
    }]
    (limitation,) = evidence["limitations"]
    assert "direccion fisica" in limitation
    assert evidence["victim_process_id"] == 423
    assert "todas quisieron insertar en el hueco antes de un registro del indice PRIMARY" in finding.explicacion
    assert "ON DUPLICATE KEY UPDATE" in finding.recomendacion
    assert "error 1213" in finding.recomendacion


def test_finding_at_the_end_of_the_index():
    findings, _ = detect_deadlocks(snapshot(mysql_ct03(heap=1, hold="X")), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.evidencia["gaps"][0]["position"] == "end_of_index"
    assert "el hueco al final del indice PRIMARY" in finding.explicacion


def test_finding_counts_only_the_transactions_that_insert():
    raw = mysql_ct03(second={
        "holds": [record_lock(3, "X,GAP", table="ql_ct03_reservas")],
        "waits_for": record_lock(2, table="ql_ct03_reservas"),
    })
    raw["transactions"][0]["holds"].append(record_lock(2, table="ql_ct03_reservas"))
    findings, _ = detect_deadlocks(snapshot(raw), T0, DeadlockMemory())

    (finding,) = findings
    assert "una de ellas quiso insertar" in finding.explicacion
    assert len(finding.evidencia["gaps"]) == 1
