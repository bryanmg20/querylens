"""writer: muestra del ciclo en pipeline_health.cycle_samples y abrazos
perdidos (DEADLOCKS_MISSED), sin base real."""
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
        if "public.deadlock_events" in sql:
            if self.engine.missed_error is not None:
                raise self.engine.missed_error
            return _Result([self.engine.missed] if self.engine.missed is not None else [])
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
    def __init__(self, prev=None, samples_error=None, missed=None, missed_error=None):
        self.log = []
        self.prev = prev
        self.samples_error = samples_error
        # fila de MISSED_DEADLOCKS_SELECT: {"counted": ..., "captured": ...}
        self.missed = missed
        self.missed_error = missed_error

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


# ------------------------------------------------------- abrazos perdidos

def _issues(engine):
    rows = [row for _, rows in _sql(engine, "INSERT INTO pipeline_health.issues") for row in rows]
    return {row["code"]: json.loads(row["params"]) for row in rows}


def _resolved_scopes(engine):
    [(_, params)] = _sql(engine, "SET resolved_at = now()")
    return params["scopes"]


def test_more_counted_than_captured_is_an_issue():
    engine = _Engine(prev=_prev(), missed={"counted": 7, "captured": 4})
    writer.flush(engine, "db_1", _report(), STARTED)

    assert _issues(engine)["DEADLOCKS_MISSED"] == {
        "count": 1, "missed": 3, "counted": 7, "captured": 4, "window_s": writer.MISSED_WINDOW_S,
    }
    assert "capture" in _resolved_scopes(engine)
    [(_, last_run)] = _sql(engine, "pipeline_health.last_run")
    assert last_run["status"] == "degraded"


def test_missed_window_ends_before_the_sample_and_lasts_an_hour():
    engine = _Engine(prev=_prev(), missed={"counted": 0, "captured": 0})
    writer.flush(engine, "db_1", _report(), STARTED)

    [(_, params)] = _sql(engine, "public.deadlock_events")
    until = TAKEN - timedelta(seconds=writer.MISSED_GRACE_S)
    assert params == {"db_id": "db_1", "since": until - timedelta(seconds=writer.MISSED_WINDOW_S), "until": until}


@pytest.mark.parametrize("missed", [
    {"counted": 4, "captured": 4},
    # un borde de la ventana: mas eventos que lo contado no es una perdida
    {"counted": 3, "captured": 4},
    # ningun ciclo de la ventana pudo comparar el contador
    {"counted": None, "captured": 0},
])
def test_nothing_missed_is_no_issue_and_resolves_an_open_one(missed):
    engine = _Engine(prev=_prev(), missed=missed)
    writer.flush(engine, "db_1", _report(), STARTED)

    assert "DEADLOCKS_MISSED" not in _issues(engine)
    # capture evaluado: un DEADLOCKS_MISSED abierto se resuelve
    assert "capture" in _resolved_scopes(engine)


def test_failed_comparison_keeps_an_open_issue_open(caplog):
    # sin el GRANT SELECT sobre deadlock_events
    error = ProgrammingError("x", {}, _PgError("permission denied for table deadlock_events", "42501"))
    engine = _Engine(prev=_prev(), missed_error=error)
    report = _report()
    report.checked("collect")
    with caplog.at_level(logging.WARNING, logger="health.writer"):
        writer.flush(engine, "db_1", report, STARTED)

    assert "DEADLOCKS_MISSED" not in _issues(engine)
    assert "capture" not in _resolved_scopes(engine)
    assert any("missed" in record.getMessage() for record in caplog.records)
