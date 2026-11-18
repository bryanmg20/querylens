"""AP-03 predicado no sargable (SegundoInforme, Tabla 2), igual para ambos
motores: un SELECT simple con las mismas condiciones de escaneo completo que
AP-02 (escaneo completo evitable) cuyo predicado envuelve en una funcion,
una operacion o una conversion de tipo una columna que tiene un indice que
empieza por ella, asi que el motor no puede usarlo.

Los predicados son los que manda el pipeline para los casos de la bateria del
007 (ql_sandbox/explain_predicados); las pruebas con prefijo d3/d4/d6 son las
correcciones pedidas en telemetry_pipeline/references/008. Los armadores del
snapshot son los de test_predicate_fixes.py.
"""
from anti_patterns.non_sargable_predicate import _detect_predicate_subtypes, detect_non_sargable_predicates
from test_predicate_fixes import (
    FULL_SCAN_NOTE,
    LOWER_NOTE,
    MYSQL_QUERY_ID,
    NOW,
    PG_QUERY_ID,
    _history,
    _index,
    _snapshot,
)


def _subtypes(predicate):
    return _detect_predicate_subtypes(predicate)


def _non_sargable(query_id, canonic_query, operation, previous_calls=30, previous_rows=30, **snapshot_kwargs):
    # ventana: 20 ejecuciones y 20 filas devueltas (1 fila por ejecucion); note
    # tiene indice salvo que se pase otra lista
    snapshot_kwargs.setdefault("indexes", [_index("note")])
    snapshot = _snapshot(query_id, canonic_query, [operation], **snapshot_kwargs)
    return detect_non_sargable_predicates(snapshot, _history(query_id, previous_calls, previous_rows), NOW)


# ------------------------------------------------------- subtipos del predicado
#
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


# ------------------------------------------------------------ detector completo


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
    assert _non_sargable(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], previous_calls=49, previous_rows=49) == []


def test_ap03_skips_when_real_selectivity_is_high():
    # 20 ejecuciones que devuelven 20.000 filas cada una (20 % de la tabla)
    assert _non_sargable(
        PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE[0], rows_returned=400_000 + 30,
    ) == []


def test_ap03_without_plan_is_not_evaluated():
    snapshot = _snapshot(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE)
    snapshot.canonic_explains = []
    assert detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 30, 30), NOW) == []


# D3: sin ejecuciones en la ventana no se evalua, aunque haya plan

def test_d3_non_sargable_skips_query_without_executions_even_with_plan():
    snapshot = _snapshot(PG_QUERY_ID, LOWER_NOTE, FULL_SCAN_NOTE, indexes=[_index("note")])
    assert detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 50)) == []
    assert len(detect_non_sargable_predicates(snapshot, _history(PG_QUERY_ID, 40))) == 1


# D4: la condicion de un self join no se atribuye a la lectura interna

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


# D6: full_index_scan cuenta como escaneo completo (base de AP-02)

def test_d6_non_sargable_full_index_scan_is_medium():
    # caso 13 (MySQL): DATE(created_at) recorre todo el indice de created_at;
    # severidad media frente al full_table_scan
    operations = [{
        "type": "scan", "access_method": "full_index_scan", "relation": "ql_pt_orders",
        "table_name": None, "estimated_rows": 99835, "index_name": "idx_created_at",
        "predicate": "(DATE(ql_pt_orders.created_at) = '$1')",
    }]
    snapshot = _snapshot(
        MYSQL_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE CAST(created_at AS DATE) = $1", operations,
        indexes=[_index("created_at", "idx_created_at")],
    )
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
