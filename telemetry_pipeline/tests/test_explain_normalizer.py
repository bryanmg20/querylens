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


def _pg_plan_tree(root_type, children):
    root = {
        "Node Type": root_type,
        "Plan Rows": 1,
        "Total Cost": 1.0,
        "Plans": children,
    }
    return [{"Plan": {"Plan": root}}]


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


def test_pg_output_rows_from_root():
    # las filas del nodo raiz (el join), no las de cada scan
    stats = {"query_explain": [{"query_id": 1, "plan": [{"Plan": {
        "Node Type": "Hash Join", "Plan Rows": 5000, "Total Cost": 1.0, "Plans": [
            {"Node Type": "Seq Scan", "Alias": "p", "Plan Rows": 100000, "Total Cost": 1.0, "Plans": []},
            {"Node Type": "Seq Scan", "Alias": "c", "Plan Rows": 50, "Total Cost": 1.0, "Plans": []},
        ],
    }}]}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 5000


def test_pg_empty_plan():
    stats = {"query_explain": [{"query_id": 1, "plan": []}]}
    EXPLAIN_NORMALIZERS["postgres"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["logical_shape"]["scans"] == 0


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

def test_mysql_output_rows_single_table():
    stats = {"query_explain": [{"query_id": 7, "plan": _mysql_table(
        _mysql_table_node("t", rows_produced_per_join=100)
    )}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 100


def test_mysql_output_rows_last_access_of_join():
    # forma real de MySQL 8.0: nested_loop dentro de ordering_operation
    stats = {"query_explain": [{"query_id": 8, "plan": {
        "query_block": {
            "cost_info": {"query_cost": "1"},
            "ordering_operation": {
                "using_filesort": True,
                "nested_loop": [
                    {"table": _mysql_table_node("c", rows_produced_per_join=50)},
                    {"table": _mysql_table_node("p", access_type="ref", rows_produced_per_join=5000)},
                ],
            },
        }
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] == 5000


def test_mysql_output_rows_union_is_none():
    stats = {"query_explain": [{"query_id": 9, "plan": {
        "query_block": {"union_result": {"query_specifications": []}}
    }}]}
    EXPLAIN_NORMALIZERS["mysql"]().normalize(stats)
    assert stats["canonic_explains"][0]["canonical_plan"]["estimates"]["output_rows"] is None
