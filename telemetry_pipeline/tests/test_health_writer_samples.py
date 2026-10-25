"""writer: muestra del ciclo en pipeline_health.cycle_samples, sin base real."""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import ProgrammingError

from health import writer
from health.report import HealthReport
from stages.pg_log import LOG_WINDOW_BYTES

pytestmark = pytest.mark.unit

STARTED = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
TAKEN = STARTED + timedelta(seconds=1)


class _PgError(Exception):
    def __init__(self, msg, pgcode="42P01"):
        super().__init__(msg)
        self.pgcode = pgcode


class _Rows(list):
    def first(self):
        return self[0] if self else None


class _Result:
    rowcount = 0

    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return _Rows(self._rows)


class _Conn:
    def __init__(self, engine):
        self.engine = engine

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.engine.log.append((sql, params))
        if "cycle_samples" in sql and self.engine.samples_error is not None:
            raise self.engine.samples_error
        if "FROM pipeline_health.cycle_samples" in sql and "SELECT" in sql:
            return _Result([self.engine.prev] if self.engine.prev is not None else [])
        return _Result()


class _Tx:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return _Conn(self.engine)

    def __exit__(self, *exc):
        return False


class _Engine:
    def __init__(self, prev=None, samples_error=None):
        self.log = []
        self.prev = prev
        self.samples_error = samples_error

    def begin(self):
        return _Tx(self)


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    monkeypatch.setattr(writer, "_schema_warned", False)


def _sql(engine, needle):
    return [(sql, params) for sql, params in engine.log if needle in sql]


def _report(deadlocks=12, files=None):
    report = HealthReport()
    report.cycle_sample = {
        "taken_at": TAKEN,
        "engine_deadlocks": deadlocks,
        "counter_epoch": "r|s",
        "log_files": files if files is not None else [{"name": "a.json", "size": 3000}],
    }
    return report


def _prev(deadlocks=10, size=1000):
    return {
        "taken_at": TAKEN - timedelta(seconds=10),
        "engine_deadlocks": deadlocks,
        "counter_epoch": "r|s",
        # psycopg2 devuelve el JSONB ya decodificado
        "log_files": [{"name": "a.json", "size": size}],
    }


def test_sample_is_inserted_with_its_differences():
    engine = _Engine(prev=_prev())
    writer.flush(engine, "db_1", _report(), STARTED)

    [(_, select)] = _sql(engine, "ORDER BY taken_at DESC")
    assert select == {"db_id": "db_1", "taken_at": TAKEN}
    [(_, row)] = _sql(engine, "INSERT INTO pipeline_health.cycle_samples")
    assert row == {
        "db_id": "db_1",
        "taken_at": TAKEN,
        "engine_deadlocks": 12,
        "counter_epoch": "r|s",
        "log_files": json.dumps([{"name": "a.json", "size": 3000}]),
        "deadlocks_since_prev": 2,
        "counter_reset": False,
        "log_bytes_since_prev": 2000,
        "seconds_since_prev": 10.0,
        "log_window_overrun": False,
    }


def test_first_sample_is_inserted_without_differences():
    engine = _Engine(prev=None)
    writer.flush(engine, "db_1", _report(), STARTED)
    [(_, row)] = _sql(engine, "INSERT INTO pipeline_health.cycle_samples")
    assert row["deadlocks_since_prev"] is None
    assert row["log_window_overrun"] is None


def test_overrun_becomes_an_issue_of_the_same_flush():
    engine = _Engine(prev=_prev(size=0))
    report = _report(files=[{"name": "a.json", "size": LOG_WINDOW_BYTES + 10}])
    writer.flush(engine, "db_1", report, STARTED)

    [(_, rows)] = _sql(engine, "INSERT INTO pipeline_health.issues")
    [issue] = rows
    assert issue["code"] == "LOG_WINDOW_OVERRUN"
    assert issue["section"] == "deadlocks"
    assert json.loads(issue["params"]) == {
        "count": 1, "log_bytes": LOG_WINDOW_BYTES + 10, "seconds": 10.0, "window_bytes": LOG_WINDOW_BYTES,
    }
    [(_, last_run)] = _sql(engine, "pipeline_health.last_run")
    assert last_run["status"] == "degraded"


def test_without_sample_nothing_is_read_or_written():
    engine = _Engine()
    writer.flush(engine, "db_1", HealthReport(), STARTED)
    assert _sql(engine, "cycle_samples") == []


def test_missing_samples_table_does_not_block_last_run(caplog):
    error = ProgrammingError("x", {}, _PgError('relation "pipeline_health.cycle_samples" does not exist'))
    engine = _Engine(samples_error=error)
    with caplog.at_level(logging.WARNING, logger="health.writer"):
        writer.flush(engine, "db_1", _report(), STARTED)
        writer.flush(engine, "db_1", _report(), STARTED)

    assert len(_sql(engine, "pipeline_health.last_run")) == 2
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "pipeline_health.sql" in warnings[0].getMessage()


def test_purge_removes_old_samples_apart_from_issues():
    error = ProgrammingError("x", {}, _PgError('relation "pipeline_health.cycle_samples" does not exist'))
    engine = _Engine(samples_error=error)
    writer.purge(engine, days=15)
    # issues y last_run se purgan aunque la tabla de muestras falte
    assert _sql(engine, "DELETE FROM pipeline_health.issues")
    assert _sql(engine, "DELETE FROM pipeline_health.last_run")
    [(_, params)] = _sql(engine, "DELETE FROM pipeline_health.cycle_samples")
    assert params == {"days": 15}
