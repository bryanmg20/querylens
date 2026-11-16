"""AP-04 indice ausente (SegundoInforme, Tabla 2), igual para ambos motores:
un SELECT simple con las mismas condiciones de escaneo completo que AP-02
(escaneo completo evitable) que filtra columnas peladas por igualdad o rango
sin un indice que empiece por ellas. Se recomienda un indice con las columnas
de igualdad primero y luego las de rango.

Los predicados son los que manda el pipeline para los casos de la bateria del
007 (ql_sandbox/explain_predicados); las pruebas con prefijo d3/d4 son las
correcciones pedidas en telemetry_pipeline/references/008. Los armadores del
snapshot son los de test_predicate_fixes.py.
"""
from anti_patterns.missing_index import _filtered_columns, detect_missing_indexes
from test_predicate_fixes import (
    FULL_SCAN_NOTE,
    MYSQL_QUERY_ID,
    NOW,
    PG_QUERY_ID,
    _history,
    _snapshot,
)

MYSQL_FULL_SCAN = {
    "type": "scan", "access_method": "full_table_scan", "relation": "ql_pt_orders",
    "table_name": None, "index_name": None,
}


def _belongs_to(alias):
    return lambda column: not column.table or column.table == alias


def _types(**types):
    return lambda name: types.get(name)


def _missing(query_id, canonic_query, operation, previous_calls=30, previous_rows=30):
    # ventana: 20 ejecuciones y 20 filas devueltas (1 fila por ejecucion)
    snapshot = _snapshot(query_id, canonic_query, [operation])
    return detect_missing_indexes(snapshot, _history(query_id, previous_calls, previous_rows), NOW)


# ------------------------------------------------------------ detector completo


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
                    previous_calls=49, previous_rows=49) == []


def test_ap04_only_simple_selects():
    operation = {**MYSQL_FULL_SCAN, "estimated_rows": 10, "predicate": "(ql_pt_orders.note = '$1')"}
    for query in (
        "SELECT id FROM ql_pt_orders WHERE note = $1 LIMIT 10",
        "SELECT COUNT(*) FROM ql_pt_orders WHERE note = $1",
        "SELECT o.id FROM ql_pt_orders AS o JOIN ql_pt_customers AS c ON c.id = o.customer_id WHERE o.note = $1",
    ):
        assert _missing(MYSQL_QUERY_ID, query, operation) == [], query


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


# D3: sin ejecuciones en la ventana no se evalua

def test_d3_missing_index_skips_query_without_executions():
    operations = [{**FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}]
    snapshot = _snapshot(PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", operations)
    assert detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 50), NOW) == []
    assert len(detect_missing_indexes(snapshot, _history(PG_QUERY_ID, 40), NOW)) == 1


# ------------------------------------------------ columnas que cuentan (regla 6)


def test_d4_missing_index_ignores_other_table():
    # condiciones de join atribuidas a la tabla del scan
    equality, range_, _ = _filtered_columns("((o.customer_id = c.id) AND (o.note = '$1'))", _belongs_to("o"))
    assert equality == ["note"] and range_ == []


def test_ap04_like_is_not_counted():
    # casos 09/10: el patron llega redactado y no se sabe si empieza con %
    belongs = _belongs_to("ql_pt_orders")
    for predicate in ("(note ~~ $1)", "(ql_pt_orders.note LIKE '$1')", "(note ~~ '$1%'::text)"):
        assert _filtered_columns(predicate, belongs) == ([], [], []), predicate
    # con otra condicion en el AND, esa si se evalua
    assert _filtered_columns(
        "((ql_pt_orders.note LIKE '$1') AND (ql_pt_orders.total = $2))", belongs, _types(total="decimal"),
    ) == (["total"], [], [])


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
