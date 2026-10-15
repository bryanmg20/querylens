"""Conversiones de tipo del predicado, iguales en los dos motores.

Los predicados son los de la bateria de ql_sandbox/explain_predicados ya
redactados por el pipeline. Los detectores reconocen una conversion solo por un
cast sobre la columna.
"""
import pytest

from stages.type_conversions import make_type_conversions_explicit

pytestmark = pytest.mark.unit

COLUMNS = [
    {"schema_name": "ql_demo", "table_name": "ql_pt_orders", "column_name": name, "data_type": data_type}
    for name, data_type in (
        ("id", "int"), ("customer_id", "int"), ("status", "varchar"), ("total", "decimal"),
        ("created_at", "datetime"), ("note", "text"), ("code", "char"),
    )
] + [
    {"schema_name": "public", "table_name": "ql_pt_orders", "column_name": name, "data_type": data_type}
    for name, data_type in (
        ("id", "integer"), ("customer_id", "integer"), ("status", "character varying"),
        ("total", "numeric"), ("note", "text"), ("code", "character"),
    )
] + [
    {"schema_name": "public", "table_name": "ql_pt_customers", "column_name": "email",
     "data_type": "character varying"},
]


def _run(dialect, predicate, canonic_query="SELECT id FROM ql_pt_orders WHERE x = $1",
         relation="ql_pt_orders", table_name=None):
    schema = "ql_demo" if dialect == "mysql" else "public"
    stats = {
        "columns": COLUMNS,
        "top_impact_queries": [{"query_id": 1, "schema_name": schema, "canonic_query": canonic_query}],
        "canonic_explains": [{
            "query_id": 1,
            "canonical_plan": {"physical_operations": [{
                "type": "scan", "relation": relation, "table_name": table_name, "predicate": predicate,
            }]},
        }],
    }
    make_type_conversions_explicit(stats, dialect)
    return stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]["predicate"]


@pytest.mark.parametrize(
    "predicate, expected",
    [
        # caso 15: char contra numero
        ("(ql_pt_orders.code = $1)", "(CAST(ql_pt_orders.code AS DOUBLE PRECISION) = $1)"),
        ("(ql_pt_orders.code IN ($1, '$2'))", "(CAST(ql_pt_orders.code AS DOUBLE PRECISION) IN ($1, '$2'))"),
        (
            "(ql_pt_orders.created_at BETWEEN $1 AND $2)",
            "(CAST(ql_pt_orders.created_at AS DOUBLE PRECISION) BETWEEN $1 AND $2)",
        ),
        # texto contra texto, numero contra numero, tipo desconocido: sin conversion
        ("(ql_pt_orders.code = '$1')", "(ql_pt_orders.code = '$1')"),
        ("(ql_pt_orders.total > $1)", "(ql_pt_orders.total > $1)"),
        ("(ql_pt_orders.unknown = $1)", "(ql_pt_orders.unknown = $1)"),
        # columna contra columna: no hay valor del que leer el tipo
        ("(ql_pt_orders.total > ql_pt_orders.id)", "(ql_pt_orders.total > ql_pt_orders.id)"),
        # el resto del predicado no cambia de forma
        (
            "((ql_pt_orders.status = '$1') OR (ql_pt_orders.code = $2))",
            "((ql_pt_orders.status = '$1') OR (CAST(ql_pt_orders.code AS DOUBLE PRECISION) = $2))",
        ),
    ],
)
def test_mysql_implicit_conversion_is_written(predicate, expected):
    assert _run("mysql", predicate) == expected


def test_mysql_alias_resolves_to_table():
    predicate = "(o.code = $1)"
    query = "SELECT id FROM ql_pt_orders AS o WHERE o.code = $1"
    assert _run("mysql", predicate, query, relation="o") == "(CAST(o.code AS DOUBLE PRECISION) = $1)"


@pytest.mark.parametrize(
    "predicate, expected",
    [
        # varchar: la coercion de Postgres no es una conversion
        ("((status)::text = $1)", "(status = $1)"),
        ("((status)::text = ANY ($1))", "(status = ANY ($1))"),
        ("((status)::text ~~ $1)", "(status ~~ $1)"),
        ("(((status)::text = $1) OR (total > $2))", "((status = $1) OR (total > $2))"),
        # char(n): el cast cambia el valor y si impide el indice
        ("((code)::text = $1)", "((code)::text = $1)"),
        # tipo desconocido: se asume varchar
        ("((unknown)::text = $1)", "(unknown = $1)"),
        # otros casts no se tocan
        ("((customer_id)::numeric = $1)", "((customer_id)::numeric = $1)"),
        ("((status)::text[] = $1)", "((status)::text[] = $1)"),
    ],
)
def test_postgres_varchar_text_cast_is_dropped(predicate, expected):
    assert _run("postgres", predicate, table_name="ql_pt_orders") == expected


def test_postgres_qualified_column_of_other_table():
    predicate = "(lower((c.email)::text) = $1)"
    query = "SELECT o.id FROM ql_pt_orders AS o JOIN ql_pt_customers AS c ON c.id = o.customer_id"
    assert _run("postgres", predicate, query, relation="c", table_name="ql_pt_customers") == "(lower(c.email) = $1)"


def test_without_columns_section_nothing_breaks():
    stats = {"canonic_explains": [{"query_id": 1, "canonical_plan": {"physical_operations": [
        {"type": "scan", "relation": "t", "predicate": "((status)::text = $1)"},
    ]}}]}
    make_type_conversions_explicit(stats, "postgres")
    assert stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]["predicate"] == "(status = $1)"
