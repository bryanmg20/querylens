"""Analiza pred_battery.json con los helpers reales de AP-03, AP-04 y AP-05.
Se corre con el venv de detection_engine, desde detection_engine/; ver README.md."""
import json
import sys

sys.path.insert(0, ".")
import sqlglot  # noqa: E402

from anti_patterns.missing_index import _filtered_columns  # noqa: E402
from anti_patterns.non_sargable_predicate import _detect_predicate_subtypes  # noqa: E402
from anti_patterns.table_aliases import resolve_table_aliases  # noqa: E402
from anti_patterns.unused_index import _predicate_columns  # noqa: E402

TYPES = {
    (None, "ql_pt_orders", "id"): "integer", (None, "ql_pt_orders", "customer_id"): "integer",
    (None, "ql_pt_orders", "status"): "character varying", (None, "ql_pt_orders", "total"): "numeric",
    (None, "ql_pt_orders", "created_at"): "timestamp without time zone", (None, "ql_pt_orders", "note"): "text",
    (None, "ql_pt_orders", "code"): "character", (None, "ql_pt_customers", "id"): "integer",
    (None, "ql_pt_customers", "email"): "character varying", (None, "ql_pt_customers", "name"): "character varying",
    (None, "ql_pt_customers", "region"): "integer",
}
TABLES = {"ql_pt_orders", "ql_pt_customers"}


def canonic(sql):
    # aproximacion del canonic_query del pipeline (dialecto postgres)
    try:
        return sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres")
    except Exception:
        return sql


def analyze(sql, scans):
    alias_map = resolve_table_aliases(canonic(sql))
    rows = []
    for op in scans:
        rel = op.get("relation")
        real = alias_map.get(rel, rel)
        pred = op.get("predicate")
        ap03 = _detect_predicate_subtypes(pred, real, None, TYPES) if pred else []

        def belongs(col, real=real):
            return not col.table or alias_map.get(col.table, col.table) == real

        ap04 = _filtered_columns(pred, belongs) if pred else ([], [])
        ap05 = sorted(_predicate_columns(pred)) if pred else []
        rows.append({"rel": rel, "real": real, "acc": op.get("access_method"), "est": op.get("estimated_rows"),
                     "pred": pred, "ap03": sorted(ap03), "ap04": ap04, "ap05": ap05})
    return rows


def lost_conditions(source, raw):
    notes = []
    for node in raw or []:
        if source.startswith("pg"):
            if "Filter" in node and "Index Cond" in node:
                notes.append(f"{node['rel']}: Index Cond {node['Index Cond']} PISA Filter {node['Filter']}")
            if "Recheck Cond" in node:
                notes.append(f"{node['rel']}: Recheck Cond {node['Recheck Cond']} no se copia ({node['node']})")
            if node["node"] == "Bitmap Index Scan":
                notes.append(f"Bitmap Index Scan sin relation (Index Cond {node.get('Index Cond')})")
            for k in ("Join Filter", "Hash Cond", "Merge Cond"):
                if k in node:
                    notes.append(f"{node['node']} {k} {node[k]} (va al join, no al scan)")
        else:
            if "index_condition" in node:
                notes.append(f"{node['rel']}: index_condition {node['index_condition']} NO se copia")
            if node.get("access_type") not in ("ALL", "index", "range", "const", "eq_ref", "ref"):
                notes.append(f"{node['rel']}: access_type {node.get('access_type')} -> access_method None")
            if node.get("access_type") == "index":
                notes.append(f"{node['rel']}: access_type index (recorre TODO el indice) -> index_lookup")
    return notes


def main():
    cases = json.load(open(sys.argv[1], encoding="utf-8"))
    for case in cases:
        print("=" * 110)
        print(f"{case['name']}:  {case['sql']}")
        for source in ("pg_generic", "pg_literal", "mysql"):
            data = case.get(source)
            if data is None:
                continue
            if "error" in data:
                print(f"  [{source}] ERROR: {data['error']}")
                continue
            for row in analyze(case["sql"], data["scans"]):
                print(f"  [{source:<10}] {str(row['rel']):<9} {str(row['acc']):<16} est={str(row['est']):<7} pred={row['pred']}")
                print(f"  {'':<12} AP-03={row['ap03']}  AP-04 igualdad/rango={row['ap04']}  AP-05={row['ap05']}")
            for note in lost_conditions(source, data.get("raw")):
                print(f"  [{source:<10}] ! {note}")


if __name__ == "__main__":
    main()
