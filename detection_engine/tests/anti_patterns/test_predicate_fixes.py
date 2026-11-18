"""Armadores compartidos de las pruebas de los detectores que leen el plan, y
las correcciones de telemetry_pipeline/references/008 (D1-D9) que tocan
varios detectores en la misma prueba.

Las pruebas de cada regla estan en su archivo: test_ap03_non_sargable_predicate.py
(AP-03 predicado no sargable), test_ap04_missing_index.py (AP-04 indice
ausente) y test_ap02_avoidable_full_scan.py (AP-02 escaneo completo evitable);
las tres y test_finding_contract.py importan los armadores de aca.

Los predicados son los que manda el pipeline para los casos de la bateria del
007 (ql_sandbox/explain_predicados). Los snapshots son minimos y se validan con
el contrato, igual que los que llegan por la cola.
"""
from datetime import datetime, timezone

from querylens_contract import SnapshotPayload

from anti_patterns.avoidable_full_scan import detect_avoidable_full_scans
from anti_patterns.missing_index import detect_missing_indexes
from anti_patterns.non_sargable_predicate import (
    _strip_engine_internal_markers,
    detect_non_sargable_predicates,
)
from anti_patterns.unused_index import _collect_query_traffic, _predicate_columns
from models import StatementHistory

TYPES = {
    (None, "ql_pt_orders", "id"): "integer",
    (None, "ql_pt_orders", "customer_id"): "integer",
    (None, "ql_pt_orders", "status"): "character varying",
    (None, "ql_pt_orders", "created_at"): "timestamp without time zone",
    (None, "ql_pt_orders", "note"): "text",
    (None, "ql_pt_orders", "code"): "character",
    (None, "ql_pt_orders", "tel"): "varchar",
    (None, "ql_pt_customers", "email"): "character varying",
}

PG_QUERY_ID = 123
MYSQL_QUERY_ID = "ql_demo/abc"
NOW = datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc)
LOWER_NOTE = "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1"


# ------------------------------------------------------------------ armadores


def _index(column, name=None):
    name = name or f"idx_{column}"
    return {
        "schema_name": "public", "table_name": "ql_pt_orders", "index_name": name, "index_scans": 0,
        "index_ref": f"CREATE INDEX {name} ON public.ql_pt_orders USING btree ({column})",
    }


def _snapshot(query_id, canonic_query, operations, execution_count=50, rows_returned=50, indexes=()):
    statement = {
        "query_id": query_id,
        "query_text": canonic_query,
        "schema_name": "public",
        "execution_count": execution_count,
        "rows_returned": rows_returned,
        "total_time_ms": 500.0,
        "mean_time_ms": 10.0,
        "counters_epoch": "2026-10-13 10:00:00.000000",
        "canonic_query": canonic_query,
    }
    return SnapshotPayload.model_validate({
        "db_id": "db_test",
        "statements": [statement],
        "top_impact_queries": [statement],
        "locks": [],
        "active_queries": [],
        "indexes": [{
            "schema_name": "public", "table_name": "ql_pt_orders", "index_name": "ql_pt_orders_pkey",
            "index_scans": 10, "index_ref": "CREATE UNIQUE INDEX ql_pt_orders_pkey ON public.ql_pt_orders USING btree (id)",
        }, *indexes],
        "tables": [{"schema_name": "public", "table_name": "ql_pt_orders", "live_rows": 100_000}],
        "columns": [
            {"schema_name": "public", "table_name": "ql_pt_orders", "column_name": column, "data_type": data_type}
            for (_, table, column), data_type in TYPES.items() if table == "ql_pt_orders"
        ],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "canonic_explains": [{
            "query_id": query_id,
            # valores reales del pipeline: generic / auto_explain (Postgres), sample (MySQL)
            "explain_source": "sample" if isinstance(query_id, str) else "generic",
            "canonical_plan": {"physical_operations": operations},
        }],
    })


def _history(query_id, execution_count, rows_returned=50):
    return {str(query_id): StatementHistory(
        query_id=str(query_id),
        captured_at=datetime(2026, 10, 13, 10, 5, tzinfo=timezone.utc),
        execution_count=execution_count,
        total_time_ms=500.0,
        rows_returned=rows_returned,
        counters_epoch=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
    )}


FULL_SCAN_NOTE = [{
    "type": "scan", "access_method": "full_table_scan", "relation": "ql_pt_orders",
    "table_name": "ql_pt_orders", "estimated_rows": 10, "predicate": "(lower(note) = $1)",
}]


# ------------------------------------------- correcciones de varios detectores


def test_d5_initplan_column_reference_is_cleaned():
    # (InitPlan N).colM de Postgres 17: AP-03 y AP-05 limpian la referencia
    assert _strip_engine_internal_markers("(customer_id = (InitPlan 2).col1)") == "(customer_id = NULL)"
    assert _strip_engine_internal_markers("(o.customer_id = (SubPlan 1))") == "(o.customer_id = NULL)"
    assert _predicate_columns("(customer_id = (InitPlan 2).col1)") == {"customer_id"}


def test_d8_table_name_resolves_renamed_relation():
    # caso 35: Postgres llama ql_pt_orders_1 a la segunda lectura de la tabla;
    # AP-04, AP-03, AP-05 y AP-02 la resuelven con table_name
    operations = [{**FULL_SCAN_NOTE[0], "relation": "ql_pt_orders_1", "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)

    missing = detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 30, rows_returned=30), NOW)
    assert [f.table_name for f in missing] == ["ql_pt_orders"]

    sargable_ops = [{**operations[0], "predicate": "(lower(note) = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, LOWER_NOTE, sargable_ops, indexes=[_index("note")])
    assert [f.table_name for f in detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 30, 30), NOW)] == ["ql_pt_orders"]

    assert set(_collect_query_traffic(snapshot)) == {("public", "ql_pt_orders")}

    full_scan = detect_avoidable_full_scans(snapshot, _history(PG_QUERY_ID, 30, rows_returned=30), NOW)
    assert [f.table_name for f in full_scan] == ["ql_pt_orders"]
