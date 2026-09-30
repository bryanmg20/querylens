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
                "query_text": "SELECT * FROM sbtest1 WHERE id = $1",
                "ready_for_explain": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_postgres_sets_search_path_before_explain():
    conn = _FakeConn()
    stats = _stats(schema_name="public")
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent[0] == "SET LOCAL search_path TO public"
    assert "EXPLAIN (GENERIC_PLAN, FORMAT JSON) SELECT * FROM sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 1


def test_postgres_emits_generic_plan_not_plain_explain():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "GENERIC_PLAN" in conn.sent[1]
    assert "EXPLAIN (FORMAT JSON)" not in conn.sent[1]


def test_postgres_keeps_placeholders_untouched():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert "id = $1" in conn.sent[1]


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


def test_postgres_records_generic_explain_source():
    conn = _FakeConn()
    stats = _stats()
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert stats["canonic_explains"][0]["explain_source"] == "generic"


def test_postgres_skips_not_ready_candidates():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": 2,
                "query_text": "SELECT * FROM sbtest1",
                "ready_for_explain": False,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"] == []


def test_postgres_skips_candidates_without_query_id():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": None,
                "query_text": "SELECT 1",
                "ready_for_explain": True,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert conn.sent == []


def test_postgres_skips_multi_statement_query():
    conn = _FakeConn()
    stats = {
        "top_impact_queries": [
            {
                "query_id": 3,
                "query_text": "SELECT 1; SELECT 2",
                "ready_for_explain": True,
                "schema_name": "public",
            },
        ],
    }
    ExplainStage(_FakeCollector()).execute(stats, conn)
    assert [s for s in conn.sent if "EXPLAIN" in s] == []


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


def test_postgres_continues_after_one_failure():
    conn = _FailingConn()
    stats = _stats(schema_name="public")
    stats["top_impact_queries"].append(
        {
            "query_id": 4,
            "query_text": "SELECT * FROM sbtest2 WHERE id = $1",
            "ready_for_explain": True,
            "schema_name": "public",
        }
    )
    ExplainStage(_FakeCollector()).execute(stats, conn)
    explains = [s for s in conn.sent if "EXPLAIN" in s]
    assert len(explains) == 2


class _FakeMysqlCollector:
    source_dialect = "mysql"
    engine = MY_ENGINE


def _mysql_stats(schema_name="ql_demo"):
    return {
        "top_impact_queries": [
            {
                "query_id": 7,
                "query_text": "INSERT INTO sbtest1 (id, k) VALUES (? , ?)",
                "query_sample_text": "insert into sbtest1 (id, k) values (5, 'x')",
                "ready_for_explain": True,
                "schema_name": schema_name,
            },
        ],
    }


def test_mysql_uses_sample_text_not_digest():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert "insert into sbtest1 (id, k) values (5, 'x')" in conn.sent[1]
    assert "VALUES (? , ?)" not in conn.sent[1]


def test_mysql_uses_schema_before_explain():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name="ql_demo")
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent[0] == "USE ql_demo"
    assert "EXPLAIN FORMAT=JSON insert into sbtest1" in conn.sent[1]
    assert stats["canonic_explains"][0]["query_id"] == 7


def test_mysql_records_sample_explain_source():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert stats["canonic_explains"][0]["explain_source"] == "sample"


def test_mysql_skips_use_when_no_schema():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats(schema_name=None)
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert "EXPLAIN FORMAT=JSON insert into sbtest1" in conn.sent[0]


def test_mysql_truncated_sample_is_not_explainable():
    conn = _FakeConn(rows=[{"EXPLAIN": '{"query_block": {"select_id": 1}}'}])
    stats = _mysql_stats()
    stats["top_impact_queries"][0]["ready_for_explain"] = False
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.sent == []
    assert stats["canonic_explains"] == []


def test_mysql_truncated_sample_rolls_back_without_aborting_cycle():
    class _TruncatedConn(_FakeConn):
        def execute(self, stmt):
            self.sent.append(str(stmt))
            if "EXPLAIN FORMAT=JSON" in str(stmt):
                raise RuntimeError("You have an error in your SQL syntax")
            return _FakeResult(self.rows)

    conn = _TruncatedConn()
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert conn.rollback_calls == 1
    assert stats["canonic_explains"] == []


def test_mysql_no_plan_row_is_logged_and_skipped():
    conn = _FakeConn(rows=[])
    stats = _mysql_stats()
    ExplainStage(_FakeMysqlCollector()).execute(stats, conn)
    assert stats["canonic_explains"] == []
