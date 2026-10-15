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


def _subtypes(predicate):
    return _detect_predicate_subtypes(predicate)


# Conversiones: una sola regla en los dos motores, un cast sobre la columna.
# El pipeline las deja iguales (telemetry_pipeline/stages/type_conversions.py,
# probado en tests/test_type_conversions.py): escribe la implicita de MySQL y
# quita el (col)::text que Postgres pone en las columnas varchar.

def test_cast_on_column_is_implicit_cast_in_both_engines():
    # Postgres: int contra decimal; MySQL: char contra numero (lo escribe el pipeline)
    assert _subtypes("((customer_id)::numeric = $1)") == [("implicit_cast", "customer_id")]
    assert _subtypes("(CAST(o.code AS DOUBLE PRECISION) = $1)") == [("implicit_cast", "code")]
    assert _subtypes("(CAST(o.code AS DOUBLE PRECISION) IN ($1, '$2'))") == [("implicit_cast", "code")]
    # char(n) -> text de Postgres: el pipeline lo deja porque si cambia el valor
    assert _subtypes("((code)::text = $1)") == [("implicit_cast", "code")]


def test_bare_column_has_no_conversion_in_both_engines():
    # sin cast no hay conversion, sin importar el motor ni el tipo del valor
    assert _subtypes("(status = $1)") == []
    assert _subtypes("(o.tel = $1)") == []
    assert _subtypes("(o.tel = '$1')") == []


def test_column_against_column_is_not_evaluated():
    # caso 29: Postgres escribe la conversion de una de las columnas y MySQL no;
    # con dos columnas de la misma fila ningun indice sirve, haya cast o no
    assert _subtypes("(total > (id)::numeric)") == []
    assert _subtypes("(ql_pt_orders.total > ql_pt_orders.id)") == []
    assert _subtypes("(lower(note) = lower(status))") == []
    # con un valor del otro lado si cuenta
    assert _subtypes("((total + id) > $1)") == [("arithmetic_on_column", "total")]


def test_cast_on_value_is_not_a_conversion():
    assert _subtypes("(created_at >= CAST('$1' AS TIMESTAMPTZ))") == []


def test_date_function_in_both_engines():
    # el pipeline escribe DATE(col) para el CAST(col AS DATE) de MySQL
    assert _subtypes("(date(created_at) = $1)") == [("function_on_column", "created_at")]
    assert _subtypes("(DATE(o.created_at) = '$1')") == [("function_on_column", "created_at")]


# D4: condiciones de join atribuidas a la tabla del scan (AP-04; AP-03 solo
# evalua SELECT simples)

def _belongs_to(alias):
    return lambda column: not column.table or column.table == alias


def test_d4_missing_index_ignores_other_table():
    equality, range_, _ = _filtered_columns("((o.customer_id = c.id) AND (o.note = '$1'))", _belongs_to("o"))
    assert equality == ["note"] and range_ == []


# D5: (InitPlan N).colM de Postgres 17

def test_d5_initplan_column_reference_is_cleaned():
    assert _strip_engine_internal_markers("(customer_id = (InitPlan 2).col1)") == "(customer_id = NULL)"
    assert _strip_engine_internal_markers("(o.customer_id = (SubPlan 1))") == "(o.customer_id = NULL)"
    assert _predicate_columns("(customer_id = (InitPlan 2).col1)") == {"customer_id"}


# Detectores completos: D3 (ejecuciones en la ventana) y D8 (table_name)

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


def test_d3_non_sargable_skips_query_without_executions_even_with_plan():
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1", FULL_SCAN_NOTE,
                         indexes=[_index("note")])
    assert detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 50)) == []
    assert len(detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 40))) == 1


NOW = datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc)


def test_d3_missing_index_skips_query_without_executions():
    operations = [{**FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)
    assert detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 50), NOW) == []
    assert len(detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 40), NOW)) == 1


# AP-04 indice ausente con la base de AP-02 escaneo completo evitable

def _missing(query_id, canonic_query, operation, previous_calls=30, previous_rows=30):
    # ventana: 20 ejecuciones y 20 filas devueltas (1 fila por ejecucion)
    snapshot = _snapshot(query_id, canonic_query, [operation])
    return detect_missing_indexes(snapshot, _history(query_id, previous_calls, previous_rows), NOW)


MYSQL_FULL_SCAN = {
    "type": "scan", "access_method": "full_table_scan", "relation": "ql_pt_orders",
    "table_name": None, "index_name": None,
}


def test_ap04_uses_real_selectivity_not_estimate():
    # caso 04 (MySQL): BETWEEN sin indice, MySQL estima la constante 11,1 %
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 11091.67,
                 "predicate": "(ql_pt_orders.note BETWEEN '$1' AND '$2')"}
    findings = _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note BETWEEN $1 AND $2", operation)
    assert [(f.evidencia["range_columns"], f.evidencia["selectivity"]) for f in findings] == [(["note"], 1e-05)]


def test_ap04_skips_when_real_selectivity_is_high():
    # 20 ejecuciones que devuelven 20.000 filas cada una (20 % de la tabla)
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.note = '$1')"}
    snapshot = _snapshot(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation],
                         execution_count=50, rows_returned=400_000 + 30)
    assert detect_missing_indexes(snapshot, _history(MYSQL_QUERY_ID, 30, 30), NOW) == []


def test_ap04_requires_min_window_calls():
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.note = '$1')"}
    assert _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operation,
                    previous_calls=45, previous_rows=45) == []


def test_ap04_only_simple_selects():
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.note = '$1')"}
    for query in (
        "SELECT id FROM ql_pt_orders WHERE note = $1 LIMIT 10",
        "SELECT COUNT(*) FROM ql_pt_orders WHERE note = $1",
        "SELECT o.id FROM ql_pt_orders AS o JOIN ql_pt_customers AS c ON c.id = o.customer_id WHERE o.note = $1",
    ):
        assert _missing(MYSQL_QUERY_ID, query, operation) == [], query


def test_ap04_like_is_not_counted():
    # casos 09/10: el patron llega redactado y no se sabe si empieza con %
    belongs = _belongs_to("ql_pt_orders")
    for predicate in ("(note ~~ $1)", "(ql_pt_orders.note LIKE '$1')", "(note ~~ '$1%'::text)"):
        assert _filtered_columns(predicate, belongs) == ([], [], []), predicate
    # con otra condicion en el AND, esa si se evalua
    assert _filtered_columns(
        "((ql_pt_orders.note LIKE '$1') AND (ql_pt_orders.total = $2))", belongs, _types(total="decimal"),
    ) == (["total"], [], [])


# AP-04 indice ausente: conversion de tipo sobre la columna

def _types(**types):
    return lambda name: types.get(name)


def test_ap04_cast_on_column_is_discarded_in_both_engines():
    belongs = _belongs_to("ql_pt_orders")
    # MySQL caso 15 (el pipeline escribe la conversion) y Postgres int -> numeric
    assert _filtered_columns(
        "(CAST(ql_pt_orders.code AS DOUBLE PRECISION) = $1)", belongs, _types(code="char"),
    ) == ([], [], [])
    assert _filtered_columns("((customer_id)::numeric = $1)", belongs, _types(customer_id="integer")) == (
        [], [], []
    )
    assert _filtered_columns("((code)::text = $1)", belongs, _types(code="character")) == ([], [], [])


def test_ap04_bare_column_counts_in_both_engines():
    belongs = _belongs_to("ql_pt_orders")
    assert _filtered_columns("(ql_pt_orders.code = '$1')", belongs, _types(code="char")) == (["code"], [], [])
    assert _filtered_columns("(status = $1)", belongs, _types(status="character varying")) == (
        ["status"], [], []
    )


def test_ap04_unknown_type_counts_but_unverified():
    # sin tipo el pipeline no puede escribir la conversion: igual en los dos motores
    belongs = _belongs_to("ql_pt_orders")
    assert _filtered_columns("(ql_pt_orders.code = $1)", belongs, _types()) == (["code"], [], ["code"])
    assert _filtered_columns("(code = $1)", belongs, _types()) == (["code"], [], ["code"])


def test_ap04_is_null_counts_as_equality():
    # caso 08: el indice resuelve IS NULL en los dos motores
    belongs = _belongs_to("ql_pt_orders")
    assert _filtered_columns("(note IS NULL)", belongs) == (["note"], [], [])
    # sin valor no hay conversion posible: no queda sin verificar
    assert _filtered_columns("(ql_pt_orders.note IS NULL)", belongs, _types()) == (["note"], [], [])
    # IS NOT NULL (y el NOT ... IS NULL que agrega MySQL) no cuentan
    assert _filtered_columns("(note IS NOT NULL)", belongs) == ([], [], [])
    assert _filtered_columns("(NOT ql_pt_orders.note IS NULL)", belongs) == ([], [], [])
    assert _filtered_columns("((note IS NULL) AND (total > $1))", belongs, _types(total="numeric")) == (
        ["note"], ["total"], []
    )


def test_ap04_finding_marks_unverified_conversion():
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.total = $1)"}
    finding, = _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE total = $1", operation)
    # total no esta en TYPES del snapshot de prueba: tipo desconocido
    assert finding.evidencia["conversion_verificada"] is False
    assert finding.evidencia["conversion_sin_verificar_por"] == ["tipo_de_columna_desconocido"]
    assert "No se pudo descartar" in finding.explicacion

    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.note = '$1')"}
    finding, = _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operation)
    assert finding.evidencia["conversion_verificada"] is True

    # tel es varchar y se compara con un numero: el pipeline escribe el cast -> sin hallazgo
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10,
                 "predicate": "(CAST(ql_pt_orders.tel AS DOUBLE PRECISION) = $1)"}
    assert _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE tel = $1", operation) == []


def test_ap04_postgres_generic_plan_is_unverified():
    operation = {**FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation])
    finding, = detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 30, 30), NOW)
    assert finding.evidencia["conversion_sin_verificar_por"] == ["plan_generico"]


def test_ap04_full_index_scan_on_composite_index():
    # indice (status, total): MySQL recorre el indice entero filtrando total
    operation = {**MYSQL_FULL_SCAN, "access_method": "full_index_scan", "index_name": "idx_status_total",
                 "estimated_rows": 9983.5, "predicate": "(ql_pt_orders.total = $1)"}
    findings = _missing(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE total = $1", operation)
    assert [(f.evidencia["access_method"], f.evidencia["equality_columns"]) for f in findings] == [
        ("full_index_scan", ["total"])
    ]


def test_d8_table_name_resolves_renamed_relation():
    # caso 35: Postgres llama ql_pt_orders_1 a la segunda lectura de la tabla
    operations = [{**FULL_SCAN_NOTE[0], "relation": "ql_pt_orders_1", "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)

    missing = detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 30, rows_returned=30), NOW)
    assert [f.table_name for f in missing] == ["ql_pt_orders"]

    sargable_ops = [{**operations[0], "predicate": "(lower(note) = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1", sargable_ops,
                         indexes=[_index("note")])
    assert [f.table_name for f in detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 30, 30), NOW)] == ["ql_pt_orders"]

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
        "predicate": "(DATE(ql_pt_orders.created_at) = '$1')",
    }]
    return _snapshot(MYSQL_QUERY_ID, canonic_query, operations, indexes=[_index("created_at", "idx_created_at")])


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


def test_d6_non_sargable_full_index_scan_is_medium():
    # AP-03 predicado no sargable: full_index_scan cuenta como escaneo completo
    # (base de AP-02), con severidad media frente al full_table_scan
    snapshot = _full_index_scan_snapshot("SELECT id FROM ql_pt_orders WHERE CAST(created_at AS DATE) = $1")
    findings = detect_non_sargable_predicates(snapshot, _history(MYSQL_QUERY_ID, 30, 30), NOW)
    assert [f.severidad for f in findings] == ["medium"]


def test_mysql_conversion_end_to_end():
    # predicado como lo deja el pipeline para o.tel (varchar) = numero
    operations = [{
        "type": "scan", "access_method": "full_table_scan", "relation": "o",
        "table_name": None, "estimated_rows": 10000, "predicate": "(CAST(o.tel AS DOUBLE PRECISION) = $1)",
    }]
    snapshot = _snapshot(MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders AS o WHERE o.tel = $1", operations,
                         indexes=[_index("tel")])
    findings = detect_non_sargable_predicates(snapshot, _history(MYSQL_QUERY_ID, 30, 30), NOW)
    assert [(f.evidencia["subtipo"], f.evidencia["column"], f.table_name) for f in findings] == [
        ("implicit_cast", "tel", "ql_pt_orders")
    ]


# AP-03 predicado no sargable con la base de AP-02 escaneo completo evitable

def _non_sargable(query_id, canonic_query, operation, previous_calls=30, previous_rows=30, **snapshot_kwargs):
    # ventana: 20 ejecuciones y 20 filas devueltas (1 fila por ejecucion); note
    # tiene indice salvo que se pase otra lista
    snapshot_kwargs.setdefault("indexes", [_index("note")])
    snapshot = _snapshot(query_id, canonic_query, [operation], **snapshot_kwargs)
    return detect_non_sargable_predicates(snapshot, _history(query_id, previous_calls, previous_rows), NOW)


LOWER_NOTE = "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1"


def test_ap03_simple_select_full_scan_is_reported():
    finding, = _non_sargable(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0])
    assert (finding.evidencia["subtipo"], finding.evidencia["column"], finding.severidad) == (
        "function_on_column", "note", "high"
    )
    assert (finding.evidencia["window_calls"], finding.evidencia["selectivity"]) == (20, 1e-05)
    assert finding.evidencia["unused_indexes"] == ["idx_note"]


def test_ap03_requires_existing_index():
    # sin indice en la columna el escaneo completo es inevitable con o sin
    # LOWER(); ese caso es de AP-02 escaneo completo evitable
    assert _non_sargable(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], indexes=[]) == []
    # un indice compuesto que no empieza por la columna tampoco cuenta
    composite = _index("status, note", "idx_status_note")
    assert _non_sargable(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], indexes=[composite]) == []


def test_ap03_only_simple_selects():
    operation = {**FULL_SCAN_NOTE[0], "relation": "o"}
    for query in (
        "SELECT id FROM ql_pt_orders WHERE LOWER(note) = $1 LIMIT 10",
        "SELECT COUNT(*) FROM ql_pt_orders WHERE LOWER(note) = $1",
        "SELECT o.id FROM ql_pt_orders AS o JOIN ql_pt_customers AS c ON c.id = o.customer_id "
        "WHERE LOWER(o.note) = $1",
    ):
        assert _non_sargable(PG_QUERY_ID, query, operation) == [], query


def test_ap03_requires_full_scan():
    operation = {**FULL_SCAN_NOTE[0], "access_method": "index_lookup", "index_name": "idx_note"}
    assert _non_sargable(PG_QUERY_ID, LOWER_NOTE, operation) == []


def test_ap03_requires_min_window_calls():
    assert _non_sargable(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], previous_calls=45, previous_rows=45) == []


def test_ap03_skips_when_real_selectivity_is_high():
    # 20 ejecuciones que devuelven 20.000 filas cada una (20 % de la tabla)
    assert _non_sargable(
        PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], rows_returned=400_000 + 30,
    ) == []


def test_ap03_without_plan_is_not_evaluated():
    snapshot = _snapshot(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE)
    snapshot.canonic_explains = []
    assert detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 30, 30), NOW) == []
