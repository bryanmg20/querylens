import pytest

from stages.explain_normalizer import EXPLAIN_NORMALIZERS

pytestmark = pytest.mark.unit


# ---------- Postgres fixtures ----------

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


# ---------- MySQL fixtures ----------

def _mysql_table(plan_table, query_cost=10.5):
    return {
        "query_block": {
            "cost_info": {"query_cost": str(query_cost)},
            "table": plan_table,
        }
    }


def _mysql_table_node(name, access_type="ALL", **extra):
    base = {
        "table_name": name,
        "access_type": access_type,
        "rows_produced_per_join": 50,
    }
    base.update(extra)
    return base


# ---------- Postgres tests ----------

def test_pg_scan_counted():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan("Seq Scan", **{"Alias": "t"})}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"]["scans"] == 1
    assert plan["physical_operations"][0]["access_method"] == "full_table_scan"


def test_pg_joins_and_sorts():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan_tree("Hash Join", [
        {"Node Type": "Seq Scan", "Alias": "a", "Plan Rows": 1, "Total Cost": 1.0, "Plans": [],
         "Hash Cond": "a.id = b.id"},
        {"Node Type": "Sort", "Plan Rows": 1, "Total Cost": 1.0, "Plans": []},
    ])}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    shape = stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]
    assert shape["joins"] == 1
    assert shape["sorts"] == 1


def test_pg_total_cost():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan("Seq Scan")}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["total_cost"] == 25.5


def test_pg_output_rows_from_root_plan():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan("Seq Scan", **{"Plan Rows": 42})}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 42


def test_pg_output_rows_none_when_missing():
    stats = {"query_explain": [{"query_id": 1, "plan": [{"Plan": {
        "Node Type": "Seq Scan", "Total Cost": 1.0, "Plans": [],
    }}]}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] is None


def test_pg_empty_plan():
    stats = {"query_explain": [{"query_id": 1, "plan": []}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]["scans"] == 0


def test_pg_counts_aggregates_and_sorts():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan_tree("Aggregate", [
        _pg_child("Sort"),
        _pg_child("Seq Scan", Alias="t"),
    ])}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    shape = stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]
    assert shape["aggregates"] == 1
    assert shape["sorts"] == 1


def test_pg_counts_subqueries_and_distinct():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan_tree("Unique", [
        _pg_child("Subquery Scan", **{"Parent Relationship": "SubPlan"}),
        _pg_child("Seq Scan", Alias="t"),
    ])}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    shape = stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]
    assert shape["distinct"] == 1
    assert shape["subqueries"] == 1


def test_pg_join_predicate_is_copied_into_the_operation():
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan_tree("Nested Loop", [
        _pg_child("Seq Scan", Alias="a"),
        _pg_child("Seq Scan", Alias="b"),
    ], **{"Hash Cond": "a.id = b.id"})}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    operations = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    joins = [o for o in operations if o["type"] == "join"]
    assert joins and joins[0]["predicate"] == "a.id = b.id"


def test_pg_unparsable_plan_yields_empty_shape():
    stats = {"query_explain": [{"query_id": 1, "plan": "{no json"}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"] == {
        "scans": 0, "joins": 0, "aggregates": 0, "sorts": 0, "subqueries": 0, "distinct": 0,
    }
    assert plan["physical_operations"] == []


@pytest.mark.parametrize(
    "root_type,children,expected_type",
    [
        ("Aggregate", [_pg_child("Seq Scan", Alias="t")], "aggregate"),
        ("Sort", [_pg_child("Seq Scan", Alias="t")], "sort"),
        ("Unique", [_pg_child("Seq Scan", Alias="t")], "distinct"),
        ("Subquery Scan", [_pg_child("Seq Scan", Alias="t")], "subquery"),
        ("Nested Loop", [_pg_child("Seq Scan", Alias="a")], "join"),
    ],
)
def test_pg_every_operation_type_reaches_physical_operations(
    root_type, children, expected_type
):
    """Todo nodo contabilizado en LogicalShape debe llegar tambien a
    physical_operations, que es lo que declara el Literal de models/snapshot.py."""
    stats = {"query_explain": [{"query_id": 1, "plan": _pg_plan_tree(root_type, children)}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    types = [o["type"] for o in plan["physical_operations"]]
    assert expected_type in types


# ---------- MySQL tests ----------

def test_mysql_scan_counted():
    stats = {"query_explain": [{"query_id": 2, "plan": _mysql_table(_mysql_table_node("sbtest1"))}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"]["scans"] == 1
    assert plan["physical_operations"][0]["access_method"] == "full_table_scan"


def test_mysql_full_table_scan():
    stats = {"query_explain": [{"query_id": 3, "plan": _mysql_table(_mysql_table_node("t", access_type="ALL"))}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]["access_method"] == "full_table_scan"


def test_mysql_total_cost():
    stats = {"query_explain": [{"query_id": 4, "plan": _mysql_table(_mysql_table_node("t"), query_cost=42.0)}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["total_cost"] == 42.0


def test_mysql_output_rows_from_single_table():
    stats = {"query_explain": [{"query_id": 18, "plan": _mysql_table(
        _mysql_table_node("t", rows_produced_per_join=123)
    )}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 123


def test_mysql_output_rows_from_nested_loop_last_table():
    stats = {"query_explain": [{"query_id": 19, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "nested_loop": [
                {"table": _mysql_table_node("a", rows_produced_per_join=10)},
                {"table": _mysql_table_node("b", rows_produced_per_join=20)},
                {"table": _mysql_table_node("c", rows_produced_per_join=30)},
            ],
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 30


def test_mysql_output_rows_from_ordering_operation_wrapper():
    stats = {"query_explain": [{"query_id": 20, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "ordering_operation": {
                "using_filesort": True,
                "nested_loop": [
                    {"table": _mysql_table_node("a", rows_produced_per_join=10)},
                    {"table": _mysql_table_node("b", rows_produced_per_join=20)},
                ],
            },
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 20


def test_mysql_output_rows_none_when_missing():
    stats = {"query_explain": [{"query_id": 21, "plan": {
        "query_block": {"cost_info": {"query_cost": "1"}}
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] is None


def test_mysql_nested_loop():
    stats = {"query_explain": [{"query_id": 5, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "nested_loop": [_mysql_table(_mysql_table_node("a")), _mysql_table(_mysql_table_node("b"))],
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    shape = stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]
    assert shape["joins"] == 1
    assert shape["scans"] == 2


def test_mysql_sort():
    stats = {"query_explain": [{"query_id": 6, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "ordering_operation": {},
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]["sorts"] == 1


def test_mysql_index_access_type_maps_to_index_lookup():
    stats = {"query_explain": [{"query_id": 8, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "table": _mysql_table_node("t", access_type="ref", key="idx_t", attached_condition="`t`.`k` = 1"),
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    operation = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]
    assert operation["access_method"] == "index_lookup"
    assert operation["index_name"] == "idx_t"
    assert operation["predicate"] == "`t`.`k` = 1"


def test_mysql_unknown_access_type_leaves_access_method_none():
    stats = {"query_explain": [{"query_id": 9, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "table": _mysql_table_node("t", access_type="Materialize"),
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    operation = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]
    assert operation["access_method"] is None


def test_mysql_plan_without_cost_info_keeps_none():
    stats = {"query_explain": [{"query_id": 10, "plan": {
        "query_block": {"table": _mysql_table_node("t")}
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["estimates"]["total_cost"] is None
    assert plan["logical_shape"]["scans"] == 1


def test_mysql_empty_plan_does_not_crash():
    stats = {"query_explain": [{"query_id": 11, "plan": {}}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"]["scans"] == 0
    assert plan["physical_operations"] == []


def test_explain_source_is_preserved_from_the_stage():
    stats = {"query_explain": [
        {"query_id": 1, "explain_source": "generic", "plan": _pg_plan("Seq Scan", **{"Alias": "t"})},
        {"query_id": 2, "explain_source": "sample", "plan": _mysql_table(_mysql_table_node("t"))},
    ]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert {e["query_id"]: e["explain_source"] for e in stats["canonic_explains"]} == {
        1: "generic", 2: "sample",
    }


@pytest.mark.parametrize(
    "block,expected_type",
    [
        ({"nested_loop": [{"table": _mysql_table_node("a")}]}, "join"),
        ({"grouping_operation": {}}, "aggregate"),
        ({"ordering_operation": {}}, "sort"),
        ({"duplicates_removal": {}}, "distinct"),
    ],
)
def test_mysql_every_operation_type_reaches_physical_operations(block, expected_type):
    stats = {"query_explain": [{"query_id": 12, "plan": {
        "query_block": dict({"cost_info": {"query_cost": "1"}}, **block)
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    types = [o["type"] for o in stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]]
    assert expected_type in types


def test_mysql_mysql_only_flags_travel_as_predicate():
    """MySQL reporta estas caracteristicas como bool; el snapshot solo tiene
    `predicate: str | None`, asi que el nombre de la flag viaja ahi."""
    stats = {"query_explain": [{"query_id": 13, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "ordering_operation": {"using_filesort": True},
            "grouping_operation": {"using_temporary_table": True},
            "nested_loop": {"join_condition": "a.c = b.c"},
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    ops = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    by_type = {o["type"]: o for o in ops}
    assert by_type["sort"]["predicate"] == "using_filesort"
    assert by_type["aggregate"]["predicate"] == "using_temporary_table"
    assert by_type["join"]["predicate"] == "a.c = b.c"


def test_mysql_false_flag_leaves_predicate_empty():
    stats = {"query_explain": [{"query_id": 14, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "ordering_operation": {"using_filesort": False},
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    ops = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    assert [o for o in ops if o["type"] == "sort"][0]["predicate"] is None


def test_pg_join_without_predicate_still_reaches_operations():
    """Antes un join sin predicado se descartaba en silencio y su contador de
    LogicalShape no cuadria con el numero de operaciones del snapshot."""
    stats = {"query_explain": [{"query_id": 15, "plan": _pg_plan_tree(
        "Nested Loop", [_pg_child("Seq Scan", Alias="a"), _pg_child("Seq Scan", Alias="b")]
    )}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"]["joins"] == 1
    assert [o for o in plan["physical_operations"] if o["type"] == "join"][0][
        "predicate"
    ] is None


def test_pg_subquery_scan_emits_both_operations():
    """Un scan que es SubPlan cuenta en scans y en subqueries, asi que debe
    aparecer dos veces en physical_operations: con type 'scan' y con 'subquery'."""
    stats = {"query_explain": [{"query_id": 17, "plan": {
        "Node Type": "Aggregate",
        "Plan Rows": 1,
        "Plans": [{
            "Node Type": "Seq Scan",
            "Parent Relationship": "SubPlan",
            "Subplan Name": "SubPlan 1",
            "Alias": "a",
            "Plan Rows": 3,
        }],
    }}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    plan = stats["canonic_explains"][0]["canonical_plan"]
    assert plan["logical_shape"]["scans"] == 1
    assert plan["logical_shape"]["subqueries"] == 1
    assert [o["type"] for o in plan["physical_operations"]] == ["aggregate", "scan", "subquery"]


def test_shape_counters_always_match_physical_operations():
    """Invariante: cada contador de LogicalShape tiene su tipo en operations."""
    stats = {"query_explain": [{"query_id": 16, "plan": _pg_plan_tree(
        "Aggregate",
        [
            _pg_child("Sort", **{"Plan Rows": 5, "Sort Key": ["c"], "Plans": [
                _pg_child("Nested Loop", **{
                    "Hash Cond": "(a.id = b.id)",
                    "Plans": [
                        _pg_child("Seq Scan", **{"Alias": "a", "Plan Rows": 3}),
                        _pg_child("Index Scan", **{"Alias": "b", "Index Name": "idx_b"}),
                    ],
                }),
            ]}),
            _pg_child("Unique", **{"Plan Rows": 1}),
        ],
    )}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    shape = stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]
    assert shape == {
        "scans": 2, "joins": 1, "aggregates": 1, "sorts": 1, "subqueries": 0, "distinct": 1,
    }
    ops = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    assert sorted(o["type"] for o in ops) == [
        "aggregate", "distinct", "join", "scan", "scan", "sort",
    ]