import pytest

from stages.candidates import CandidatesStage
from stages.schema_resolver import (
    _build_user_schema_map,
    resolve_statements_schema,
)

pytestmark = pytest.mark.unit


class _FakePostgresCollector:
    source_dialect = "postgres"

    def preprocess_statements(self, stats):
        return stats


def test_build_user_schema_map_keeps_first_row_per_user():
    rows = [
        {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        {"user_id": 10, "username": "ql_user", "resolved_schema": "analytics"},
        {"user_id": 11, "username": "tenant", "resolved_schema": "tenant_a"},
    ]
    assert _build_user_schema_map(rows) == {10: "public", 11: "tenant_a"}


def test_build_user_schema_map_skips_bad_rows():
    rows = [
        {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        {"user_id": None, "username": "x", "resolved_schema": "y"},
        {"username": "z", "resolved_schema": "w"},
    ]
    assert _build_user_schema_map(rows) == {10: "public"}


def test_resolve_sets_schema_name_by_userid():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "statements": [{"userid": 10, "query_text": "SELECT c FROM sbtest1"}],
    }
    resolve_statements_schema(stats)
    assert stats["statements"][0]["schema_name"] == "public"
    assert "userid" not in stats["statements"][0]
    assert "schema_resolver" not in stats


def test_resolve_different_schema_per_user():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
            {"user_id": 11, "username": "tenant", "resolved_schema": "tenant_a"},
        ],
        "statements": [
            {"userid": 10, "query_text": "SELECT * FROM sbtest1"},
            {"userid": 11, "query_text": "SELECT * FROM sbtest1"},
        ],
    }
    resolve_statements_schema(stats)
    assert [s["schema_name"] for s in stats["statements"]] == ["public", "tenant_a"]
    assert all("userid" not in s for s in stats["statements"])


def test_resolve_missing_userid_returns_none():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "statements": [{"query_text": "SELECT * FROM sbtest1"}],
    }
    resolve_statements_schema(stats)
    assert stats["statements"][0]["schema_name"] is None
    assert "userid" not in stats["statements"][0]


def test_resolve_unknown_user_returns_none():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "statements": [{"userid": 99, "query_text": "SELECT * FROM sbtest1"}],
    }
    resolve_statements_schema(stats)
    assert stats["statements"][0]["schema_name"] is None
    assert "userid" not in stats["statements"][0]


def test_resolve_no_parse_no_query_text_needed():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "statements": [{"userid": 10}],
    }
    resolve_statements_schema(stats)
    assert stats["statements"][0]["schema_name"] == "public"
    assert "userid" not in stats["statements"][0]


def test_resolve_applies_to_candidates():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "top_impact_queries": [{"userid": 10, "query_text": "UPDATE sbtest1"}],
        "non_explainable_candidates": [{"userid": 10, "query_text": "SELECT 1"}],
    }
    resolve_statements_schema(stats)
    assert stats["top_impact_queries"][0]["schema_name"] == "public"
    assert stats["non_explainable_candidates"][0]["schema_name"] == "public"
    assert "userid" not in stats["top_impact_queries"][0]
    assert "userid" not in stats["non_explainable_candidates"][0]


def test_resolve_missing_resolver_is_noop():
    stats = {"statements": [{"query_id": 1}]}
    resolve_statements_schema(stats)
    assert "schema_name" not in stats["statements"][0]


def test_resolve_resolver_zero_rows_still_drops_key():
    stats = {"schema_resolver": [], "statements": [{"query_id": 1}]}
    resolve_statements_schema(stats)
    assert "schema_resolver" not in stats
    assert "schema_name" not in stats["statements"][0]


def test_resolve_drops_userid_after_lookup():
    stats = {
        "schema_resolver": [{"user_id": 10, "resolved_schema": "public"}],
        "statements": [{"userid": 10, "query_text": "SELECT 1"}],
        "top_impact_queries": [{"userid": 10, "query_text": "SELECT 1"}],
    }
    resolve_statements_schema(stats)
    assert stats["statements"][0]["schema_name"] == "public"
    assert "userid" not in stats["statements"][0]
    assert "userid" not in stats["top_impact_queries"][0]


def test_candidates_resolve_schema_before_explain():
    stats = {
        "schema_resolver": [
            {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
        ],
        "active_queries": [],
        "statements": [
            {
                "query_id": 1,
                "query_text": "SELECT * FROM sbtest1",
                "userid": 10,
                "coeff_of_variation": 3.0,
                "mean_time_ms": 50.0,
            }
        ],
    }
    CandidatesStage(_FakePostgresCollector()).execute(stats)
    top = stats["top_impact_queries"][0]
    assert top["schema_name"] == "public"
    assert "userid" not in top
    assert "schema_resolver" not in stats