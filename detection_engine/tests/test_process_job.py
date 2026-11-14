"""process_job: guarda los hallazgos ordenados por impacto (#51), descarta
los de una regla a la que le falto una fuente y anota el estado de evaluacion
de la ventana (#52). Sin base real: el guardado se parchea."""
from datetime import datetime, timezone
from unittest import mock

import pytest

import main
from models import Hallazgo

ENQUEUED = datetime(2026, 11, 12, 15, 0, 7, tzinfo=timezone.utc)


def _finding(antipatron, impacto_ms, severidad="medium"):
    return Hallazgo(
        antipatron=antipatron, severidad=severidad, evidencia={"canonic_query": f"SELECT {antipatron}"},
        explicacion="e", recomendacion="r", riesgo="x", impacto_ms=impacto_ms,
    )


def _job(**collection_status):
    message = {
        "db_id": "db-1", "statements": [], "locks": [], "active_queries": [], "indexes": [],
        "tables": [], "columns": [], "server_start_timestamp": [],
    }
    if collection_status:
        message["collection_status"] = collection_status
    return {"msg_id": 7, "message": message, "enqueued_at": ENQUEUED}


@pytest.fixture
def saved(monkeypatch):
    calls = {}
    monkeypatch.setattr(main, "load_statement_history", lambda engine, db_id: {})
    monkeypatch.setattr(main, "save_statement_histories", mock.MagicMock())
    monkeypatch.setattr(main, "run_contention", lambda engine, snapshot, fallback_captured_at: [
        _finding("long_transaction_cascade", 20_000.0),
    ])
    monkeypatch.setattr(main, "detect_all", lambda snapshot, history, captured_at: [
        _finding("unused_index", 40.0, "low"),
        _finding("missing_index", 300.0),
        _finding("disk_spill", 300.0, "high"),
    ])
    monkeypatch.setattr(main, "save_findings", lambda engine, db_id, findings: calls.update(findings=findings))
    monkeypatch.setattr(
        main, "save_evaluation",
        lambda engine, db_id, captured_at, states: calls.update(evaluation=(db_id, captured_at, states)),
    )
    return calls


def test_findings_are_saved_in_impact_order(saved):
    returned = main.process_job(mock.MagicMock(), _job())
    order = ["long_transaction_cascade", "disk_spill", "missing_index", "unused_index"]
    assert [f.antipatron for f in saved["findings"]] == order
    assert [f.antipatron for f in returned] == order


def test_evaluation_state_is_saved_for_the_window(saved):
    main.process_job(mock.MagicMock(), _job())
    db_id, captured_at, states = saved["evaluation"]
    assert (db_id, captured_at, len(states)) == ("db-1", ENQUEUED, 13)


def test_findings_of_a_rule_without_its_source_are_not_saved(saved):
    job = _job(status="degraded", sections_failed=["indexes"], issues=[{
        "code": "SECTION_FAILED", "severity": "degraded", "section": "indexes",
        "message": "m", "remediation": "r",
    }])
    main.process_job(mock.MagicMock(), job)
    # con la seccion de indices sin leer, AP-04 y AP-05 no opinan en esta ventana
    assert [f.antipatron for f in saved["findings"]] == ["long_transaction_cascade", "disk_spill"]


def test_a_failure_in_the_evaluation_does_not_lose_the_findings(saved, monkeypatch):
    monkeypatch.setattr(main, "evaluate_pathologies", mock.MagicMock(side_effect=RuntimeError("x")))
    main.process_job(mock.MagicMock(), _job())
    assert len(saved["findings"]) == 4
    assert "evaluation" not in saved


def test_a_failure_saving_the_evaluation_does_not_fail_the_job(saved, monkeypatch):
    monkeypatch.setattr(main, "save_evaluation", mock.MagicMock(side_effect=RuntimeError("x")))
    assert len(main.process_job(mock.MagicMock(), _job())) == 4
