"""AP-08 (error grave de estimacion de cardinalidad): la estimacion del plan
se compara con las filas de la MISMA ejecucion (estimates.actual_rows), no
con el promedio de la ventana. Sin actual_rows no se evalua
(references/004_ap08_filas_misma_ejecucion.md).
"""
from datetime import datetime, timezone

from querylens_contract import SnapshotPayload

from anti_patterns.cardinality_misestimate import detect_cardinality_misestimates
from models import StatementHistory

CAPTURED_AT = datetime(2026, 10, 29, 10, 6, tzinfo=timezone.utc)
EPOCH = "2026-10-29 10:00:00.000000"


def _snapshot(output_rows, actual_rows, canonic_query="SELECT id FROM t WHERE a = $1 AND b = $2",
              execution_count=50, rows_returned=50_000, explain_source="history"):
    statement = {
        "query_id": "ql_demo/abc", "query_text": "q", "schema_name": "ql_demo",
        "execution_count": execution_count, "rows_returned": rows_returned,
        "total_time_ms": 500.0, "mean_time_ms": 10.0, "counters_epoch": EPOCH,
        "canonic_query": canonic_query,
    }
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": [statement], "top_impact_queries": [statement],
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "canonic_explains": [{
            "query_id": "ql_demo/abc", "explain_source": explain_source,
            "canonical_plan": {"estimates": {"output_rows": output_rows, "actual_rows": actual_rows}},
        }],
    })


def _history(execution_count=30):
    # la consulta ya tenia fila: con execution_count menor que el actual, corrio en la ventana
    return {"ql_demo/abc": StatementHistory(
        query_id="ql_demo/abc", captured_at=datetime(2026, 10, 29, 10, 5, tzinfo=timezone.utc),
        execution_count=execution_count, total_time_ms=300.0, rows_returned=30_000,
        counters_epoch=datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc),
    )}


def _detect(snapshot, history=None):
    return detect_cardinality_misestimates(snapshot, history or _history(), CAPTURED_AT)


def test_reports_when_the_same_execution_disagrees_with_the_estimate():
    finding, = _detect(_snapshot(output_rows=8, actual_rows=20_000))
    assert finding.antipatron == "cardinality_misestimate"
    assert finding.severidad == "high"  # q-error 2.500
    assert finding.evidencia["actual_rows"] == 20_000
    assert finding.evidencia["estimated_rows"] == 8
    assert finding.evidencia["direccion"] == "subestimacion"
    assert finding.evidencia["explain_source"] == "history"
    assert finding.table_name == "t"


def test_compares_against_the_same_execution_not_the_window_average():
    # card07: el plan del valor frecuente estima bien sus 500.000 filas, aunque
    # el promedio de la ventana (1.000 por ejecucion) quede muy lejos
    snapshot = _snapshot(output_rows=498_033, actual_rows=500_000, rows_returned=50_000)
    assert _detect(snapshot) == []


def test_without_actual_rows_the_query_is_not_evaluated():
    # plan generico de Postgres o sample de MySQL: no hay respaldo con el promedio
    snapshot = _snapshot(output_rows=8, actual_rows=None, explain_source="sample")
    assert _detect(snapshot) == []


def test_requires_an_execution_since_the_previous_snapshot():
    # un plan viejo de una consulta que dejo de correr no repite el hallazgo
    snapshot = _snapshot(output_rows=8, actual_rows=20_000, execution_count=30)
    assert _detect(snapshot, _history(execution_count=30)) == []


def test_q_error_below_ten_is_not_reported():
    assert _detect(_snapshot(output_rows=1_000, actual_rows=9_000)) == []


def test_small_counts_are_not_reported():
    # q-error 50 pero ninguno de los dos llega a 100 filas
    assert _detect(_snapshot(output_rows=1, actual_rows=50)) == []


def test_medium_severity_between_ten_and_a_hundred():
    finding, = _detect(_snapshot(output_rows=200, actual_rows=5_000))
    assert finding.severidad == "medium"
    assert finding.evidencia["direccion"] == "subestimacion"


def test_overestimation_is_reported_too():
    # card05 / card08 en MySQL: 10 % fijo sin histograma para un valor de 1.000 filas
    finding, = _detect(_snapshot(output_rows=85_405, actual_rows=1_000))
    assert finding.evidencia["direccion"] == "sobreestimacion"


def test_excluded_shapes_are_not_evaluated():
    for query in (
        "SELECT id FROM t WHERE a = $1 LIMIT $2",
        "SELECT a, count(*) FROM t WHERE b = $1 GROUP BY a",
        "SELECT DISTINCT a FROM t WHERE b = $1",
        "SELECT count(*) FROM t WHERE b = $1",
    ):
        assert _detect(_snapshot(output_rows=8, actual_rows=20_000, canonic_query=query)) == [], query
