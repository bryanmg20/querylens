from datetime import datetime, timedelta, timezone

import pytest

from stages.normalize import (
    _to_number,
    clean_mysql_explain_predicate_dynamic,
    normalize_active_query_timestamps,
    normalize_blocking_pids,
    normalize_locks,
    normalize_predicate,
    normalize_statement_epochs,
)

pytestmark = pytest.mark.unit


def test_to_number():
    assert _to_number(None) is None
    assert _to_number("42.5") == 42.5
    assert _to_number(7) == 7.0


def test_normalize_active_query_timestamps_drop_tz():
    stats = {"active_queries": [{"transaction_start_time": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)}]}
    normalize_active_query_timestamps(stats)
    assert stats["active_queries"][0]["transaction_start_time"] == "2026-01-01 12:00:00.000000"


def test_normalize_active_query_timestamps_converts_non_utc_to_utc():
    tz_bogota = timezone(timedelta(hours=-5))
    stats = {"active_queries": [{"transaction_start_time": datetime(2026, 1, 1, 7, 0, tzinfo=tz_bogota)}]}
    normalize_active_query_timestamps(stats)
    assert stats["active_queries"][0]["transaction_start_time"] == "2026-01-01 12:00:00.000000"


def test_normalize_statement_epochs_drop_tz():
    stats = {
        "statements": [{"query_id": 1, "counters_epoch": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)}],
    }
    normalize_statement_epochs(stats)
    assert stats["statements"][0]["counters_epoch"] == "2026-01-01 12:00:00.000000"


def test_normalize_statement_epochs_converts_non_utc_to_utc():
    tz_bogota = timezone(timedelta(hours=-5))
    stats = {
        "statements": [{"query_id": 1, "counters_epoch": datetime(2026, 1, 1, 7, 0, tzinfo=tz_bogota)}],
    }
    normalize_statement_epochs(stats)
    assert stats["statements"][0]["counters_epoch"] == "2026-01-01 12:00:00.000000"


def test_normalize_active_query_timestamps_none():
    stats = {"active_queries": [{"transaction_start_time": None}]}
    normalize_active_query_timestamps(stats)
    assert stats["active_queries"][0]["transaction_start_time"] is None


def test_normalize_blocking_pids_string():
    stats = {"active_queries": [{"blocking_pids": "1, 2,, 3x"}]}
    normalize_blocking_pids(stats)
    assert stats["active_queries"][0]["blocking_pids"] == [1, 2]


def test_normalize_blocking_pids_absent():
    stats = {"active_queries": [{"blocking_pids": None}]}
    normalize_blocking_pids(stats)
    assert stats["active_queries"][0]["blocking_pids"] == []


def test_normalize_locks_true_false():
    stats = {"locks": [{"is_granted": "GRANTED"}, {"is_granted": "WAITING"}, {"is_granted": None}]}
    normalize_locks(stats)
    assert stats["locks"][0]["is_granted"] is True
    assert stats["locks"][1]["is_granted"] is False
    assert stats["locks"][2]["is_granted"] is None


def test_normalize_locks_none_section():
    """N-1: CollectStage deja la seccion en None cuando la query de telemetry
    falla; el loop no debe iterar sobre None sino tratarlo como vacio."""
    stats = {"locks": None}
    normalize_locks(stats)
    assert stats["locks"] is None


def test_normalize_blocking_pids_none_section():
    stats = {"active_queries": None}
    normalize_blocking_pids(stats)
    assert stats["active_queries"] is None


def test_clean_mysql_predicate_removes_cache_but_keeps_table():
    cleaned = clean_mysql_explain_predicate_dynamic("(<cache>(`sbtest1`.`id`)</cache> = 42)")
    assert "cache" not in cleaned
    assert "`" not in cleaned
    assert cleaned == "((sbtest1.id) = $1)"


def test_clean_mysql_predicate_preserves_cast_function():
    cleaned = clean_mysql_explain_predicate_dynamic("cast(`a`.`k` as signed) = 42")
    assert cleaned == "CAST(a.k AS BIGINT) = $1"


def test_clean_mysql_predicate_removes_backticks():
    cleaned = clean_mysql_explain_predicate_dynamic("`sbtest2`.`id` = 17")
    assert cleaned is not None
    assert "`" not in cleaned
    assert cleaned == "sbtest2.id = $1"


def test_clean_mysql_predicate_redacts_string_literals():
    """N-1: el plan se arma con la query real, asi que un LIKE con el valor
    del cliente viajaba tal cual a la cola. El string se reemplaza entero."""
    cleaned = clean_mysql_explain_predicate_dynamic("(c LIKE '%7%')")
    assert cleaned == "(c LIKE $1)"


def test_clean_mysql_predicate_redacts_multiple_literals_in_reading_order():
    cleaned = clean_mysql_explain_predicate_dynamic("(a.id BETWEEN 2000 AND 4000)")
    assert cleaned == "(a.id BETWEEN $1 AND $2)"
    cleaned = clean_mysql_explain_predicate_dynamic("(a.id = 1 AND b.c = 'x')")
    assert cleaned == "(a.id = $1 AND b.c = $2)"


def test_clean_mysql_predicate_keeps_null_and_booleans():
    """NULL/TRUE/FALSE no son datos de cliente: redactarlos solo ensucia el
    predicado (a.id IS NULL es legible, a.id IS $1 no)."""
    assert clean_mysql_explain_predicate_dynamic("a.id IS NULL") == "a.id IS NULL"
    assert clean_mysql_explain_predicate_dynamic("a.flag = TRUE") == "a.flag = TRUE"


def test_clean_mysql_predicate_redacts_literals_on_fallback_path():
    """Si sqlglot no puede parsear, el fallback tampoco puede dejar pasar
    literales: la rama de error es un canal de fuga tanto como la principal."""
    cleaned = clean_mysql_explain_predicate_dynamic("id = 42 AND ((")
    assert cleaned == "id = $1 AND (("
    assert "42" not in cleaned


def test_clean_mysql_predicate_none():
    assert clean_mysql_explain_predicate_dynamic("") is None
    assert clean_mysql_explain_predicate_dynamic(None) is None


def test_clean_mysql_predicate_fallback_on_parse_error():
    cleaned = clean_mysql_explain_predicate_dynamic("(((")
    assert cleaned == "((("


def test_normalize_predicate_runs_over_plan():
    stats = {
        "canonic_explains": [
            {
                "canonical_plan": {
                    "physical_operations": [
                        {"predicate": "(`t`.`id` = 5)"},
                        {"predicate": None},
                    ]
                }
            }
        ]
    }
    normalize_predicate(stats)
    ops = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    assert ops[0]["predicate"] == "(t.id = $1)"
    assert "`" not in ops[0]["predicate"]
    assert ops[1]["predicate"] is None

@pytest.mark.parametrize(
    "predicate, secret",
    [
        ("(t.c = 0x41424344)", "41424344"),
        ("(t.c = X'0A1B')", "0A1B"),
        ("(t.f = b'1010')", "1010"),
    ],
)
def test_clean_mysql_predicate_redacts_hex_and_bit_literals(predicate, secret):
    """EXPLAIN de MySQL muestra los valores de columnas binarias como hex: era
    un canal de fuga igual que los strings (N-1)."""
    cleaned = clean_mysql_explain_predicate_dynamic(predicate)
    assert secret not in cleaned.upper()
    assert "$1" in cleaned


@pytest.mark.parametrize(
    "predicate, expected",
    [
        ("t.c = 0xDEAD AND ((", "t.c = $1 AND (("),
        ("t.c = X'0A1B' AND ((", "t.c = $1 AND (("),
        ("t.f = b'1010' AND ((", "t.f = $1 AND (("),
    ],
)
def test_clean_mysql_predicate_redacts_hex_on_fallback_path(predicate, expected):
    """El fallback por regex tampoco puede dejar el hex: ni crudo ni partido
    (X$1 o 0x$1 seguirian delatando la forma del dato)."""
    assert clean_mysql_explain_predicate_dynamic(predicate) == expected
