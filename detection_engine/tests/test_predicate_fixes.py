"""Correcciones del detector pedidas en telemetry_pipeline/references/008 (D1-D9).

Los predicados son los que manda el pipeline para los casos de la bateria del
007 (ql_sandbox/explain_predicados). Los snapshots son minimos y se validan con
el contrato, igual que los que llegan por la cola.
"""
from datetime import datetime, timezone

from querylens_contract import SnapshotPayload

from anti_patterns.avoidable_full_scan import detect_avoidable_full_scans
from anti_patterns.missing_index import _filtered_columns, detect_missing_indexes
from anti_patterns.non_sargable_predicate import (
    _detect_predicate_subtypes,
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


def _subtypes(predicate, table="ql_pt_orders", engine="postgres", belongs=None):
    return _detect_predicate_subtypes(
        predicate, table, None, TYPES, engine=engine, belongs_to_table=belongs,
    )


# D1: (varchar)::text de Postgres no es una conversion que impida el indice

def test_d1_varchar_to_text_is_not_implicit_cast():
    assert _subtypes("((status)::text = $1)") == []
    assert _subtypes("((status)::text = ANY ($1))") == []
    assert _subtypes("((status)::text ~~ $1)") == []


def test_d1_char_to_text_is_still_implicit_cast():
    assert _subtypes("((code)::text = $1)") == [("implicit_cast", "code")]


def test_d1_unknown_type_is_still_reported():
    assert _subtypes("((status)::text = $1)", table=None) == [("implicit_cast", "status")]


def test_d1_other_casts_are_still_reported():
    assert _subtypes("((status)::numeric = $1)") == [("implicit_cast", "status")]


# D2: MySQL redacta los numeros como $n y los textos como '$n'

def test_d2_mysql_text_column_against_number():
    assert _subtypes("(o.tel = $1)", engine="mysql") == [("implicit_cast", "tel")]
    assert _subtypes("(o.tel = -$1)", engine="mysql") == [("implicit_cast", "tel")]
    assert _subtypes("(o.tel IN ($1, '$2'))", engine="mysql") == [("implicit_cast", "tel")]
    assert _subtypes("(o.created_at BETWEEN $1 AND $2)", engine="mysql") == [("implicit_cast", "created_at")]


def test_d2_mysql_text_column_against_text_is_fine():
    assert _subtypes("(o.tel = '$1')", engine="mysql") == []
    assert _subtypes("(o.customer_id = $1)", engine="mysql") == []


def test_d2_postgres_parameter_is_not_inferred():
    # en Postgres todo valor llega como $n, sin tipo
    assert _subtypes("(note = $1)") == []


# D4: condiciones de join atribuidas a la tabla del scan

def _belongs_to(alias):
    return lambda column: not column.table or column.table == alias


def test_d4_join_condition_only_counts_own_columns():
    # caso 39 (MySQL, scan de c)
    found = _subtypes("(LOWER(c.email) = LOWER(o.note))", table="ql_pt_customers",
                      engine="mysql", belongs=_belongs_to("c"))
    assert found == [("function_on_column", "email")]


def test_d4_parameterized_index_cond_from_other_table():
    # caso 36 (Postgres, Index Cond del scan de b con la columna de a)
    assert _subtypes("(id = (a.id + $1))", belongs=_belongs_to("b")) == []


def test_d4_missing_index_ignores_other_table():
    equality, range_ = _filtered_columns("((o.customer_id = c.id) AND (o.note = '$1'))", _belongs_to("o"))
    assert equality == ["note"] and range_ == []


# D5: (InitPlan N).colM de Postgres 17

def test_d5_initplan_column_reference_is_cleaned():
    assert _strip_engine_internal_markers("(customer_id = (InitPlan 2).col1)") == "(customer_id = NULL)"
    assert _strip_engine_internal_markers("(o.customer_id = (SubPlan 1))") == "(o.customer_id = NULL)"
    assert _predicate_columns("(customer_id = (InitPlan 2).col1)") == {"customer_id"}


# D9: MySQL muestra DATE(col) como CAST(col AS DATE)

def test_d9_mysql_cast_as_date_is_function():
    assert _subtypes("(CAST(o.created_at AS DATE) = '$1')", engine="mysql") == [
        ("function_on_column", "created_at")
    ]


def test_d9_postgres_cast_as_date_unchanged():
    assert _subtypes("((created_at)::date = $1)") == [("implicit_cast", "created_at")]


# Detectores completos: D3 (ejecuciones en la ventana) y D8 (table_name)

def _snapshot(query_id, canonic_query, operations, execution_count=50, rows_returned=50):
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
        }],
        "tables": [{"schema_name": "public", "table_name": "ql_pt_orders", "live_rows": 100_000}],
        "columns": [
            {"schema_name": "public", "table_name": "ql_pt_orders", "column_name": column, "data_type": data_type}
            for (_, table, column), data_type in TYPES.items() if table == "ql_pt_orders"
        ],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        "canonic_explains": [{
            "query_id": query_id,
            "explain_source": "generic_plan",
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


def test_d3_non_sargable_skips_query_without_executions_even_with_plan():
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1", FULL_SCAN_NOTE)
    assert detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 50)) == []
    assert len(detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 40))) == 1


def test_d3_missing_index_skips_query_without_executions():
    operations = [{**FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)
    assert detect_missing_indexes(snapshot, history=_history(PG_QUERY_ID, 50)) == []
    assert len(detect_missing_indexes(snapshot, history=_history(PG_QUERY_ID, 40))) == 1


def test_d8_table_name_resolves_renamed_relation():
    # caso 35: Postgres llama ql_pt_orders_1 a la segunda lectura de la tabla
    operations = [{**FULL_SCAN_NOTE[0], "relation": "ql_pt_orders_1", "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)

    missing = detect_missing_indexes(snapshot)
    assert [f.table_name for f in missing] == ["ql_pt_orders"]

    sargable_ops = [{**operations[0], "predicate": "(lower(note) = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1", sargable_ops)
    assert [f.table_name for f in detect_non_sargable_predicates(snapshot)] == ["ql_pt_orders"]

    _, tables_with_traffic = _collect_query_traffic(snapshot)
    assert tables_with_traffic == {("public", "ql_pt_orders")}

    full_scan = detect_avoidable_full_scans(
        snapshot, _history(PG_QUERY_ID, 30, rows_returned=30), datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc),
    )
    assert [f.table_name for f in full_scan] == ["ql_pt_orders"]


def test_d4_self_join_condition_is_not_attributed_to_inner_read():
    # caso 36 (MySQL): b.id = (a.id + $1) llega en el scan de b; a y b son la misma tabla
    operations = [{
        "type": "scan", "access_method": "index_lookup", "relation": "b",
        "table_name": None, "estimated_rows": 1, "predicate": "(b.id = (a.id + $1))",
    }]
    snapshot = _snapshot(
        MYSQL_QUERY_ID,
        "SELECT a.id FROM ql_pt_orders AS a JOIN ql_pt_orders AS b ON b.id = a.id + $1",
        operations,
    )
    assert detect_non_sargable_predicates(snapshot) == []


# D6: AP-02 cuenta full_index_scan como escaneo completo (solo AP-02)

def _full_index_scan_snapshot(canonic_query, estimated_rows=99835):
    operations = [{
        "type": "scan", "access_method": "full_index_scan", "relation": "ql_pt_orders",
        "table_name": None, "estimated_rows": estimated_rows, "index_name": "idx_created_at",
        "predicate": "(CAST(ql_pt_orders.created_at AS DATE) = '$1')",
    }]
    return _snapshot(MYSQL_QUERY_ID, canonic_query, operations)


def _ap02(snapshot):
    return detect_avoidable_full_scans(
        snapshot, _history(MYSQL_QUERY_ID, 30, rows_returned=30), datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc),
    )


def test_d6_full_index_scan_counts_in_simple_select():
    # caso 13 (MySQL): DATE(created_at) recorre todo el indice de created_at
    findings = _ap02(_full_index_scan_snapshot("SELECT id FROM ql_pt_orders WHERE CAST(created_at AS DATE) = $1"))
    assert [(f.table_name, f.evidencia["access_method"]) for f in findings] == [("ql_pt_orders", "full_index_scan")]


def test_d6_full_index_scan_with_limit_is_not_evaluated():
    # caso 26: ORDER BY ... LIMIT recorre el indice en orden y corta enseguida
    snapshot = _full_index_scan_snapshot(
        "SELECT id FROM ql_pt_orders WHERE total > $1 ORDER BY created_at DESC LIMIT $2", estimated_rows=3.33,
    )
    assert _ap02(snapshot) == []


def test_d6_other_detectors_still_ignore_full_index_scan():
    snapshot = _full_index_scan_snapshot("SELECT id FROM ql_pt_orders WHERE CAST(created_at AS DATE) = $1")
    assert detect_missing_indexes(snapshot) == []
    assert [f.severidad for f in detect_non_sargable_predicates(snapshot)] == ["medium"]


def test_d2_mysql_end_to_end():
    operations = [{
        "type": "scan", "access_method": "full_table_scan", "relation": "o",
        "table_name": None, "estimated_rows": 10000, "predicate": "(o.tel = $1)",
    }]
    snapshot = _snapshot(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders AS o WHERE o.tel = $1", operations)
    findings = detect_non_sargable_predicates(snapshot)
    assert [(f.evidencia["subtipo"], f.evidencia["column"], f.table_name) for f in findings] == [
        ("implicit_cast", "tel", "ql_pt_orders")
    ]
