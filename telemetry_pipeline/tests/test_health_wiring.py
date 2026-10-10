"""El report de salud cruza el pipeline: collect, explain, orchestrator,
main.run_engine, registered y la purga del runner."""
import logging
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError, ProgrammingError

import main
import runner as runner_mod
from collectors.postgres.collector import Postgres_Collector
from config import registered
from health import preflight
from health.report import HealthReport
from orchestrator import Orchestrator
from stages.collect import CollectStage
from tests.test_orchestrator import _FakeConn, _FakeEngine

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_preflight_cache():
    preflight.reset_cache()
    yield
    preflight.reset_cache()


class _PgError(Exception):
    def __init__(self, message, pgcode):
        super().__init__(message)
        self.pgcode = pgcode


def _pg_exc(pgcode, cls=ProgrammingError, message="boom"):
    return cls("SELECT 1", {}, _PgError(message, pgcode))


# ---------- collect ----------


def test_collect_failure_lands_in_report_by_section():
    conn = _FakeConn(overrides={"pg_locks": _pg_exc("42501")})
    conn.rollback = lambda: None
    report = HealthReport()
    stage = CollectStage(Postgres_Collector(engine=None), report)

    stage.execute({}, conn)

    assert ("SECTION_PERMISSION_DENIED", "locks") in report.issues
    assert report.sections_failed == ["locks"]
    assert "statements" in report.sections_ok
    assert "collect" in report.scopes


# ---------- orchestrator ----------


def _run(conn, report):
    collector = Postgres_Collector(engine=_FakeEngine(conn))
    return Orchestrator(collector, report).run_pipeline()


def test_healthy_run_checks_every_scope_and_stays_ok():
    report = HealthReport()
    _run(_FakeConn(), report)
    assert {"connection", "preflight", "collect", "explain"} <= report.scopes
    assert report.status() == "ok"


def test_missing_extension_fails_the_run():
    report = HealthReport()
    _run(_FakeConn(overrides={"FROM pg_stat_statements s": _pg_exc("42P01")}), report)
    assert ("PG_STATEMENTS_NOT_INSTALLED", "statements") in report.issues
    assert report.status() == "failed"
    # Sin statements no hay explain: sus issues abiertos no se dan por resueltos.
    assert "explain" not in report.scopes


def test_hidden_text_is_detected_before_redaction():
    report = HealthReport()
    rows = [{"query_text": "<insufficient privilege>", "query_id": None, "userid": 10}]
    stats = _run(_FakeConn(overrides={"FROM pg_stat_statements s": rows}), report)
    assert ("MISSING_PG_READ_ALL_STATS", "") in report.issues
    assert stats is not None


class _BrokenEngine:
    def connect(self):
        raise _pg_exc(None, cls=OperationalError, message="password authentication failed for user")


def test_connection_failure_is_recorded_and_reraised():
    report = HealthReport()
    with pytest.raises(OperationalError):
        Orchestrator(Postgres_Collector(engine=_BrokenEngine()), report).run_pipeline()
    assert ("AUTH_FAILED", "") in report.issues
    assert report.scopes == {"connection"}


# ---------- explain ----------


def test_explain_denied_is_grouped_without_query_ids():
    report = HealthReport()
    _run(_FakeConn(overrides={"EXPLAIN (GENERIC_PLAN": _pg_exc("42501")}), report)
    params = report.issues[("EXPLAIN_PERMISSION_DENIED", "")]
    assert params["count"] == 2
    assert "query_id" not in params
    assert report.status() == "degraded"


def test_no_explainable_candidates_is_info():
    report = HealthReport()
    rows = [{
        "query_id": 1, "query_text": "UPDATE t SET a = $1", "execution_count": 5,
        "rows_returned": 5, "avg_rows_per_call": 1.0, "total_time_ms": 50.0,
        "mean_time_ms": 10.0, "stddev_time_ms": 1.0, "min_time_ms": 1.0,
        "max_time_ms": 20.0, "coeff_of_variation": 0.1, "disk_spill_indicator": 0,
        "userid": 10,
    }]
    # DML: CandidatesStage lo manda a non_explainable_candidates.
    _run(_FakeConn(overrides={"FROM pg_stat_statements s": rows}), report)
    assert ("NO_EXPLAINABLE_CANDIDATES", "") in report.issues
    assert report.status() == "ok"


def test_explained_candidates_do_not_report_no_candidates():
    report = HealthReport()
    _run(_FakeConn(), report)
    assert ("NO_EXPLAINABLE_CANDIDATES", "") not in report.issues


# ---------- main.run_engine ----------


def test_run_engine_flushes_even_when_connection_fails(monkeypatch):
    flushed = []
    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock)
    monkeypatch.setattr(
        main.health_writer, "flush",
        lambda engine, db_id, report, started_at: flushed.append((db_id, report)),
    )

    with pytest.raises(OperationalError):
        main.run_engine("postgres", _BrokenEngine, db_id="db_ab12cd34")

    [(db_id, report)] = flushed
    assert db_id == "db_ab12cd34"
    assert report.status() == "failed"
    assert ("AUTH_FAILED", "") in report.issues
    assert ("PIPELINE_FAILED", "") not in report.issues, "ya tiene su code, no es falla interna"


def test_run_engine_without_db_id_uses_one_identity_per_dialect():
    assert main._health_db_id("postgres", None) != main._health_db_id("mysql", None)
    assert main._health_db_id("mysql", "db_1") == "db_1"


def test_enqueue_failure_is_recorded(monkeypatch):
    flushed = []
    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock)
    monkeypatch.setattr(
        main.health_writer, "flush",
        lambda engine, db_id, report, started_at: flushed.append(report),
    )
    monkeypatch.setattr(main, "send_to_queue", mock.Mock(side_effect=RuntimeError("pgmq down")))

    with pytest.raises(RuntimeError):
        main.run_engine("postgres", lambda: _FakeEngine(_FakeConn()), db_id="db_1")

    [report] = flushed
    assert ("ENQUEUE_FAILED", "") in report.issues
    assert {"credentials", "pipeline"} <= report.scopes


# ---------- registered ----------


def test_unreadable_credentials_are_recorded(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AUTH_ENCRYPTION_KEY", key)
    engine = mock.MagicMock()
    ctx = engine.connect.return_value.__enter__.return_value
    ctx.execute.return_value.mappings.return_value.all.return_value = [{
        "database_identifier": "db_rota",
        "engine": "postgresql",
        "host": "no-es-fernet",
        "port": "x",
        "db_user": "x",
        "encrypted_password": "x",
        "database_name": "app",
    }]
    monkeypatch.setattr(registered, "_get_queue_engine", lambda: engine)
    recorded = []
    monkeypatch.setattr(
        registered.health_writer, "record_target_issue",
        lambda eng, db_id, code, **params: recorded.append((db_id, code)),
    )

    assert registered.load_registered_targets() is None
    assert recorded == [("db_rota", "CREDENTIALS_UNREADABLE")]


# ---------- runner ----------


class _Clock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_runner_purges_on_first_cycle_and_then_by_interval():
    calls = []
    runner = runner_mod.Runner(
        interval=10,
        load=lambda: None,
        clock=_Clock(0, 100, 3600),
        purge=lambda: calls.append("purge"),
        purge_interval=3600,
    )
    for _ in range(3):
        runner.cycle()
    assert calls == ["purge", "purge"]


def test_runner_purge_failure_does_not_stop_the_cycle(caplog):
    loaded = []

    def boom():
        raise RuntimeError("db down")

    runner = runner_mod.Runner(
        interval=10,
        load=lambda: loaded.append(1),
        clock=_Clock(0),
        purge=boom,
        purge_interval=3600,
    )
    with caplog.at_level(logging.ERROR, logger="runner"):
        runner.cycle()
    assert loaded == [1]
    assert any("purge fallo" in r.getMessage() for r in caplog.records)


def test_runner_without_purge_never_touches_the_clock_for_it():
    runner = runner_mod.Runner(interval=10, load=lambda: None, clock=_Clock(0))
    runner.cycle()
    assert runner._last_purge is None


def test_run_engine_flushes_when_target_factory_raises(monkeypatch):
    """El factory del target lanza antes de crear el engine de QueryLens: el
    report igual se vuelca, con el engine singleton de la cola (no se crea ni
    se dispone un pool solo para escribir)."""
    flushed = []
    queue_engine = mock.MagicMock()
    monkeypatch.setattr(main, "_get_queue_engine", lambda: queue_engine)
    monkeypatch.setattr(
        main.health_writer, "flush",
        lambda engine, db_id, report, started_at: flushed.append((engine, db_id, report)),
    )

    def boom():
        raise RuntimeError("motor caido")

    with pytest.raises(RuntimeError):
        main.run_engine("postgres", boom, db_id="db_1")

    [(engine, db_id, report)] = flushed
    assert engine is queue_engine and db_id == "db_1"
    assert ("PIPELINE_FAILED", "") in report.issues
    queue_engine.dispose.assert_not_called()


def test_crash_after_another_blocking_issue_is_still_recorded(monkeypatch):
    """Sin pg_stat_statements la corrida ya es 'failed'; si ademas un stage
    revienta, el crash no puede quedar escondido detras de ese issue."""
    flushed = []
    monkeypatch.setattr(
        main.health_writer, "flush",
        lambda engine, db_id, report, started_at: flushed.append(report),
    )
    conn = _FakeConn(overrides={"FROM pg_stat_statements s": _pg_exc("42P01")})
    import orchestrator as orchestrator_mod
    monkeypatch.setattr(
        orchestrator_mod, "redact_active_queries",
        mock.Mock(side_effect=RuntimeError("bug interno")),
    )

    with pytest.raises(RuntimeError, match="bug interno"):
        main.run_engine("postgres", lambda: _FakeEngine(conn), db_id="db_1")

    [report] = flushed
    assert ("PG_STATEMENTS_NOT_INSTALLED", "statements") in report.issues
    assert ("PIPELINE_FAILED", "") in report.issues
