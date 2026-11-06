"""CT-02: abrazo mortal por conversion de modo."""
from deadlock_builders import (
    T0,
    mysql_ct01,
    mysql_ct02,
    mysql_trx,
    pg_ct01,
    pg_ct02,
    pg_report,
    record_lock,
    report,
    scrambled,
    snapshot,
)

from deadlocks.ct02_mode_conversion import ANTIPATRON, is_mode_conversion
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory

# --------------------------------------------------------------- la regla


def test_mysql_shared_then_exclusive_on_the_same_record_is_ct02():
    assert is_mode_conversion(report(mysql_ct02()))


def test_mysql_next_key_shared_hold_counts():
    raw = mysql_ct02(
        first={"holds": [record_lock(2, "S")]},
        second={"holds": [record_lock(2, "S")]},
    )
    assert is_mode_conversion(report(raw))


def test_mysql_conversion_on_different_records_is_ct02():
    # las dos leyeron las filas 1 y 2 con FOR SHARE; una actualiza la 1 y la
    # otra la 2: cada una convierte lo que ya tenia en S
    shared_both = [record_lock(2, "S,REC_NOT_GAP"), record_lock(3, "S,REC_NOT_GAP")]
    raw = mysql_ct02()
    raw["transactions"] = [
        mysql_trx(1, shared_both, record_lock(2), [2], undo=0),
        mysql_trx(2, [dict(lock) for lock in shared_both], record_lock(3), [1], victim=True, undo=0),
    ]
    assert is_mode_conversion(report(raw))


def test_mysql_opposite_order_is_not_ct02():
    assert not is_mode_conversion(report(mysql_ct01()))


def test_mysql_holder_with_an_exclusive_lock_is_not_ct02():
    # la otra no lo tiene en S sino en X: no es conversion contra conversion
    raw = mysql_ct02(second={"holds": [record_lock(2)]})
    assert not is_mode_conversion(report(raw))


def test_mysql_holder_without_the_record_is_not_ct02():
    # cada una convierte su propia fila, pero la que la frena no tiene esa
    # fila: el informe no muestra que la bloqueaba (el stage puso a la otra
    # como bloqueadora porque no encontro el cruce)
    raw = mysql_ct02(
        first={"holds": [record_lock(2, "S,REC_NOT_GAP")], "waits_for": record_lock(2)},
        second={"holds": [record_lock(3, "S,REC_NOT_GAP")], "waits_for": record_lock(3)},
    )
    assert not is_mode_conversion(report(raw))


def test_mysql_shared_gap_lock_is_not_a_shared_row_lock():
    raw = mysql_ct02(
        first={"holds": [record_lock(2, "S,GAP")]},
        second={"holds": [record_lock(2, "S,GAP")]},
    )
    assert not is_mode_conversion(report(raw))


def test_mysql_conversion_into_an_insert_intention_is_not_ct02():
    # CT-03: pide insertar en el hueco, no la fila
    raw = mysql_ct02(
        first={"waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
        second={"waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    assert not is_mode_conversion(report(raw))


def test_mysql_lock_amplification_is_left_for_ct04():
    raw = mysql_ct02(first={"rows_locked": 200, "undo_log_entries": 0})
    assert not is_mode_conversion(report(raw))


def test_without_a_cycle_inside_the_report_is_not_ct02():
    raw = mysql_ct02(second={"blocked_by": [999]})
    assert not is_mode_conversion(report(raw))


def test_postgres_the_same_row_is_ct02():
    assert is_mode_conversion(report(pg_ct02()))


def test_postgres_distinct_rows_are_not_ct02():
    assert not is_mode_conversion(report(pg_ct01()))


def test_postgres_without_rows_is_not_ct02():
    assert not is_mode_conversion(report(pg_report()))


def test_the_rule_reads_only_the_common_vocabulary():
    assert is_mode_conversion(report(scrambled(mysql_ct02())))
    assert is_mode_conversion(report(scrambled(pg_ct02())))
    assert not is_mode_conversion(report(scrambled(mysql_ct01())))


# ------------------------------------------------------------- hallazgo

def test_mysql_finding_recommends_for_update():
    findings, _ = detect_deadlocks(snapshot(mysql_ct02()), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.severidad == "medium"
    assert finding.table_name == "ql_demo.ql_ct01_cuentas"
    evidence = finding.evidencia
    assert evidence["victim_process_id"] == 237
    assert evidence["transactions"][0]["holds"][0]["mode"] == "S"
    assert evidence["transactions"][0]["waits_for"]["mode"] == "X"
    assert "limitations" not in evidence
    assert "FOR UPDATE en lugar de FOR SHARE" in finding.recomendacion
    assert "clave foranea" in finding.recomendacion
    assert "error 1213" in finding.recomendacion
    assert "modo compartido (FOR SHARE)" in finding.explicacion


def test_postgres_finding_is_marked_as_inferred():
    findings, _ = detect_deadlocks(snapshot(pg_ct02()), T0, DeadlockMemory())

    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.query_id == 1693770756014189287
    (limitation,) = finding.evidencia["limitations"]
    assert limitation.startswith("Inferido")
    assert "la misma fila" in finding.explicacion
    assert "FOR KEY SHARE" in finding.recomendacion
    assert "SQLSTATE 40P01" in finding.recomendacion
