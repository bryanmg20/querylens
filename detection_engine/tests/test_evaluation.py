"""Estado de evaluacion por patologia (SegundoInforme 6.1, 6.3 y Tabla 6,
issue #52): distingue "sin hallazgos" de "no evaluada" y dice por que.

Los snapshots son minimos y se validan con el contrato. collection_status es
lo que arma el pipeline con su HealthReport (HealthReport.snapshot_status).
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest
from querylens_contract import SnapshotPayload

from catalog import CODES
from evaluation import (
    COLLECTION_CODES,
    EVALUADA,
    NO_EVALUADA,
    ORIGEN_DETECTOR,
    ORIGEN_RECOLECCION,
    PARCIAL,
    drop_unreliable_findings,
    evaluate_pathologies,
)
from models import Hallazgo, StatementHistory
from storage import EVALUATION_RETENTION, evaluation_bucket, save_evaluation

NOW = datetime(2026, 11, 12, 15, 0, 7, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "telemetry_pipeline" / "tests" / "golden"


def _pipeline_catalog() -> dict:
    # el catalogo del pipeline sin importar el paquete health (que trae SQLAlchemy y el logger)
    spec = importlib.util.spec_from_file_location("pipeline_catalog", REPO / "telemetry_pipeline/health/catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CATALOG


CATALOG = _pipeline_catalog()


def _issue(code, section=""):
    definition = CATALOG[code]
    return {
        "code": code, "severity": definition.severity, "section": section,
        "message": definition.message, "remediation": definition.remediation,
    }


def _status(*issues, failed=()):
    severities = {issue["severity"] for issue in issues}
    status = "failed" if "blocking" in severities else "degraded" if "degraded" in severities else "ok"
    return {"status": status, "sections_failed": list(failed), "issues": list(issues)}


SIMPLE = "SELECT id FROM orders WHERE note = $1"
GROUPED = "SELECT note, count(*) FROM orders GROUP BY note"


def _candidate(query_id, canonic_query=SIMPLE):
    return {
        "query_id": query_id, "query_text": canonic_query, "canonic_query": canonic_query,
        "schema_name": "public", "execution_count": 50, "rows_returned": 50, "total_time_ms": 100.0,
    }


def _explain(query_id, actual_rows=None):
    return {
        "query_id": query_id, "explain_source": "auto_explain" if actual_rows is not None else "generic",
        "canonical_plan": {"estimates": {"output_rows": 10, "actual_rows": actual_rows}},
    }


FK = {
    "schema_name": "public", "table_name": "orders", "column_name": "customer_id",
    "referenced_schema_name": "public", "referenced_table_name": "customers",
    "referenced_column_name": "id", "constraint_name": "orders_customer_fk", "position": 1,
}


def _snapshot(candidates=("q1",), explains=None, status="ok", foreign_keys=(FK,), server_start="2026-10-01 00:00:00",
              **extra):
    statements = [_candidate(query_id) if isinstance(query_id, str) else query_id for query_id in candidates]
    explains = [_explain(c["query_id"], actual_rows=40) for c in statements] if explains is None else explains
    payload = {
        "db_id": "db-1", "statements": statements, "top_impact_queries": statements,
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": server_start}],
        "foreign_keys": list(foreign_keys), "canonic_explains": explains, **extra,
    }
    if status is not None:
        payload["collection_status"] = _status() if status == "ok" else status
    return SnapshotPayload.model_validate(payload)


def _with_baseline():
    # una consulta con sus 60 ventanas anteriores: AP-01 tiene contra que comparar
    return {"q1": StatementHistory(
        query_id="q1", captured_at=NOW - timedelta(seconds=10), execution_count=10, total_time_ms=1.0,
        window_means_ms=[1.0] * 60,
    )}


def _states(snapshot, history=None):
    return {state.codigo: state for state in evaluate_pathologies(snapshot, history if history is not None else _with_baseline(), NOW)}


def _codes(state):
    return [reason.codigo for reason in state.motivos]


# -------------------------------------------------------------- forma


def test_one_state_per_pathology_of_the_catalog_in_order():
    states = evaluate_pathologies(_snapshot(), _with_baseline(), NOW)
    assert [state.codigo for state in states] == list(CODES.values())
    assert [state.antipatron for state in states] == list(CODES)


def test_everything_available_is_evaluated():
    states = _states(_snapshot())
    assert {code: state.estado for code, state in states.items()} == {code: EVALUADA for code in CODES.values()}
    assert all(state.motivos == [] and state.firma == "" for state in states.values())
    assert not any(state.descartar_hallazgos for state in states.values())


def test_same_snapshot_gives_the_same_states():
    snapshot = _snapshot(
        candidates=("q1", "q2"), explains=[_explain("q1")],
        status=_status(_issue("EXPLAIN_FAILED"), _issue("DEADLOCK_REPORTS_UNAVAILABLE", "deadlocks")),
    )
    first = [(s.codigo, s.estado, s.firma, s.detalle) for s in evaluate_pathologies(snapshot, {}, NOW)]
    second = [(s.codigo, s.estado, s.firma, s.detalle) for s in evaluate_pathologies(snapshot, {}, NOW)]
    assert first == second


def test_collection_codes_exist_in_the_pipeline_catalog():
    # si el pipeline renombra un codigo, el detector dejaria de reconocerlo en silencio
    assert set(COLLECTION_CODES) <= set(CATALOG)


# ----------------------------------------------------- condiciones del detector


def test_ap01_without_a_complete_baseline_is_not_evaluated():
    state = _states(_snapshot(), history={})["AP-01"]
    assert (state.estado, _codes(state)) == (NO_EVALUADA, ["LINEA_BASE_EN_CONSTRUCCION"])
    assert state.motivos[0].origen == ORIGEN_DETECTOR
    # no es una falta de fuente: lo que detecte no se descarta
    assert not state.descartar_hallazgos
    assert _states(_snapshot())["AP-01"].estado == EVALUADA


def test_ap06_without_foreign_keys_is_not_evaluated():
    state = _states(_snapshot(foreign_keys=()))["AP-06"]
    assert (state.estado, _codes(state)) == (NO_EVALUADA, ["SIN_CLAVES_FORANEAS"])
    assert state.detalle == {"claves_foraneas": 0}


def test_ap05_with_a_young_statistics_window_is_partial():
    young = (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat(sep=" ")
    state = _states(_snapshot(server_start=young))["AP-05"]
    assert (state.estado, _codes(state)) == (PARCIAL, ["VENTANA_ESTADISTICAS_CORTA"])


def test_full_scan_rules_without_any_plan_are_not_evaluated():
    states = _states(_snapshot(explains=[]))
    for code in ("AP-02", "AP-03", "AP-04"):
        assert (states[code].estado, _codes(states[code])) == (NO_EVALUADA, ["SIN_PLAN"]), code
        assert states[code].detalle == {"candidatas": 1, "sin_plan": 1}


def test_full_scan_rules_with_some_plans_missing_are_partial_and_say_why():
    snapshot = _snapshot(
        candidates=("q1", "q2", "q3"), explains=[_explain("q1", 10)],
        status=_status(_issue("EXPLAIN_PERMISSION_DENIED")),
    )
    state = _states(snapshot)["AP-04"]
    assert (state.estado, _codes(state)) == (PARCIAL, ["EXPLAIN_PERMISSION_DENIED"])
    assert state.motivos[0].origen == ORIGEN_RECOLECCION
    assert state.motivos[0].accion == CATALOG["EXPLAIN_PERMISSION_DENIED"].remediation
    assert state.detalle == {"candidatas": 3, "sin_plan": 2}


def test_queries_outside_the_rule_by_design_do_not_count_as_not_evaluated():
    # un GROUP BY no es un SELECT simple: AP-02/03/04 no lo evaluan por diseno (Tabla 6)
    snapshot = _snapshot(candidates=(_candidate("q1", GROUPED),), explains=[])
    states = _states(snapshot)
    for code in ("AP-02", "AP-03", "AP-04", "AP-08"):
        assert states[code].estado == EVALUADA, code
    assert states["AP-02"].detalle == {"candidatas": 0, "sin_plan": 0}


def test_ap08_without_rows_of_a_real_execution():
    no_rows = _snapshot(explains=[_explain("q1", actual_rows=None)])
    state = _states(no_rows)["AP-08"]
    assert (state.estado, _codes(state)) == (NO_EVALUADA, ["SIN_FILAS_REALES"])

    # con el log de auto_explain no disponible, el motivo es ese y trae su accion
    no_log = _snapshot(explains=[_explain("q1", actual_rows=None)], status=_status(_issue("AUTO_EXPLAIN_LOG_UNAVAILABLE")))
    state = _states(no_log)["AP-08"]
    assert _codes(state) == ["AUTO_EXPLAIN_LOG_UNAVAILABLE"]
    assert "auto_explain" in state.motivos[0].accion

    # una de dos con filas reales: parcial
    mixed = _snapshot(candidates=("q1", "q2"), explains=[_explain("q1", 40), _explain("q2", None)])
    state = _states(mixed)["AP-08"]
    assert state.estado == PARCIAL
    assert state.detalle == {"candidatas": 2, "sin_plan": 0, "sin_filas_reales": 1}


# --------------------------------------------------- estado de la recoleccion


def test_failed_statements_section_blinds_every_antipattern_and_drops_their_findings():
    status = _status(_issue("PG_STATEMENTS_NOT_INSTALLED", "statements"), failed=("statements",))
    snapshot = _snapshot(candidates=(), explains=[], status=status)
    states = _states(snapshot)
    for code in ("AP-01", "AP-02", "AP-03", "AP-04", "AP-05", "AP-06", "AP-07", "AP-08"):
        assert states[code].estado == NO_EVALUADA, code
        assert "PG_STATEMENTS_NOT_INSTALLED" in _codes(states[code]), code
        assert states[code].descartar_hallazgos, code
    # la contencion no depende de las sentencias
    assert all(states[code].estado == EVALUADA for code in ("CT-01", "CT-02", "CT-03", "CT-04", "CT-05"))


def test_failed_section_without_its_own_issue_still_says_which_section():
    status = _status(failed=("indexes",))
    state = _states(_snapshot(status=status))["AP-04"]
    assert (state.estado, _codes(state), state.motivos[0].seccion) == (NO_EVALUADA, ["SECCION_NO_LEIDA"], "indexes")
    assert state.firma == "SECCION_NO_LEIDA@indexes"


def test_failed_indexes_section_drops_ap03_ap04_ap05_and_ct04():
    status = _status(_issue("SECTION_PERMISSION_DENIED", "indexes"), failed=("indexes",))
    states = _states(_snapshot(status=status))
    dropping = {code for code, state in states.items() if state.descartar_hallazgos}
    assert dropping == {"AP-03", "AP-04", "AP-05", "CT-04"}
    # AP-02 no lee los indices
    assert states["AP-02"].estado == EVALUADA


def test_failed_columns_section_leaves_ap04_partial_and_keeps_its_findings():
    # AP-04 lee el tipo de las columnas solo para descartar una conversion
    # implicita: sin el detecta igual (tipo_de_columna_desconocido), con menos certeza
    status = _status(_issue("SECTION_PERMISSION_DENIED", "columns"), failed=("columns",))
    states = _states(_snapshot(status=status))
    ap04 = states["AP-04"]
    assert (ap04.estado, _codes(ap04), ap04.motivos[0].seccion) == (
        PARCIAL, ["SECTION_PERMISSION_DENIED"], "columns",
    )
    assert not ap04.descartar_hallazgos
    # las demas de escaneo completo no leen las columnas
    assert states["AP-02"].estado == states["AP-03"].estado == EVALUADA


def test_deadlock_reports_unavailable():
    status = _status(_issue("DEADLOCK_REPORTS_UNAVAILABLE", "deadlocks"))
    states = _states(_snapshot(status=status))
    for code in ("CT-01", "CT-02", "CT-03", "CT-04"):
        assert (states[code].estado, _codes(states[code])) == (NO_EVALUADA, ["DEADLOCK_REPORTS_UNAVAILABLE"]), code
        # no es una seccion leida a medias: no hay hallazgos que descartar
        assert not states[code].descartar_hallazgos
    assert states["CT-05"].estado == EVALUADA


def test_postgres_without_log_lock_waits_leaves_ct01_and_ct02_partial():
    status = _status(_issue("DEADLOCK_ROW_WAITS_UNLOGGED", "deadlocks"), _issue("DEADLOCK_REPORTS_PARTIAL", "deadlocks"))
    states = _states(_snapshot(status=status))
    assert _codes(states["CT-01"]) == ["DEADLOCK_REPORTS_PARTIAL", "DEADLOCK_ROW_WAITS_UNLOGGED"]
    assert states["CT-02"].estado == PARCIAL
    # CT-03 y CT-04 no necesitan saber que fila esperaba cada una
    assert _codes(states["CT-03"]) == ["DEADLOCK_REPORTS_PARTIAL"]


def test_ct05_without_seeing_other_sessions():
    status = _status(_issue("MISSING_PROCESS_PRIVILEGE", "active_queries"), failed=("active_queries",))
    state = _states(_snapshot(status=status))["CT-05"]
    assert (state.estado, _codes(state)) == (NO_EVALUADA, ["MISSING_PROCESS_PRIVILEGE"])
    assert state.descartar_hallazgos


def test_track_counts_off_blinds_ap05():
    state = _states(_snapshot(status=_status(_issue("TRACK_COUNTS_OFF"))))["AP-05"]
    assert (state.estado, _codes(state)) == (NO_EVALUADA, ["TRACK_COUNTS_OFF"])
    # con los contadores apagados todos los indices parecen sin uso
    assert state.descartar_hallazgos


def test_info_issues_that_do_not_affect_a_rule_change_nothing():
    status = _status(_issue("STATS_RECENTLY_RESET"), _issue("PAYLOAD_TOO_LARGE"))
    assert all(state.estado == EVALUADA for state in _states(_snapshot(status=status)).values())


def test_snapshot_without_collection_status_uses_only_the_detector_conditions():
    # mensajes encolados antes del campo: no se sabe si una seccion fallo
    states = _states(_snapshot(status=None, explains=[]))
    assert _codes(states["AP-04"]) == ["SIN_PLAN"]
    assert all(states[code].estado == EVALUADA for code in ("CT-01", "CT-05"))


def test_the_pipeline_goldens_can_be_evaluated():
    for name in ("postgres_snapshot.json", "mysql_snapshot.json"):
        snapshot = SnapshotPayload.model_validate(json.loads((GOLDEN / name).read_text(encoding="utf-8")))
        states = evaluate_pathologies(snapshot, {}, NOW)
        assert len(states) == len(CODES)


# ------------------------------------------------------------ descartar


def _finding(antipatron):
    return Hallazgo(antipatron=antipatron, severidad="medium", evidencia={}, explicacion="e", recomendacion="r")


def test_findings_of_a_rule_without_its_source_are_dropped():
    status = _status(_issue("SECTION_FAILED", "indexes"), failed=("indexes",))
    states = evaluate_pathologies(_snapshot(status=status), _with_baseline(), NOW)
    findings = [_finding("missing_index"), _finding("avoidable_full_scan"), _finding("unused_index")]
    kept, dropped = drop_unreliable_findings(findings, states)
    assert [f.antipatron for f in kept] == ["avoidable_full_scan"]
    assert [f.antipatron for f in dropped] == ["missing_index", "unused_index"]


def test_nothing_is_dropped_without_states():
    findings = [_finding("missing_index")]
    assert drop_unreliable_findings(findings, []) == (findings, [])


# ------------------------------------------------------------- guardado


def test_bucket_is_ten_minutes_in_utc():
    assert evaluation_bucket(NOW) == datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
    bogota = timezone(timedelta(hours=-5))
    assert evaluation_bucket(datetime(2026, 11, 12, 10, 19, 59, tzinfo=bogota)) == datetime(
        2026, 11, 12, 15, 10, tzinfo=timezone.utc)


def test_save_evaluation_sends_one_row_per_pathology_and_purges():
    conn = mock.MagicMock()
    engine = mock.MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    status = _status(_issue("DEADLOCK_REPORTS_UNAVAILABLE", "deadlocks"))
    states = evaluate_pathologies(_snapshot(status=status), {}, NOW)

    save_evaluation(engine, "db-1", NOW, states)

    (insert, rows), (purge, params) = (call.args for call in conn.execute.call_args_list)
    assert "INSERT INTO public.estado_evaluacion" in str(insert)
    assert "ON CONFLICT (db_id, codigo, tramo, estado, firma)" in str(insert)
    assert [row["codigo"] for row in rows] == list(CODES.values())
    ct01 = next(row for row in rows if row["codigo"] == "CT-01")
    assert (ct01["estado"], ct01["firma"]) == (NO_EVALUADA, "DEADLOCK_REPORTS_UNAVAILABLE@deadlocks")
    reason, = json.loads(ct01["motivos"])
    assert reason["origen"] == "recoleccion" and reason["severidad"] == "degraded"
    assert reason["accion"] == CATALOG["DEADLOCK_REPORTS_UNAVAILABLE"].remediation
    assert all(row["tramo"] == evaluation_bucket(NOW) for row in rows)
    assert "DELETE FROM public.estado_evaluacion" in str(purge)
    assert params == {"db_id": "db-1", "before": NOW - EVALUATION_RETENTION}


def test_save_evaluation_without_states_does_not_touch_the_database():
    engine = mock.MagicMock()
    save_evaluation(engine, "db-1", NOW, [])
    engine.begin.assert_not_called()


@pytest.mark.parametrize("code", sorted(COLLECTION_CODES))
def test_collection_reasons_carry_the_catalog_texts(code):
    issue = _issue(code)
    assert issue["message"] and issue["remediation"]
