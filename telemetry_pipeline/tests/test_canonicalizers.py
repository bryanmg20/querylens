import pytest

from stages.canonicalizers import (
    canonicalize_query,
    clean_mysql_sintax,
    create_canonic_queries,
    normalize_querytext_active,
)

pytestmark = pytest.mark.unit


def test_canonicalize_query_replaces_literals():
    q = canonicalize_query("SELECT * FROM t WHERE id = 42", "postgres")
    assert "42" not in q
    assert "$1" in q


def test_canonicalize_query_empty_returns_none():
    assert canonicalize_query("", "postgres") is None
    assert canonicalize_query(None, "postgres") is None


def test_canonicalize_query_fallback_on_parse_error():
    text = "  SELECT %s WHERE ---  "
    q = canonicalize_query(text, "postgres")
    assert q == "Not available"


def test_canonicalize_query_mysql_dialect():
    q = canonicalize_query("SELECT a FROM t WHERE b LIKE 'x%'", "mysql")
    assert q == "SELECT a FROM t WHERE b LIKE $1"


def test_clean_mysql_sintax():
    assert clean_mysql_sintax("SELECT DISTINCTROW a FROM t") == "SELECT DISTINCT a FROM t"


def test_create_canonic_queries_pure():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "SELECT 1"}
        ]
    }
    create_canonic_queries(stats)
    assert stats["top_impact_queries"][0]["canonic_query"] == "SELECT $1"


def test_create_canonic_queries_clean_mysql():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "SELECT DISTINCTROW 1"}
        ]
    }
    create_canonic_queries(stats, source_dialect="mysql", clean_mysql=True)
    assert "DISTINCTROW" not in stats["top_impact_queries"][0]["canonic_query"]


def test_normalize_querytext_active_drops_key():
    stats = {"active_queries": [{"query_id": 1, "query_text": "SELECT 1"}]}
    normalize_querytext_active(stats)
    row = stats["active_queries"][0]
    assert "query_text" not in row
    assert "canonic_query" in row


def test_normalize_querytext_active_empty():
    stats = {"active_queries": [{"query_id": 1, "query_text": None}]}
    normalize_querytext_active(stats)
    assert stats["active_queries"][0]["canonic_query"] == "Not available"


def test_normalize_querytext_active_none_section():
    """N-1: CollectStage deja active_queries en None cuando falla; el loop no
    debe crashear con TypeError por iterar sobre None."""
    stats = {"active_queries": None}
    normalize_querytext_active(stats)
    assert stats["active_queries"] is None