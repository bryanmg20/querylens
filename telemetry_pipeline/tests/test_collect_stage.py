import pytest

import stages.collect as collect_module
from collectors.postgres.collector import Postgres_Collector
from stages.collect import CollectStage

pytestmark = pytest.mark.unit


class _RecordingLogger:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows_for=None, fail_on=None):
        self.sent = []
        self.rollback_calls = 0
        self._rows_for = rows_for or {}
        self._fail_on = fail_on or {}

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        for needle in self._fail_on:
            if needle in sql:
                raise RuntimeError(f"boom on {needle}")
        for needle, rows in self._rows_for.items():
            if needle in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def rollback(self):
        self.rollback_calls += 1


def _stage(conn=None, collector=None):
    collector = collector or Postgres_Collector(engine=None)
    return CollectStage(collector), conn or _FakeConn()


def test_populates_every_key_declared_by_the_collector():
    stage, conn = _stage()
    stats = stage.execute({}, conn)
    assert set(stats) == set(Postgres_Collector(engine=None).queries)
    assert len(conn.sent) == len(Postgres_Collector(engine=None).queries)


def test_rows_are_converted_to_plain_dicts():
    stage, conn = _stage(_FakeConn({"pg_stat_user_tables": [{"table_name": "sbtest1"}]}))
    stats = stage.execute({}, conn)
    assert stats["tables"] == [{"table_name": "sbtest1"}]
    assert type(stats["tables"][0]) is dict


def test_failing_query_yields_none_and_rolls_back():
    stage, conn = _stage(_FakeConn(fail_on=["FROM pg_locks"]))
    stats = stage.execute({}, conn)
    assert stats["locks"] is None
    assert conn.rollback_calls == 1


def test_one_failing_query_does_not_stop_the_others():
    stage, conn = _stage(_FakeConn(
        rows_for={"pg_stat_user_tables": [{"table_name": "sbtest1"}]},
        fail_on=["FROM pg_locks"],
    ))
    stats = stage.execute({}, conn)
    assert stats["locks"] is None
    assert stats["tables"] == [{"table_name": "sbtest1"}]
    assert stats["columns"] == []


def test_stats_is_mutated_in_place_and_returned():
    stage, conn = _stage()
    stats = {"preexisting": "kept"}
    result = stage.execute(stats, conn)
    assert result is stats
    assert stats["preexisting"] == "kept"


def test_failure_is_logged_with_dialect_and_query_key(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(collect_module, "logger", recorder)
    stage, conn = _stage(_FakeConn(fail_on=["FROM pg_stat_statements"]))
    stage.execute({}, conn)
    assert len(recorder.errors) == 1
    assert recorder.errors[0].startswith("postgres | collect_telemetry | query=statements |")
    assert "boom" in recorder.errors[0]


def test_success_is_not_logged_as_error(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(collect_module, "logger", recorder)
    stage, conn = _stage()
    stage.execute({}, conn)
    assert recorder.errors == []


def test_mysql_collector_keys_are_collected_too():
    from collectors.mysql.collector import Mysql_Collector

    collector = Mysql_Collector(engine=None)
    stage, conn = _stage(collector=collector)
    stats = stage.execute({}, conn)
    assert "schema_resolver" not in stats
    assert stats["statements"] == []
