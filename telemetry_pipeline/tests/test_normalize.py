from datetime import datetime, timezone

import pytest

from stages.normalize import (
    _to_number,
    clean_mysql_explain_predicate_dynamic,
    normalize_active_query_timestamps,
    normalize_blocking_pids,
    normalize_locks,
    normalize_predicate,
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


def test_clean_mysql_predicate_removes_cache_but_keeps_table():
    cleaned = clean_mysql_explain_predicate_dynamic("(<cache>(`sbtest1`.`id`)</cache> = 42)")
    assert "cache" not in cleaned
    assert "`" not in cleaned
    assert cleaned == "((sbtest1.id) = 42)"


def test_clean_mysql_predicate_preserves_cast_function():
    cleaned = clean_mysql_explain_predicate_dynamic("cast(`a`.`k` as signed) = 42")
    assert cleaned == "CAST(a.k AS BIGINT) = 42"


def test_clean_mysql_predicate_removes_backticks():
    cleaned = clean_mysql_explain_predicate_dynamic("`sbtest2`.`id` = 17")
    assert cleaned is not None
    assert "`" not in cleaned
    assert cleaned == "sbtest2.id = 17"


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
    assert ops[0]["predicate"] == "(t.id = 5)"
    assert "`" not in ops[0]["predicate"]
    assert ops[1]["predicate"] is None