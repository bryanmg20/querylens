import pytest
from sqlalchemy import create_engine

from stages.explain import ExplainStage

pytestmark = pytest.mark.unit

PG_ENGINE = create_engine("postgresql+psycopg2://ql_user:ql_pass@localhost:5432/ql_demo")
MY_ENGINE = create_engine("mysql+pymysql://ql_demo:ql_pass@localhost:3307/ql_demo")


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows=None):
        self.sent = []
        self.rollback_calls = 0
        self.rows = rows or [
            {"QUERY PLAN": '[{"Plan": {"Node Type": "Seq Scan", "Total Cost": 1.0}}]'},
        ]

    def execute(self, stmt):
        self.sent.append(str(stmt))
        return _FakeResult(self.rows)

    def rollback(self):
        self.rollback_calls += 1


class _FakeCollector:
    source_dialect = "postgres"
    engine = PG_ENGINE


def _stats(schema_name="public"):
    return {
        "top_impact_queries": [
            {
                "query_id": 1,
                "query_text": "SELECT * FROM sbtest1",
                "real_query_found": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_postgres_sets_search_path_before_explain():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO public"
    assert "EXPLAIN (FORMAT JSON) SELECT * FROM sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 1


def test_postgres_quotes_schema_identifier():
    conn = _FakeConn()
    stats = _stats(schema_name="app stats")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == 'SET LOCAL search_path TO "app stats"'


def test_postgres_resets_search_path_when_no_schema():
    conn = _FakeConn()
    stats = _stats(schema_name=None)
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO DEFAULT"


def test_postgres_skips_non_real_candidates():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": 2,
                "query_text": "SELECT * FROM sbtest1",
                "real_query_found": False,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"] == []


class _FailingConn(_FakeConn):
    def execute(self, stmt):
        self.sent.append(str(stmt))
        if "EXPLAIN" in str(stmt):
            raise RuntimeError("explain boom")
        return _FakeResult(self.rows)


def test_postgres_explain_error_rolls_back_and_skips():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert stats["canonic_explains"] == []


class _FakeMysqlCollector:
    source_dialect = "mysql"
    engine = MY_ENGINE


def _mysql_stats(schema_name="ql_demo"):
    return {
        "top_impact_queries": [
            {
                "query_id": 7,
                "query_text": "INSERT INTO sbtest1 (id, k) VALUES (? , ?)",
                "real_query_found": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_mysql_uses_schema_before_explain():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name="ql_demo")
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE ql_demo"
    assert "EXPLAIN FORMAT=JSON INSERT INTO sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 7


def test_mysql_skips_use_when_no_schema():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=None)
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert "EXPLAIN FORMAT=JSON INSERT INTO sbtest1" in conn.sent[0]