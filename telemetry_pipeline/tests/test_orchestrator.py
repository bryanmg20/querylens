import pytest
from sqlalchemy.dialects import postgresql

from collectors.postgres.collector import Postgres_Collector
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator

pytestmark = pytest.mark.unit


_STATEMENTS = [
    {
        "query_id": 111,
        "query_text": "SELECT c FROM sbtest1 WHERE id = $1",
        "execution_count": 40,
        "rows_returned": 40,
        "avg_rows_per_call": 1.0,
        "total_time_ms": 120.0,
        "mean_time_ms": 3.0,
        "stddev_time_ms": 0.4,
        "min_time_ms": 2.0,
        "max_time_ms": 9.0,
        "coeff_of_variation": 0.13,
        "disk_spill_indicator": 0,
        "userid": 10,
    },
    {
        "query_id": 222,
        "query_text": "UPDATE sbtest1 SET k = k + 1 WHERE id = $1",
        "execution_count": 12,
        "rows_returned": 12,
        "avg_rows_per_call": 1.0,
        "total_time_ms": 90.0,
        "mean_time_ms": 7.5,
        "stddev_time_ms": 1.1,
        "min_time_ms": 5.0,
        "max_time_ms": 14.0,
        "coeff_of_variation": 0.15,
        "disk_spill_indicator": 3,
        "userid": 10,
    },
]

_EXPLAIN_PLAN = (
    '[{"Plan": {"Node Type": "Index Scan", "Index Name": "sbtest1_pkey", '
    '"Relation Name": "sbtest1", "Alias": "sbtest1", "Plan Rows": 1, '
    '"Total Cost": 8.3, "Index Cond": "(id = $1)", "Plans": []}}]'
)

_SCHEMA_RESOLVER = [
    {"user_id": 10, "username": "ql_user", "resolved_schema": "public"},
]

_ROWS_BY_SQL = {
    "pg_stat_statements": _STATEMENTS,
    "pg_stat_user_indexes": [
        {
            "schema_name": "public",
            "table_name": "sbtest1",
            "index_name": "sbtest1_pkey",
            "index_scans": 40,
            "last_index_scan": None,
            "index_ref": "CREATE INDEX sbtest1_pkey ON public.sbtest1 USING btree (id)",
            "index_size_bytes": 16384,
        }
    ],
    "pg_stat_user_tables": [
        {
            "schema_name": "public",
            "table_name": "sbtest1",
            "seq_scans": 1,
            "idx_scans": 40,
            "live_rows": 10000,
        }
    ],
    "pg_locks": [
        {
            "process_id": 42,
            "table_name": "sbtest1",
            "lock_mode": "AccessShareLock",
            "is_granted": True,
        }
    ],
    "pg_stat_activity": [
        {
            "process_id": 42,
            "query_text": "SELECT c FROM sbtest1 WHERE id = 7",
            "query_id": 111,
            "transaction_start_time": "2026-01-01 12:00:00.000000",
            "blocking_pids": "",
        }
    ],
    "pg_postmaster_start_time": [{"stats_reset": "2026-01-01 09:00:00.000000"}],
    "information_schema.columns": [
        {
            "schema_name": "public",
            "table_name": "sbtest1",
            "column_name": "id",
            "data_type": "integer",
        }
    ],
    "resolved_schema": _SCHEMA_RESOLVER,
    "EXPLAIN (GENERIC_PLAN, FORMAT JSON)": [{"QUERY PLAN": _EXPLAIN_PLAN}],
}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, overrides=None):
        self.sent = []
        self.rollback_calls = 0
        self.closed = False
        self._overrides = overrides or {}

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        for needle, rows in self._overrides.items():
            if needle in sql:
                if isinstance(rows, Exception):
                    raise rows
                return _FakeResult(rows)
        for needle, rows in _ROWS_BY_SQL.items():
            if needle in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def rollback(self):
        self.rollback_calls += 1


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn
        self.connect_calls = 0
        self.dialect = postgresql.dialect()

    def connect(self):
        self.connect_calls += 1
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        self._conn.closed = True


def _run(conn):
    engine = _FakeEngine(conn)
    collector = Postgres_Collector(engine=engine)
    stats = Orchestrator(collector).run_pipeline()
    return stats, collector, engine


def test_run_pipeline_populates_every_collected_section():
    stats, _, _ = _run(_FakeConn())
    for key in (
        "statements",
        "indexes",
        "tables",
        "locks",
        "active_queries",
        "stats_reset_timestamp",
        "columns",
    ):
        assert stats[key], f"{key} deberia traer filas del fake"


def test_run_pipeline_selects_and_explains_candidates():
    stats, _, _ = _run(_FakeConn())
    assert [c["query_id"] for c in stats["top_impact_queries"]] == [111, 222]
    assert all(c["ready_for_explain"] is True for c in stats["top_impact_queries"])
    assert {e["query_id"] for e in stats["canonic_explains"]} == {111, 222}


def test_run_pipeline_resolves_schema_and_strips_userid():
    stats, _, _ = _run(_FakeConn())
    assert stats["top_impact_queries"][0]["schema_name"] == "public"
    assert all("userid" not in c for c in stats["top_impact_queries"])
    assert "schema_resolver" not in stats


def test_run_pipeline_drops_every_transient_key():
    stats, _, _ = _run(_FakeConn())
    for transient in (
        "high_impact_statements",
        "unstable_statements",
        "disk_spill_statements",
        "query_explain",
        "schema_resolver",
    ):
        assert transient not in stats


def test_run_pipeline_result_validates_against_snapshot_contract():
    stats, _, _ = _run(_FakeConn())
    payload = SnapshotPayload.from_snapshot(stats)
    assert payload.db_id == "querylens-db-01"
    assert len(payload.statements) == 2
    assert len(payload.canonic_explains) == 2


def test_statements_failure_skips_candidates_explain_and_normalize():
    conn = _FakeConn({"pg_stat_statements": RuntimeError("relation does not exist")})
    stats, _, _ = _run(conn)

    assert stats["statements"] is None
    assert "top_impact_queries" not in stats
    assert "non_explainable_candidates" not in stats
    assert "canonic_explains" not in stats
    assert conn.rollback_calls == 1


def test_statements_failure_still_enriches_with_db_id():
    conn = _FakeConn({"pg_stat_statements": RuntimeError("boom")})
    stats, _, _ = _run(conn)
    assert stats["db_id"] == "querylens-db-01"
    assert stats["indexes"], "las demas secciones siguen collected"


def test_empty_statements_list_is_not_treated_as_missing():
    stats, _, _ = _run(_FakeConn({"pg_stat_statements": []}))
    assert stats["statements"] == []
    assert stats["top_impact_queries"] == []
    assert stats["canonic_explains"] == []


def test_non_explainable_statements_land_in_non_explainable_candidates():
    conn = _FakeConn({
        "pg_stat_statements": [
            {
                "query_id": 999,
                "query_text": "SET application_name = 'x'",
                "execution_count": 1,
                "rows_returned": 0,
                "avg_rows_per_call": 0.0,
                "total_time_ms": 1.0,
                "mean_time_ms": 1.0,
                "stddev_time_ms": None,
                "min_time_ms": 1.0,
                "max_time_ms": 1.0,
                "coeff_of_variation": None,
                "disk_spill_indicator": 0,
                "userid": 10,
            }
        ]
    })
    stats, _, _ = _run(conn)
    assert stats["top_impact_queries"] == []
    assert [c["query_id"] for c in stats["non_explainable_candidates"]] == [999]
    assert stats["canonic_explains"] == []


def test_exposes_collector_stats_for_downstream_consumers():
    _, collector, _ = _run(_FakeConn())
    assert collector.stats is not None
    assert collector.stats["db_id"] == "querylens-db-01"
    assert "top_impact_queries" in collector.stats


def test_opens_a_single_connection_and_closes_it():
    conn = _FakeConn()
    _, _, engine = _run(conn)
    assert engine.connect_calls == 1
    assert conn.closed is True


def test_reuses_one_connection_for_every_stage():
    conn = _FakeConn()
    _run(conn)
    explain_calls = [s for s in conn.sent if s.startswith("EXPLAIN")]
    set_calls = [s for s in conn.sent if s.startswith("SET LOCAL search_path")]
    assert len(explain_calls) == 2
    assert len(set_calls) == 2
