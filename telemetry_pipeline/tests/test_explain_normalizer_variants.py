"""Variantes de nodo y claves que cada motor emite y el clasificador debe reconocer.

test_explain_normalizer.py cubre el camino comun de cada tipo de operacion.
Esta archivo cubre las variantes concretas que un motor produce segun el plan
elegido: el mismo SELECT puede llegar como Bitmap Heap Scan o Seq Scan,
Merge Join o Hash Join, GroupAggregate o Aggregate. Si el clasificador solo
conoce las formas mas frecuentes, cuenta menos de lo que el plan dice y el
consumidor ve un snapshot incompleto sin ninguna señal.

Los nombres de clave equivocados tambien se fijan a proposito: sin esos tests,
un clasificador que hiciera match parcial empezaria a sumar operaciones que el
motor nunca emitio.
"""
import pytest

from stages.explain_normalizer import EXPLAIN_NORMALIZERS

pytestmark = pytest.mark.unit


# ---------- helpers Postgres ----------

def _pg_plan(node_type, **extra):
    base = {
        "Node Type": node_type,
        "Plan Rows": 100,
        "Total Cost": 25.5,
        "Plans": [],
    }
    base.update(extra)
    return [{"Plan": {"Plan": base}}]


def _pg_plan_tree(root_type, children, **root_extra):
    root = {
        "Node Type": root_type,
        "Plan Rows": 1,
        "Total Cost": 1.0,
        "Plans": children,
    }
    root.update(root_extra)
    return [{"Plan": {"Plan": root}}]


def _pg_child(node_type, **extra):
    base = {"Node Type": node_type, "Plan Rows": 1, "Total Cost": 1.0, "Plans": []}
    base.update(extra)
    return base


def _pg_normalize(explain):
    stats = {"query_explain": [explain]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    return stats["canonic_explains"][0]["canonical_plan"]


# ---------- helpers MySQL ----------

def _mysql_node(name="t", access_type="ALL", **extra):
    base = {
        "table_name": name,
        "access_type": access_type,
        "rows_produced_per_join": 50,
    }
    base.update(extra)
    return base


def _mysql_normalize(plan):
    stats = {"query_explain": [{"query_id": 1, "plan": plan}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    return stats["canonic_explains"][0]["canonical_plan"]


def _query_block(**kwargs):
    return {"query_block": kwargs}


# ---------- Postgres: variantes de scan ----------

@pytest.mark.parametrize(
    "node_type,access_method",
    [
        ("Seq Scan", "full_table_scan"),
        ("Index Scan", "index_lookup"),
        ("Index Only Scan", "index_lookup"),
        ("Bitmap Heap Scan", "index_lookup"),
        ("Bitmap Index Scan", "index_lookup"),
    ],
)
def test_pg_every_scan_variant(node_type, access_method):
    """Postgres emite cinco tipos de scan. Bitmap Heap e Index son el camino de
    un indice que no puede atender la query por si solo, y son los que desaparecen
    cuando la tupla del clasificador se reduce a los tres mas conocidos.
    """
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(node_type, **{"Alias": "t"})})
    assert plan["logical_shape"]["scans"] == 1
    assert len(plan["physical_operations"]) == 1
    assert plan["physical_operations"][0]["type"] == "scan"
    assert plan["physical_operations"][0]["access_method"] == access_method
    assert plan["physical_operations"][0]["relation"] == "t"


# ---------- Postgres: variantes de join ----------

@pytest.mark.parametrize("node_type", ["Nested Loop", "Hash Join", "Merge Join"])
def test_pg_every_join_variant(node_type):
    """Merge Join aparece cuando ambos lados llegan ordenados."""
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(node_type)})
    assert plan["logical_shape"]["joins"] == 1
    assert plan["physical_operations"][0]["type"] == "join"


@pytest.mark.parametrize(
    "field", ["Join Filter", "Hash Cond", "Merge Cond"]
)
def test_pg_join_predicate_from_each_field(field):
    plan = _pg_normalize({
        "query_id": 1,
        "plan": _pg_plan("Nested Loop", **{field: "a.id = b.id"}),
    })
    assert plan["physical_operations"][0]["predicate"] == "a.id = b.id"


def test_pg_join_prefers_filter_over_cond():
    """Con varias condiciones manda la primera: el filtro describe las filas que
    se descartan, que es lo que el consumidor busca para ver el costo real.
    """
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan("Nested Loop", **{
        "Join Filter": "(a.id = b.id)",
        "Hash Cond": "a.k = b.k",
    })})
    assert plan["physical_operations"][0]["predicate"] == "(a.id = b.id)"


def test_pg_join_without_predicate_keeps_operation():
    """Un cross join no tiene condicion. La operacion sigue existiendo con
    predicate None: es informacion valuable, porque un join sin predicar es lo
    que produce una multiplicacion de filas. Descartarla pierde justamente el
    dato que hace sospechar del plan.
    """
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan("Nested Loop")})
    assert plan["logical_shape"]["joins"] == 1
    assert len(plan["physical_operations"]) == 1
    assert plan["physical_operations"][0]["predicate"] is None


# ---------- Postgres: variantes de aggregate y sort ----------

@pytest.mark.parametrize("node_type", ["Aggregate", "Group", "GroupAggregate"])
def test_pg_every_aggregate_variant(node_type):
    """GroupAggregate es el plan de un GROUP BY sobre entrada ordenada."""
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(node_type)})
    assert plan["logical_shape"]["aggregates"] == 1
    assert plan["physical_operations"][0]["type"] == "aggregate"


@pytest.mark.parametrize("node_type", ["Sort", "Incremental Sort"])
def test_pg_sort_variants(node_type):
    """Incremental Sort es el sort por lotes de un ORDER BY con indice parcial."""
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(node_type)})
    assert plan["logical_shape"]["sorts"] == 1
    assert plan["physical_operations"][0]["type"] == "sort"


# ---------- Postgres: subconsultas ----------

@pytest.mark.parametrize(
    "node_type", ["Subquery Scan", "SubPlan", "InitPlan"]
)
def test_pg_subquery_node_types(node_type):
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(node_type)})
    assert plan["logical_shape"]["subqueries"] == 1
    assert plan["physical_operations"][0]["type"] == "subquery"


def test_pg_subplan_name_marks_plain_scan_as_subquery():
    """Un scan con 'Subplan Name' es una tabla dentro de una subconsulta
    correlacionada: cuenta como scan y como subquery, y emite las dos
    operaciones. Sin la marca, pasaria por escaneo de tabla raiz y el conteo de
    subqueries quedaria en cero pese a haber una.
    """
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(
        "Seq Scan", **{"Alias": "t", "Subplan Name": "SubPlan 1"}
    )})
    assert plan["logical_shape"]["scans"] == 1
    assert plan["logical_shape"]["subqueries"] == 1
    types = sorted(op["type"] for op in plan["physical_operations"])
    assert types == ["scan", "subquery"]


@pytest.mark.parametrize("relationship", ["SubPlan", "InitPlan"])
def test_pg_parent_relationship_marks_subquery(relationship):
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(
        "Seq Scan", **{"Alias": "t", "Parent Relationship": relationship}
    )})
    assert plan["logical_shape"]["subqueries"] == 1


def test_pg_child_relationship_is_not_a_subquery():
    """'Child' es un Parent Relationship legitimo que no es subconsulta: un scan
    hijo de un join no debe subir el conteo de subqueries."""
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(
        "Seq Scan", **{"Alias": "t", "Parent Relationship": "Child"}
    )})
    assert plan["logical_shape"]["subqueries"] == 0
    assert len(plan["physical_operations"]) == 1


# ---------- Postgres: estimates y recorrido ----------

def test_pg_total_cost_is_not_confused_with_estimated_rows():
    """Total Cost alimenta estimates; Plan Rows alimenta estimated_rows.
    Cruzarlos deja un costo flotante en el campo de filas, que es por donde el
    consumidor busca los scans caros.
    """
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan(
        "Seq Scan", **{"Plan Rows": 777, "Total Cost": 25.5}
    )})
    assert plan["estimates"]["total_cost"] == 25.5
    assert plan["physical_operations"][0]["estimated_rows"] == 777


def test_pg_missing_total_cost_leaves_none_not_zero():
    """0 significaria que el motor spineo que el plan no cuesta nada."""
    plan = _pg_normalize({"query_id": 1, "plan": [{"Plan": {
        "Node Type": "Seq Scan", "Plan Rows": 5, "Plans": [],
    }}]})
    assert plan["estimates"]["total_cost"] is None
    assert plan["physical_operations"][0]["estimated_rows"] == 5


def test_pg_walks_every_child():
    """Un plan con tres hijos produce operaciones de los tres. Limitarse al
    primero deja los conteos descuadrados respecto de las operaciones."""
    plan = _pg_normalize({"query_id": 1, "plan": _pg_plan_tree("Nested Loop", [
        _pg_child("Seq Scan", Alias="a"),
        _pg_child("Seq Scan", Alias="b"),
        _pg_child("Seq Scan", Alias="c"),
    ])})
    assert plan["logical_shape"]["scans"] == 3
    assert len(plan["physical_operations"]) == 4
    relations = {
        op["relation"] for op in plan["physical_operations"] if op["relation"]
    }
    assert relations == {"a", "b", "c"}


def test_pg_processes_every_explain():
    """canonic_explains mantiene la correspondencia con query_explain. Procesar
    solo el primero deja al resto de los candidatos sin plan y sin avisar."""
    stats = {"query_explain": [
        {"query_id": 1, "plan": _pg_plan("Seq Scan", **{"Alias": "a"})},
        {"query_id": 2, "plan": _pg_plan("Seq Scan", **{"Alias": "b"})},
        {"query_id": 3, "plan": _pg_plan("Seq Scan", **{"Alias": "c"})},
    ]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert [e["query_id"] for e in stats["canonic_explains"]] == [1, 2, 3]
    relations = {
        e["canonical_plan"]["physical_operations"][0]["relation"]
        for e in stats["canonic_explains"]
    }
    assert relations == {"a", "b", "c"}


def test_pg_explain_source_is_carried_through():
    stats = {"query_explain": [{
        "query_id": 1,
        "plan": _pg_plan("Seq Scan"),
        "explain_source": "generic",
    }]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["explain_source"] == "generic"


# ---------- MySQL: access_type ----------

@pytest.mark.parametrize(
    "access_type,expected",
    [
        ("ALL", "full_table_scan"),
        ("index", "index_lookup"),
        ("range", "index_lookup"),
        ("ref", "index_lookup"),
        ("eq_ref", "index_lookup"),
        ("const", "index_lookup"),
        ("system", None),
    ],
)
def test_mysql_access_type_mapping(access_type, expected):
    """eq_ref es clave primaria o unicamente referenciada: es el acceso mas
    selectivo del plan. Clasificarla como otra cosa la hace parecer tan costosa
    como un recorrido completo.
    """
    plan = _mysql_normalize(_query_block(table=_mysql_node("t", access_type=access_type)))
    assert plan["physical_operations"][0]["access_method"] == expected
    assert plan["logical_shape"]["scans"] == 1


def test_mysql_unknown_access_type_is_not_guessed():
    """MySQL agrega modos de acceso con cada version. Uno desconocido queda en
    None y no inventado: el consumidor decide que hacer con la incertidumbre."""
    plan = _mysql_normalize(_query_block(table=_mysql_node("t", access_type="index_merge")))
    assert plan["physical_operations"][0]["access_method"] is None
    assert plan["logical_shape"]["scans"] == 1


# ---------- MySQL: claves de operacion ----------

@pytest.mark.parametrize(
    "key", ["subquery", "attached_subqueries", "dependent_subquery"]
)
def test_mysql_subquery_keys(key):
    """MySQL nombra las subconsultas de tres formas segun si estan pegadas al
    bloque, son independientes, o dependen de la fila externa. Reconocer solo
    una deja el conteo de subqueries en cero para las otras dos.
    """
    plan = _mysql_normalize(_query_block(
        table=_mysql_node("sbtest1"), **{key: {"table": _mysql_node("inner")}}
    ))
    assert plan["logical_shape"]["subqueries"] == 1
    assert len([op for op in plan["physical_operations"] if op["type"] == "subquery"]) == 1


@pytest.mark.parametrize(
    "key,field,operation_type",
    [
        ("grouping_operation", "aggregates", "aggregate"),
        ("ordering_operation", "sorts", "sort"),
        ("duplicates_removal", "distinct", "distinct"),
        ("nested_loop", "joins", "join"),
    ],
)
def test_mysql_operation_keys_are_counted(key, field, operation_type):
    plan = _mysql_normalize(_query_block(**{key: {"table": _mysql_node("t")}}))
    assert plan["logical_shape"][field] == 1
    assert operation_type in [op["type"] for op in plan["physical_operations"]]


@pytest.mark.parametrize(
    "key,field",
    [
        ("grouping_op", "aggregates"),
        ("ordering_op", "sorts"),
        ("duplicates_removal_x", "distinct"),
        ("NESTED_LOOP", "joins"),
        ("Subquery", "subqueries"),
    ],
)
def test_mysql_wrong_key_names_are_not_counted(key, field):
    """Sin este test, un clasificador con match parcial empezaria a sumar
    operaciones que el motor nunca emitio y los conteos inflarian solos.

    El scan del table anidado si se cuenta: MySQL si lo emite. Lo que no debe
    aparecer es la operacion del tipo que nombra la clave mala.
    """
    plan = _mysql_normalize(_query_block(**{key: {"table": _mysql_node("t")}}))
    assert plan["logical_shape"][field] == 0
    singular = {"aggregates": "aggregate", "sorts": "sort",
                "distinct": "distinct", "joins": "join", "subqueries": "subquery"}
    assert singular[field] not in [op["type"] for op in plan["physical_operations"]]


def test_mysql_table_key_with_non_dict_value():
    """'table' tambien aparece como nombre de columna dentro de other_keys.
    Contarlo sin verificar el dict produce una operacion scan vacia.
    """
    plan = _mysql_normalize(_query_block(other_keys={"table": "sbtest1"}))
    assert plan["logical_shape"]["scans"] == 0
    assert plan["physical_operations"] == []


def test_mysql_nested_recursion_finds_deep_tables():
    """MySQL anida la estructura del plan: un table puede estar a varios
    niveles. Si no se recorre en profundidad, las tablas internas se pierden."""
    plan = _mysql_normalize(_query_block(
        ordering_operation={
            "nested_loop": {"table": _mysql_node("inner")},
        },
        table=_mysql_node("outer"),
    ))
    assert plan["logical_shape"]["scans"] == 2
    assert plan["logical_shape"]["sorts"] == 1
    relations = {op["relation"] for op in plan["physical_operations"] if op["relation"]}
    assert relations == {"outer", "inner"}


def test_mysql_list_valued_children_are_walked():
    """attached_subqueries llega como lista de planes."""
    plan = _mysql_normalize(_query_block(
        attached_subqueries=[
            {"table": _mysql_node("s1")},
            {"table": _mysql_node("s2")},
        ],
    ))
    assert plan["logical_shape"]["subqueries"] == 1
    assert plan["logical_shape"]["scans"] == 2


# ---------- MySQL: estimates ----------

def test_mysql_query_cost_is_extracted():
    plan = _mysql_normalize({
        "query_block": {
            "cost_info": {"query_cost": "1234.5"},
            "table": _mysql_node("t"),
        }
    })
    assert plan["estimates"]["total_cost"] == 1234.5


def test_mysql_missing_cost_info_leaves_none():
    plan = _mysql_normalize(_query_block(table=_mysql_node("t")))
    assert plan["estimates"]["total_cost"] is None
    assert plan["logical_shape"]["scans"] == 1


# ---------- flags de MySQL ----------

@pytest.mark.parametrize(
    "flag,operation_type",
    [
        ("join_condition", "join"),
        ("using_temporary_table", "aggregate"),
        ("using_filesort", "sort"),
    ],
)
def test_mysql_flag_travels_as_predicate(flag, operation_type):
    """MySQL reporta estas caracteristicas como bool y el contrato solo tiene
    predicate: str | None. El nombre de la flag viaja ahi para que el consumidor
    sepa por que hay un disco o una tabla temporal.
    """
    key = {
        "join_condition": "nested_loop",
        "using_temporary_table": "grouping_operation",
        "using_filesort": "ordering_operation",
    }[flag]
    plan = _mysql_normalize(_query_block(**{key: {flag: True}}))
    by_type = {op["type"]: op for op in plan["physical_operations"]}
    assert by_type[operation_type]["predicate"] == flag


@pytest.mark.parametrize(
    "flag,key",
    [
        ("join_condition", "nested_loop"),
        ("using_temporary_table", "grouping_operation"),
        ("using_filesort", "ordering_operation"),
    ],
)
def test_mysql_false_flag_leaves_predicate_none(flag, key):
    """Una flag en False no es informacion: no debe viajar como texto ni
    distinguirse de que el campo no existiera."""
    plan = _mysql_normalize(_query_block(**{key: {flag: False}}))
    predicates = [op["predicate"] for op in plan["physical_operations"]]
    assert flag not in predicates
    assert None in predicates


def test_mysql_non_boolean_flag_value_is_preserved():
    """join_condition llega como texto de SQL, no como bool: ese valor si es
    informacion y no debe descartarse."""
    plan = _mysql_normalize(_query_block(
        nested_loop={"join_condition": "(`a`.`c` = `b`.`c`)"}
    ))
    join = [op for op in plan["physical_operations"] if op["type"] == "join"][0]
    assert join["predicate"] == "(`a`.`c` = `b`.`c`)"
