"""Variantes del plan generico de Postgres con valores de pg_stats
(stages/plan_variants.py) y su paso por ExplainStage y el normalizer."""
import json
import logging

import pytest
from sqlalchemy import create_engine

from health.report import HealthReport
from models.snapshot import CanonicExplain
from stages import plan_variants as pv
from stages.explain import ExplainStage

pytestmark = pytest.mark.unit

PG_ENGINE = create_engine("postgresql+psycopg2://ql_user:ql_pass@localhost:5432/ql_demo")

# Valores "reales" de pg_stats: no pueden aparecer en el snapshot, en el texto
# SQL enviado ni en el log.
FREQUENT = "valor_frecuente_secreto"
RARE = "valor_raro_secreto"


def _node(node_type="Seq Scan", relation="pedidos", **extra):
    node = {"Node Type": node_type, "Plan Rows": 1, "Total Cost": 1.0}
    if relation:
        node["Relation Name"] = relation
        node["Alias"] = extra.pop("alias", relation)
    node.update(extra)
    return node


# ---------- mapeo $n -> columna ----------


@pytest.mark.parametrize("condition, expected", [
    ("(k > $1)", {1: pv.ParamRef("pedidos", "k", ">")}),
    ("((estado)::text = $1)", {1: pv.ParamRef("pedidos", "estado", "=")}),
    ("($1 < k)", {1: pv.ParamRef("pedidos", "k", ">")}),
    ("((created_at)::timestamp without time zone >= $1)",
     {1: pv.ParamRef("pedidos", "created_at", ">=")}),
])
def test_param_columns_maps_single_param(condition, expected):
    root = _node(Filter=condition)
    assert pv.param_columns(root, [1]) == expected


def test_param_columns_maps_a_range_with_two_params():
    root = _node(**{"Index Cond": "((id >= $1) AND (id <= $2))"})
    assert pv.param_columns(root, [1, 2]) == {
        1: pv.ParamRef("pedidos", "id", ">="),
        2: pv.ParamRef("pedidos", "id", "<="),
    }


def test_param_columns_resolves_alias_to_relation():
    root = _node("Nested Loop", relation=None, Plans=[
        _node(relation="sbtest1", alias="a", Filter="(a.k > $1)"),
        _node("Index Scan", relation="sbtest1", alias="b", **{"Index Cond": "(id = a.id)"}),
    ])
    assert pv.param_columns(root, [1]) == {1: pv.ParamRef("sbtest1", "k", ">")}


def test_bitmap_index_scan_takes_the_relation_of_its_heap_scan():
    root = _node("Bitmap Heap Scan", Plans=[
        _node("Bitmap Index Scan", relation=None, **{"Index Cond": "(k = $1)"}),
    ])
    assert pv.param_columns(root, [1]) == {1: pv.ParamRef("pedidos", "k", "=")}


@pytest.mark.parametrize("condition", [
    "(c ~~ $1)",                        # LIKE: el valor de prueba no sale de pg_stats
    "(id = ANY (ARRAY[$1, $2]))",       # IN
    "(lower(email) = $1)",              # columna dentro de una funcion
    "(k > ($1 * 2))",                   # el $n no es el valor completo
    "(k <> $1)",
    "(x.k > $1)",                       # alias que no es de ningun scan
])
def test_param_columns_rejects_unmappable_conditions(condition):
    assert pv.param_columns(_node(Filter=condition), [1]) is None


def test_param_columns_rejects_a_param_missing_from_the_plan():
    """LIMIT $2 no aparece en ninguna condicion: sin valor para $2 no hay EXECUTE."""
    assert pv.param_columns(_node(Filter="(k > $1)"), [1, 2]) is None


def test_param_columns_rejects_a_param_bound_to_two_columns():
    root = _node(Filter="((k > $1) AND (id < $1))")
    assert pv.param_columns(root, [1]) is None


def test_query_params_requires_contiguous_numbers():
    assert pv.query_params("SELECT * FROM t WHERE a = $1 AND b > $2") == [1, 2]
    assert pv.query_params("SELECT * FROM t WHERE a = $2") is None
    assert pv.query_params("SELECT 1") == []
    # Un $1 dentro de un string no es un parametro.
    assert pv.query_params("SELECT '$1' FROM t WHERE a = $1") == [1]


# ---------- eleccion de valores ----------


def test_equality_uses_most_common_and_a_histogram_value():
    stats = pv.ColumnStats(mcv=["a", "b"], hist=["c", "d", "e"])
    assert pv.pick_values("=", stats) == pv.Pick("a", "d", True)


def test_equality_without_histogram_uses_the_least_common_mcv():
    stats = pv.ColumnStats(mcv=["a", "b", "c"], hist=[])
    assert pv.pick_values("=", stats) == pv.Pick("a", "c", True)


def test_equality_without_mcv_is_stable():
    stats = pv.ColumnStats(mcv=[], hist=["1", "500", "1000"])
    assert pv.pick_values("=", stats) == pv.Pick("500", "500", False)


def test_equality_without_any_value_cannot_pick():
    assert pv.pick_values("=", pv.ColumnStats()) is None


@pytest.mark.parametrize("op, expected", [
    (">", pv.Pick("1", "1000", True)),
    (">=", pv.Pick("1", "1000", True)),
    ("<", pv.Pick("1000", "1", True)),
    ("<=", pv.Pick("1000", "1", True)),
])
def test_ranges_use_the_histogram_ends(op, expected):
    stats = pv.ColumnStats(mcv=["7"], hist=["1", "500", "1000"])
    assert pv.pick_values(op, stats) == expected


def test_range_without_histogram_cannot_pick():
    assert pv.pick_values(">", pv.ColumnStats(mcv=["1", "2"], hist=[])) is None


# ---------- anonimizacion de predicados de las variantes ----------

@pytest.mark.parametrize("predicate, expected", [
    ("((estado)::text = 'pendiente'::text)", "((estado)::text = $1)"),
    ("(monto >= 500)", "(monto >= $1)"),
    ("(500 <= monto)", "($1 <= monto)"),
    # Numeracion por predicado y en orden de lectura, como en MySQL (N-1).
    ("(((estado)::text = 'x'::text) AND (monto >= 500))",
     "(((estado)::text = $1) AND (monto >= $2))"),
    ("((monto >= 500) AND ((estado)::text = 'x'::text))",
     "((monto >= $1) AND ((estado)::text = $2))"),
    ("((estado)::text = 'O''Brien'::text)", "((estado)::text = $1)"),
    ("(monto >= '-5'::integer)", "(monto >= $1)"),
    ("(monto >= 1.5e3)", "(monto >= $1)"),
    ("(created_at >= '2024-01-01 00:00:00'::timestamp without time zone)", "(created_at >= $1)"),
    ("(lower(estado) = 'secreto'::text)", "(lower(estado) = $1)"),
])
def test_variant_literals_are_numbered_in_reading_order(predicate, expected):
    assert pv.anonymize_predicate(predicate) == expected


def test_structure_without_literals_is_untouched():
    for predicate in ("(sbtest1.id = (SubPlan 1))", "(b.id = a.id)", "(sbtest1 = t2)", None):
        assert pv.anonymize_predicate(predicate) == predicate


# ---------- ExplainStage + normalizer ----------

_GENERIC = [{"QUERY PLAN": [{"Plan": _node(
    **{"Plan Rows": 333333, "Filter": "((estado)::text = $1)"},
)}]}]
_MANY = [{"QUERY PLAN": [{"Plan": _node(
    **{"Plan Rows": 950000, "Filter": f"((estado)::text = '{FREQUENT}'::text)"},
)}]}]
_FEW = [{"QUERY PLAN": [{"Plan": _node(
    "Index Scan",
    **{"Plan Rows": 10000, "Index Name": "pedidos_estado_idx",
       "Index Cond": f"((estado)::text = '{RARE}'::text)"},
)}]}]
_STATS_ROW = {"resolved": True, "has_stats": True, "mcv": [FREQUENT, RARE], "hist": []}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Tx:
    def __init__(self, conn, nested=False):
        self._conn = conn
        self._nested = nested

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, *exc):
        if self._nested:
            self._conn.events.append("release" if exc_type is None else "rollback_to_savepoint")
        elif exc_type is None:
            self._conn.events.append("commit")
        else:
            self._conn.events.append("rollback")


class _Conn:
    def __init__(self, stats_row=_STATS_ROW, execute_error=None):
        self.sent = []
        self.events = []
        self.bound = []
        self._stats_row = stats_row
        self._execute_error = execute_error

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        self.events.append(sql)
        params = stmt.compile().params
        self.bound.append(params)
        if sql.startswith("EXPLAIN (GENERIC_PLAN"):
            return _Result(_GENERIC)
        if "pg_stats" in sql:
            return _Result([] if self._stats_row is None else [self._stats_row])
        if "EXECUTE ql_plan_variant" in sql:
            if self._execute_error is not None:
                raise self._execute_error
            return _Result(_MANY if params["p1"] == FREQUENT else _FEW)
        return _Result([])

    def begin(self):
        return _Tx(self)

    def begin_nested(self):
        return _Tx(self, nested=True)

    def close(self):
        pass


class _Collector:
    source_dialect = "postgres"
    engine = PG_ENGINE


def _stats():
    return {"top_impact_queries": [{
        "query_id": 1,
        "query_text": "SELECT * FROM pedidos WHERE estado = $1",
        "schema_name": "public",
        "ready_for_explain": True,
    }]}


def _run(conn, report=None):
    report = report if report is not None else HealthReport()
    stats = ExplainStage(_Collector(), report).execute(_stats(), conn)
    return stats["canonic_explains"][0], report


def test_sensitive_column_gets_two_variants_and_sensitive_stability():
    conn = _Conn()
    explain, _ = _run(conn)

    assert explain["explain_source"] == "generic"
    assert explain["canonical_plan"]["estimates"]["output_rows"] == 333333
    assert explain["plan_stability"] == "sensitive"
    assert [v["label"] for v in explain["plan_variants"]] == ["many_rows", "few_rows"]
    rows = [v["canonical_plan"]["estimates"]["output_rows"] for v in explain["plan_variants"]]
    assert rows == [950000, 10000]
    methods = [
        v["canonical_plan"]["physical_operations"][0]["access_method"]
        for v in explain["plan_variants"]
    ]
    assert methods == ["full_table_scan", "index_lookup"]
    CanonicExplain.model_validate(explain)


def test_variant_values_never_reach_the_snapshot_or_the_sql_text():
    conn = _Conn()
    explain, _ = _run(conn)

    payload = json.dumps(explain)
    assert FREQUENT not in payload and RARE not in payload
    # Cada nodo conserva su condicion, con el valor devuelto a su $n.
    predicates = [
        variant["canonical_plan"]["physical_operations"][0]["predicate"]
        for variant in explain["plan_variants"]
    ]
    assert predicates == ["((estado)::text = $1)", "((estado)::text = $1)"]
    # Los valores viajan como bind params, no pegados en el SQL.
    assert not any(FREQUENT in sql or RARE in sql for sql in conn.sent)
    assert {"p1": FREQUENT} in conn.bound and {"p1": RARE} in conn.bound


def test_prepare_runs_in_a_savepoint_and_is_always_deallocated():
    conn = _Conn()
    _run(conn)

    prepare = next(i for i, s in enumerate(conn.sent) if s.startswith("PREPARE ql_plan_variant AS"))
    assert conn.sent[prepare - 1] == "SET LOCAL plan_cache_mode = force_custom_plan"
    assert conn.sent[-1] == "DEALLOCATE ql_plan_variant"
    assert conn.sent[prepare].endswith("SELECT * FROM pedidos WHERE estado = $1")


def test_variant_failure_keeps_the_generic_plan_and_logs_no_value(caplog):
    error = RuntimeError(f'invalid input syntax for type integer: "{FREQUENT}"')
    conn = _Conn(execute_error=error)
    report = HealthReport()

    with caplog.at_level(logging.WARNING):
        explain, report = _run(conn, report)

    assert explain["canonical_plan"]["estimates"]["output_rows"] == 333333
    assert "plan_variants" not in explain and "plan_stability" not in explain
    assert not report.issues
    assert "DEALLOCATE ql_plan_variant" in conn.sent
    assert "rollback_to_savepoint" in conn.events
    assert "EXPLAIN variants" in caplog.text and "RuntimeError" in caplog.text
    assert FREQUENT not in caplog.text


def test_column_without_frequent_values_is_stable_without_variants():
    conn = _Conn(stats_row={**_STATS_ROW, "mcv": [], "hist": ["1", "2", "3"]})
    explain, _ = _run(conn)

    assert explain["plan_stability"] == "stable"
    assert "plan_variants" not in explain
    assert not any("PREPARE" in sql for sql in conn.sent)


def test_table_without_statistics_falls_back_to_generic_and_reports_it():
    conn = _Conn(stats_row={"resolved": True, "has_stats": False, "mcv": None, "hist": None})
    explain, report = _run(conn)

    assert set(explain) == {"query_id", "explain_source", "canonical_plan"}
    assert report.issues == {("PG_STATS_MISSING", ""): {"count": 1}}
    assert not any("PREPARE" in sql for sql in conn.sent)


def test_unresolved_relation_falls_back_to_generic_silently():
    conn = _Conn(stats_row={"resolved": False, "has_stats": False, "mcv": None, "hist": None})
    explain, report = _run(conn)

    assert set(explain) == {"query_id", "explain_source", "canonical_plan"}
    assert not report.issues


def test_unmappable_query_does_not_touch_pg_stats():
    conn = _Conn()
    stats = _stats()
    stats["top_impact_queries"][0]["query_text"] = "SELECT * FROM pedidos WHERE estado = $1 LIMIT $2"
    ExplainStage(_Collector(), HealthReport()).execute(stats, conn)

    assert not any("pg_stats" in sql for sql in conn.sent)
    assert len([s for s in conn.sent if s.startswith("SET LOCAL search_path")]) == 1


def test_snapshot_defaults_when_no_variants():
    explain = CanonicExplain.model_validate({
        "query_id": 1, "explain_source": "sample", "canonical_plan": {},
    })
    assert explain.plan_stability is None
    assert explain.plan_variants == []
