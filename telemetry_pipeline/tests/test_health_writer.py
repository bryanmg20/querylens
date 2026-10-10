"""writer: SQL y parametros que llegan a pipeline_health, sin base real.

El recorrido completo contra Postgres (upsert -> resolve -> purge) esta en
test_health_integration.py.
"""
import json
import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from health import writer
from health.report import HealthReport

pytestmark = pytest.mark.unit

STARTED = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class _Result:
    rowcount = 3


class _Conn:
    def __init__(self, log):
        self.log = log

    def execute(self, stmt, params=None):
        self.log.append((str(stmt), params))
        return _Result()


class _Tx:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        if self.engine.error is not None:
            raise self.engine.error
        return _Conn(self.engine.log)

    def __exit__(self, *exc):
        return False


class _Engine:
    def __init__(self, error=None):
        self.log = []
        self.error = error

    def begin(self):
        return _Tx(self)


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    monkeypatch.setattr(writer, "_schema_warned", False)


def _sql(engine, needle):
    return [(sql, params) for sql, params in engine.log if needle in sql]


def test_flush_upserts_last_run_with_status_and_sections():
    report = HealthReport()
    report.section_ok("tables")
    report.section_failed("statements")
    report.add("PG_STATEMENTS_NOT_INSTALLED", section="statements", sqlstate="42P01")
    report.engine_version = "17.2"
    engine = _Engine()

    writer.flush(engine, "db_1", report, STARTED)

    [(_, params)] = _sql(engine, "pipeline_health.last_run")
    assert params == {
        "db_id": "db_1",
        "started_at": STARTED,
        "status": "failed",
        "sections_ok": ["tables"],
        "sections_failed": ["statements"],
        "msg_id": None,
        "engine_version": "17.2",
    }


def test_flush_upserts_each_issue_with_catalog_text():
    report = HealthReport()
    report.add("MISSING_PG_READ_ALL_STATS", rows=4)
    engine = _Engine()

    writer.flush(engine, "db_1", report, STARTED)

    [(sql, rows)] = _sql(engine, "INSERT INTO pipeline_health.issues")
    assert "ON CONFLICT (db_id, code, section) WHERE resolved_at IS NULL" in sql
    [row] = rows
    assert row["code"] == "MISSING_PG_READ_ALL_STATS"
    assert row["severity"] == "degraded"
    assert row["section"] == ""
    assert "pg_read_all_stats" in row["remediation"]
    assert json.loads(row["params"]) == {"rows": 4, "count": 1}


def test_flush_resolves_only_checked_scopes():
    report = HealthReport()
    report.checked("connection", "collect")
    report.add("SECTION_FAILED", section="locks")
    engine = _Engine()

    writer.flush(engine, "db_1", report, STARTED)

    [(sql, params)] = _sql(engine, "SET resolved_at")
    assert "scope = ANY(:scopes)" in sql
    assert params == {"db_id": "db_1", "scopes": ["collect", "connection"], "seen": ["SECTION_FAILED/locks"]}


def test_flush_without_checked_scopes_resolves_nothing():
    engine = _Engine()
    writer.flush(engine, "db_1", HealthReport(), STARTED)
    assert not _sql(engine, "SET resolved_at")
    assert not _sql(engine, "INSERT INTO pipeline_health.issues")


def test_flush_never_raises_on_db_error(caplog):
    engine = _Engine(error=OperationalError("x", {}, Exception("queue down")))
    with caplog.at_level(logging.ERROR, logger="health.writer"):
        writer.flush(engine, "db_1", HealthReport(), STARTED)
    assert any("flush | db_id=db_1" in r.getMessage() for r in caplog.records)


class _PgError(Exception):
    pgcode = "42P01"


def test_missing_schema_warns_once(caplog):
    engine = _Engine(error=ProgrammingError("x", {}, _PgError("relation does not exist")))
    with caplog.at_level(logging.WARNING, logger="health.writer"):
        writer.flush(engine, "db_1", HealthReport(), STARTED)
        writer.flush(engine, "db_2", HealthReport(), STARTED)
        writer.purge(engine, days=15)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "pipeline_health.sql" in warnings[0].getMessage()


def test_record_target_issue_writes_failed_run_and_resolves_only_credentials():
    engine = _Engine()
    writer.record_target_issue(engine, "db_1", "CREDENTIALS_UNREADABLE", exc_type="InvalidToken")

    [(_, last_run)] = _sql(engine, "pipeline_health.last_run")
    assert last_run["status"] == "failed"
    [(_, rows)] = _sql(engine, "INSERT INTO pipeline_health.issues")
    assert rows[0]["code"] == "CREDENTIALS_UNREADABLE"
    [(_, resolve)] = _sql(engine, "SET resolved_at")
    assert resolve["scopes"] == ["credentials"]


def test_purge_deletes_with_retention_days():
    engine = _Engine()
    writer.purge(engine, days=15)
    issues = _sql(engine, "DELETE FROM pipeline_health.issues")
    runs = _sql(engine, "DELETE FROM pipeline_health.last_run")
    assert issues[0][1] == {"days": 15}
    assert runs[0][1] == {"days": 15}


@pytest.mark.parametrize("raw, days", [("", 15), ("30", 30), ("0", 15), ("-1", 15), ("x", 15)])
def test_retention_days_from_env(monkeypatch, raw, days):
    monkeypatch.setenv("HEALTH_RETENTION_DAYS", raw)
    assert writer.retention_days() == days
