"""EngineCountersStage: muestra cruda del contador de abrazos y del log.

La conexion es falsa y sirve las sentencias del stage desde memoria, como en
test_deadlocks_stage.py.
"""
from datetime import datetime, timezone

import pytest

from health.report import HealthReport
from stages.deadlocks import DeadlocksStage
from stages.engine_counters import EngineCountersStage
from stages.pg_log import PgLogTail

pytestmark = pytest.mark.unit

PG_NOW = datetime(2026, 10, 25, 12, 0, 0)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, *exc):
        if exc_type is not None:
            self.conn.rollbacks += 1


class _Conn:
    def __init__(self, handlers):
        # [(fragmento del SQL, funcion(params) -> filas | Exception)]
        self.handlers = handlers
        self.sent = []
        self.rollbacks = 0

    def begin(self):
        return _Tx(self)

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        for fragment, handler in self.handlers:
            if fragment in sql:
                rows = handler(stmt.compile().params)
                if isinstance(rows, Exception):
                    raise rows
                return _Result(rows)
        raise AssertionError(f"SQL inesperado: {sql}")


class _Collector:
    def __init__(self, dialect):
        self.source_dialect = dialect


FILES = [
    {"name": "postgresql-Sun.json", "size": 4096, "modification": PG_NOW},
    {"name": "postgresql-Sat.json", "size": 100, "modification": PG_NOW},
]


def _pg_conn(counter=None, files=FILES):
    counter = counter if counter is not None else [{"deadlocks": 7, "counter_epoch": "2026-10-25 08:00:00+00"}]
    return _Conn([
        ("pg_stat_database", lambda p: counter),
        ("querylens.log_files()", lambda p: files),
        ("querylens.read_log", lambda p: [{"chunk": memoryview(b"")}]),
        ("current_setting('log_error_verbosity')", lambda p: [{
            "log_error_verbosity": "default", "log_min_messages": "warning",
            "log_lock_waits": "on", "deadlock_timeout": "1s",
            "database_name": "ql_demo", "now_utc": PG_NOW,
        }]),
    ])


def _run(dialect, conn, log=None):
    report = HealthReport()
    EngineCountersStage(_Collector(dialect), report).execute({}, conn, log)
    return report


def test_postgres_sample_has_counter_epoch_and_log_files():
    report = _run("postgres", _pg_conn())
    sample = report.cycle_sample
    assert sample["engine_deadlocks"] == 7
    assert sample["counter_epoch"] == "2026-10-25 08:00:00+00"
    assert sample["log_files"] == [
        {"name": "postgresql-Sun.json", "size": 4096},
        {"name": "postgresql-Sat.json", "size": 100},
    ]
    assert sample["taken_at"].tzinfo is timezone.utc
    assert report.issues == {}


def test_listing_is_reused_from_deadlocks_stage():
    """Un solo log_files() por ciclo, como entre deadlocks y auto_explain."""
    conn = _pg_conn()
    log = PgLogTail()
    DeadlocksStage(_Collector("postgres")).execute({}, conn, log)
    _run("postgres", conn, log)
    assert sum("querylens.log_files()" in sql for sql in conn.sent) == 1


def test_counter_failure_keeps_the_log_and_reports_the_issue():
    conn = _pg_conn(counter=RuntimeError("permission denied for view pg_stat_database"))
    report = _run("postgres", conn)
    assert report.cycle_sample["engine_deadlocks"] is None
    assert report.cycle_sample["log_files"] is not None
    assert ("DEADLOCK_COUNTER_UNAVAILABLE", "deadlocks") in report.issues
    assert conn.rollbacks == 1


def test_log_failure_keeps_the_counter():
    conn = _pg_conn(files=RuntimeError("function querylens.log_files() does not exist"))
    report = _run("postgres", conn)
    assert report.cycle_sample["engine_deadlocks"] == 7
    assert report.cycle_sample["log_files"] is None
    # el issue del log lo deja DeadlocksStage
    assert report.issues == {}


def test_mysql_reads_lock_deadlocks_without_log():
    conn = _Conn([("INNODB_METRICS", lambda p: [{"deadlocks": 4, "status": "enabled"}])])
    report = _run("mysql", conn)
    assert report.cycle_sample["engine_deadlocks"] == 4
    assert report.cycle_sample["counter_epoch"] is None
    assert report.cycle_sample["log_files"] is None


def test_mysql_disabled_counter_is_not_a_value():
    """Con el contador deshabilitado no avanza: la diferencia daria cero."""
    conn = _Conn([("INNODB_METRICS", lambda p: [{"deadlocks": 0, "status": "disabled"}])])
    report = _run("mysql", conn)
    assert report.cycle_sample["engine_deadlocks"] is None
    assert ("DEADLOCK_COUNTER_UNAVAILABLE", "deadlocks") in report.issues


def test_unknown_dialect_takes_no_sample():
    report = _run("sqlite", _Conn([]))
    assert report.cycle_sample is None
