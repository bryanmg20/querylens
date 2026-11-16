"""AP-02 escaneo completo evitable (SegundoInforme, Tabla 2), igual para ambos
motores y limitado a SELECT simples:
  1. un SELECT de una sola tabla, sin JOIN, subconsultas, LIMIT, GROUP BY,
     DISTINCT ni agregados (las funciones de ventana se aceptan)
  2. >= 2 ejecuciones en la ventana
  3. el plan recorre completa esa tabla (full_table_scan) o un indice entero
     (full_index_scan)
  4. la tabla tiene >= 10.000 filas vivas
  5. devuelve <= 10 % de las filas: (filas de la ventana / ejecuciones) / filas vivas

Las pruebas de full_index_scan y de los predicados del pipeline estan en
test_predicate_fixes.py (D6); aca van las condiciones de la regla.
"""
from datetime import datetime, timedelta, timezone

import pytest
from querylens_contract import SnapshotPayload

from anti_patterns.avoidable_full_scan import detect_avoidable_full_scans
from models import StatementHistory

NOW = datetime(2026, 11, 16, 10, 0, 10, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(seconds=10)
EPOCH = "2026-11-16 09:00:00.000000"
EPOCH_AT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
PG_ID = 123
MYSQL_ID = "ql_demo/scan"
QUERY = "SELECT id FROM ql_pt_orders WHERE note = $1"
LIVE_ROWS = 100_000
# contadores acumulados de la fila anterior
BASE_CALLS, BASE_ROWS = 30, 30


def _scan(access_method="full_table_scan", relation="ql_pt_orders", table_name="ql_pt_orders"):
    return {
        "type": "scan", "access_method": access_method, "relation": relation,
        "table_name": table_name, "estimated_rows": 10, "predicate": "(note = $1)",
    }


def _snapshot(
    canonic_query=QUERY, operations=None, query_id=PG_ID, window_calls=20, window_rows=20,
    tables=None, epoch=EPOCH, with_explain=True, in_top_impact=True,
):
    statement = {
        "query_id": query_id, "query_text": canonic_query, "canonic_query": canonic_query,
        "schema_name": "public", "database_name": "ql_demo",
        "execution_count": BASE_CALLS + window_calls, "rows_returned": BASE_ROWS + window_rows,
        "total_time_ms": 800.0, "mean_time_ms": 10.0, "counters_epoch": epoch,
    }
    explains = [{
        "query_id": query_id, "explain_source": "auto_explain",
        "canonical_plan": {"physical_operations": [_scan()] if operations is None else operations},
    }] if with_explain else []
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": [statement],
        "top_impact_queries": [statement] if in_top_impact else [],
        "locks": [], "active_queries": [], "indexes": [], "columns": [],
        "tables": tables if tables is not None else [
            {"schema_name": "public", "table_name": "ql_pt_orders", "live_rows": LIVE_ROWS},
        ],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "canonic_explains": explains,
    })


def _history(query_id=PG_ID):
    return {str(query_id): StatementHistory(
        query_id=str(query_id), captured_at=PREVIOUS, execution_count=BASE_CALLS,
        total_time_ms=500.0, rows_returned=BASE_ROWS, counters_epoch=EPOCH_AT,
    )}


def _detect(snapshot, history=None):
    return detect_avoidable_full_scans(snapshot, _history() if history is None else history, NOW)


# ---------------------------------------------------------------- positivos


def test_full_scan_returning_few_rows_is_reported():
    finding, = _detect(_snapshot())
    assert finding.antipatron == "avoidable_full_scan"
    assert finding.severidad == "medium"
    assert finding.table_name == "ql_pt_orders"
    assert finding.evidencia["access_method"] == "full_table_scan"
    assert finding.evidencia["live_rows"] == LIVE_ROWS
    assert finding.evidencia["rows_per_call"] == 1
    assert finding.evidencia["window_calls"] == 20
    assert finding.impacto_ms == pytest.approx(300.0)


def test_mysql_scan_by_alias_is_resolved_to_the_table():
    # MySQL no trae table_name en el plan: la relacion es el alias del FROM
    snapshot = _snapshot(
        canonic_query="SELECT o.id FROM ql_pt_orders AS o WHERE o.note = $1",
        operations=[_scan(relation="o", table_name=None)], query_id=MYSQL_ID,
    )
    finding, = _detect(snapshot, _history(MYSQL_ID))
    assert finding.table_name == "ql_pt_orders"


def test_window_functions_keep_the_select_simple():
    query = "SELECT id, ROW_NUMBER() OVER (ORDER BY id) FROM ql_pt_orders WHERE note = $1"
    assert len(_detect(_snapshot(canonic_query=query))) == 1


# ------------------------------------------------------- umbrales de la regla


def test_selectivity_of_exactly_ten_percent_is_reported_and_above_is_not():
    # 20 ejecuciones: 10.000 filas por ejecucion son el 10 % de 100.000
    assert len(_detect(_snapshot(window_rows=20 * 10_000))) == 1
    assert _detect(_snapshot(window_rows=20 * 10_001)) == []


def test_table_needs_ten_thousand_live_rows():
    def tables(live_rows):
        return [{"schema_name": "public", "table_name": "ql_pt_orders", "live_rows": live_rows}]
    assert _detect(_snapshot(tables=tables(9_999))) == []
    assert len(_detect(_snapshot(tables=tables(10_000)))) == 1


def test_requires_two_calls_in_the_window():
    assert _detect(_snapshot(window_calls=1, window_rows=1)) == []
    assert len(_detect(_snapshot(window_calls=2, window_rows=2))) == 1


# ---------------------------------------------------------------- controles


@pytest.mark.parametrize("query", [
    "SELECT o.id FROM ql_pt_orders AS o JOIN ql_pt_customers AS c ON c.id = o.customer_id WHERE o.note = $1",
    "SELECT note, COUNT(*) FROM ql_pt_orders WHERE note = $1 GROUP BY note",
    "SELECT COUNT(*) FROM ql_pt_orders WHERE note = $1",
    "SELECT DISTINCT note FROM ql_pt_orders WHERE note = $1",
    "SELECT id FROM ql_pt_orders WHERE note = $1 LIMIT $2",
    "SELECT id FROM ql_pt_orders WHERE customer_id IN (SELECT id FROM ql_pt_customers WHERE email = $1)",
    "WITH t AS (SELECT id FROM ql_pt_orders WHERE note = $1) SELECT id FROM t",
    "UPDATE ql_pt_orders SET note = $1 WHERE id = $2",
])
def test_only_simple_selects_are_evaluated(query):
    assert _detect(_snapshot(canonic_query=query)) == []


def test_index_access_is_not_a_full_scan():
    assert _detect(_snapshot(operations=[_scan(access_method="index_lookup")])) == []


def test_without_plan_the_query_is_not_evaluated():
    assert _detect(_snapshot(with_explain=False)) == []


def test_scan_of_another_table_is_not_attributed():
    operation = _scan(relation="ql_pt_customers", table_name="ql_pt_customers")
    assert _detect(_snapshot(operations=[operation])) == []


def test_table_of_another_schema_with_the_same_name_is_not_used():
    # la de la consulta (public) es chica; la grande es homonima de otro schema
    tables = [
        {"schema_name": "public", "table_name": "ql_pt_orders", "live_rows": 500},
        {"schema_name": "otro", "table_name": "ql_pt_orders", "live_rows": LIVE_ROWS},
    ]
    assert _detect(_snapshot(tables=tables)) == []


def test_only_top_impact_queries_are_evaluated():
    assert _detect(_snapshot(in_top_impact=False)) == []


def test_counters_reset_is_not_compared_with_the_previous_period():
    assert _detect(_snapshot(epoch="2026-11-16 09:30:00.000000")) == []


# ------------------------------------------------- consulta nueva en la ventana


def test_query_that_started_in_this_window_uses_all_its_counters():
    started = (NOW - timedelta(seconds=5)).replace(tzinfo=None).isoformat(sep=" ")
    finding, = _detect(_snapshot(epoch=started), history={})
    assert finding.evidencia["query_new_in_window"] is True
    assert finding.evidencia["window_calls"] == BASE_CALLS + 20
