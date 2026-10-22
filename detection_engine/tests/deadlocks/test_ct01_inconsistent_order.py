"""CT-01: abrazo mortal por orden inconsistente de acceso."""
from deadlock_builders import (
    DETECTED,
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

from deadlocks.ct01_inconsistent_order import ANTIPATRON, is_inconsistent_order
from deadlocks.cycles import deadlock_signature
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory

# --------------------------------------------------------------- la regla


def test_mysql_opposite_order_is_ct01():
    assert is_inconsistent_order(report(mysql_ct01()))


def test_mysql_next_key_exclusive_locks_count_as_exclusive():
    raw = mysql_ct01(
        first={"holds": [record_lock(2, "X")], "waits_for": record_lock(3, "X")},
        second={"holds": [record_lock(3, "X")], "waits_for": record_lock(2, "X")},
    )
    assert is_inconsistent_order(report(raw))


def test_mysql_mode_conversion_on_the_same_record_is_not_ct01():
    assert not is_inconsistent_order(report(mysql_ct02()))


def test_mysql_shared_hold_on_the_other_record_is_not_ct01():
    # cada una espera un registro distinto, pero quien la bloquea lo tiene en S
    raw = mysql_ct01(
        first={"holds": [record_lock(2, "S,REC_NOT_GAP")]},
        second={"holds": [record_lock(3, "S,REC_NOT_GAP")]},
    )
    assert not is_inconsistent_order(report(raw))


def test_mysql_insert_intention_on_a_gap_is_not_ct01():
    # CT-03: la espera es una insercion en un hueco bloqueado
    raw = mysql_ct01(
        first={"holds": [record_lock(2, "X,GAP")], "waits_for": record_lock(3, "X,GAP,INSERT_INTENTION")},
        second={"holds": [record_lock(3, "X,GAP")], "waits_for": record_lock(2, "X,GAP,INSERT_INTENTION")},
    )
    assert not is_inconsistent_order(report(raw))


def test_mysql_lock_amplification_is_left_for_ct04():
    # UPDATE sin indice: 200 registros bloqueados para 1 modificado
    raw = mysql_ct01(first={"rows_locked": 200, "undo_log_entries": 1})
    assert not is_inconsistent_order(report(raw))
    # 9 a 1 todavia no es ampliacion
    assert is_inconsistent_order(report(mysql_ct01(first={"rows_locked": 9, "undo_log_entries": 1})))


def test_mysql_three_transaction_cycle_is_ct01():
    raw = mysql_ct01()
    raw["transactions"] = [
        mysql_trx(1, [record_lock(2)], record_lock(3), [2]),
        mysql_trx(2, [record_lock(3)], record_lock(4), [3]),
        mysql_trx(3, [record_lock(4)], record_lock(2), [1], victim=True),
    ]
    assert is_inconsistent_order(report(raw))


def test_without_a_cycle_inside_the_report_is_not_ct01():
    raw = mysql_ct01(second={"blocked_by": [999]})
    assert not is_inconsistent_order(report(raw))


def test_postgres_distinct_rows_are_ct01():
    assert is_inconsistent_order(report(pg_ct01()))


def test_postgres_the_same_row_is_not_ct01():
    assert not is_inconsistent_order(report(pg_ct02()))


def test_postgres_without_rows_is_not_ct01():
    # no se sabe si esperaban filas distintas: sin clasificar
    assert not is_inconsistent_order(report(pg_report()))


def test_postgres_both_waiting_the_same_tuple_is_not_ct01():
    # la espera de tupla si identifica la fila: las dos esperan la misma
    raw = pg_report()
    for trx in raw["transactions"]:
        trx["waits_for"].update(
            lock_type="tuple", lock_mode="ExclusiveLock",
            resources=["tuple (0,1) of relation 16384"], targets=["tuple (0,1) of relation 16384"],
        )
    assert not is_inconsistent_order(report(raw))


def test_requested_shared_lock_is_not_ct01():
    raw = pg_ct01()
    raw["transactions"][0]["waits_for"]["mode"] = "S"
    assert not is_inconsistent_order(report(raw))


def test_incomplete_report_is_not_classified():
    raw = {"detected_at": DETECTED, "source": "mysql_error_log", "complete": False, "transactions": []}
    assert not is_inconsistent_order(report(raw))


# ------------------------------------------------- independiente del motor

def test_the_rule_reads_only_the_common_vocabulary():
    assert is_inconsistent_order(report(scrambled(mysql_ct01())))
    assert not is_inconsistent_order(report(scrambled(mysql_ct02())))
    assert is_inconsistent_order(report(scrambled(pg_ct01())))


def test_a_new_engine_with_full_detail_is_checked_like_mysql():
    raw = scrambled(mysql_ct01(first={"holds": [record_lock(2, "S,REC_NOT_GAP")]}))
    assert not is_inconsistent_order(report(raw))


# ------------------------------------------------------------ la firma

def test_signature_ignores_which_transaction_was_the_victim_and_the_order():
    a = report(mysql_ct01())
    swapped = mysql_ct01()
    swapped["transactions"].reverse()
    for trx in swapped["transactions"]:
        trx["victim"] = not trx["victim"]
    assert deadlock_signature(a, ANTIPATRON) == deadlock_signature(report(swapped), ANTIPATRON)


def test_signature_changes_with_the_statement_table_index_or_mode():
    base = deadlock_signature(report(mysql_ct01()), ANTIPATRON)
    other_query = mysql_ct01(first={"canonic_query": "UPDATE ql_ct01_cuentas SET saldo = $1 WHERE id = $2"})
    other_index = mysql_ct01(first={"waits_for": record_lock(3, index="k_1")})
    other_mode = mysql_ct01(first={"waits_for": record_lock(3, "X")})
    assert deadlock_signature(report(other_query), ANTIPATRON) != base
    assert deadlock_signature(report(other_index), ANTIPATRON) != base
    assert deadlock_signature(report(other_mode), ANTIPATRON) != base


def test_signature_does_not_depend_on_the_rows():
    # mismas sentencias sobre otras filas: el mismo defecto de la aplicacion
    other_rows = mysql_ct01(
        first={"holds": [record_lock(7)], "waits_for": record_lock(8)},
        second={"holds": [record_lock(8)], "waits_for": record_lock(7)},
    )
    assert deadlock_signature(report(other_rows), ANTIPATRON) == deadlock_signature(report(mysql_ct01()), ANTIPATRON)


def test_signature_depends_on_the_pattern():
    assert deadlock_signature(report(pg_ct01()), ANTIPATRON) != deadlock_signature(report(pg_ct01()), "otro")


# ------------------------------------------------------------- hallazgo

def test_finding_identifies_the_cycle_and_recommends_a_single_order():
    findings, memory = detect_deadlocks(snapshot(mysql_ct01()), T0, DeadlockMemory())

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
    assert "error 1213" in finding.recomendacion
    assert "en modo exclusivo" in finding.explicacion
    assert memory.seen == {f"{DETECTED}|265": evidence["signature"]}


def test_postgres_finding_notes_that_holds_are_unknown():
    findings, _ = detect_deadlocks(snapshot(pg_ct01()), T0, DeadlockMemory())
    (finding,) = findings
    assert finding.antipatron == ANTIPATRON
    assert finding.query_id == 1693770756014189287
    assert finding.table_name == "ql_ct01_cuentas"
    (limitation,) = finding.evidencia["limitations"]
    assert "CT-02" in limitation
    assert finding.evidencia["transactions"][0]["holds"] is None
    assert "en modo exclusivo" not in finding.explicacion
    assert "SQLSTATE 40P01" in finding.recomendacion


def test_unknown_engine_recommendation_names_no_error_code():
    findings, _ = detect_deadlocks(snapshot(scrambled(pg_ct01())), T0, DeadlockMemory())
    (finding,) = findings
    assert "40P01" not in finding.recomendacion and "1213" not in finding.recomendacion
