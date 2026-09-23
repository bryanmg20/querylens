import json
import os
import statistics
import subprocess
import sys
from collections import Counter

import sqlglot
from sqlglot import exp
from sqlalchemy import text

PIPELINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE)
from config.connections import get_connection_querylens_db

SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 10
REPS = sys.argv[2] if len(sys.argv) > 2 else "30"
PY = os.path.join(PIPELINE, "venv", "Scripts", "python.exe")

NUMERIC_HINTS = ("int", "float", "decimal", "numeric", "double", "real", "smallserial", "bigserial")
STRING_HINTS = ("char", "text", "varchar", "enum")


def op_of(query_text):
    head = (query_text or "").lstrip().split(None, 1)[0].upper()
    if head in ("SELECT", "UPDATE", "INSERT", "DELETE", "WITH", "REPLACE", "MERGE"):
        return head
    return "OTHER"


def engine_of(payload):
    for c in payload.get("top_impact_queries") or []:
        if isinstance(c.get("query_id"), int):
            return "postgres"
        if isinstance(c.get("query_id"), str):
            return "mysql"
    return "unknown"


def long_types(col_types, table, col):
    cands = [(table, col)]
    if table:
        cands.append((None, col))
    for t, c in cands:
        if (t, c) in col_types:
            return col_types[(t, c)]
    return None


def plausible_issues(query_text, col_types, dialect):
    """Devuelve lista de literales inverosimiles: comparaciones 'columna numerica' vs literal string."""
    if not query_text:
        return []
    norm = query_text.replace(", ...", ", 0")
    try:
        ast = sqlglot.parse_one(norm, read=dialect)
    except Exception:
        return ["query_no_verificable"]

    aliases = {}
    tables = {t for (t, _) in col_types}
    for node in ast.walk():
        if isinstance(node, exp.From):
            src = node.this
            if isinstance(src, exp.Table):
                aliases[src.alias or src.name] = src.name
        elif isinstance(node, exp.Join):
            src = node.this
            if isinstance(src, exp.Table):
                aliases[src.alias or src.name] = src.name

    def resolve_table(alias):
        return aliases.get(alias, alias)

    issues = []

    def check(col, lit):
        if not isinstance(col, exp.Column) or not isinstance(lit, exp.Literal):
            return
        if not lit.is_string:
            return
        try:
            ctype = long_types(col_types, resolve_table(col.table), col.name)
        except Exception:
            return
        if ctype is None and len(tables) == 1:
            ctype = long_types(col_types, next(iter(tables)), col.name)
        if not ctype:
            return
        low = ctype.lower()
        if any(h in low for h in NUMERIC_HINTS) and lit.this.lower() != "null":
            issues.append((f"{col.name}={lit.this}", ctype))

    for node in ast.walk():
        if isinstance(node, exp.Binary):  # =, >, <, <=, >=, !=
            if isinstance(node.left, exp.Column) and isinstance(node.right, exp.Literal):
                check(node.left, node.right)
            elif isinstance(node.right, exp.Column) and isinstance(node.left, exp.Literal):
                check(node.right, node.left)
        elif isinstance(node, exp.Between):
            if isinstance(node.this, exp.Column):
                for bound in (node.args.get("low"), node.args.get("high")):
                    check(node.this, bound)
        elif isinstance(node, exp.Like) and isinstance(node.this, exp.Column):
            check(node.this, node.args.get("expression"))
    return issues


def engine_stats():
    return {"samples": [], "op": {}, "issues": [], "misses": [], "expl": 0}


def run_sample(i):
    agg = {}
    subprocess.run(["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", REPS], check=True)
    subprocess.run([PY, os.path.join(PIPELINE, "main.py")], check=True)

    with get_connection_querylens_db().connect() as conn:
        rows = conn.execute(
            text("SELECT msg_id, message FROM pgmq.q_analyze_job ORDER BY msg_id DESC LIMIT 2")
        ).mappings().all()

    print(f"\n=== Muestra {i + 1}/{SAMPLES} ===", flush=True)
    for r in rows:
        payload = r["message"]
        if isinstance(payload, (str, bytes, bytearray)):
            payload = json.loads(payload)

        e = engine_of(payload)
        colmap = {(c["table_name"], c["column_name"]): c["data_type"]
                  for c in payload.get("columns") or [] if c.get("table_name")}
        dialect = "postgres" if e == "postgres" else "mysql"
        st = agg.setdefault(e, engine_stats())

        total = found = 0
        for cand in payload.get("top_impact_queries") or []:
            total += 1
            q = cand.get("query_text") or ""
            op = op_of(q)
            o = st["op"].setdefault(op, {"total": 0, "found": 0})
            o["total"] += 1
            if cand.get("real_query_found"):
                found += 1
                o["found"] += 1
                iss = plausible_issues(q, colmap, dialect)
                if iss:
                    st["issues"].append((i + 1, cand.get("query_id"), op, iss))
            else:
                st["misses"].append((op, q.replace("\n", " ")[:70]))

        explains = len(payload.get("canonic_explains") or [])
        st["expl"] += explains
        st["samples"].append((total, found, explains))
        print(f"  msg_id={r['msg_id']} {e}: found={found}/{total} "
              f"({100 * found / max(1, total):.0f}%) explains={explains}", flush=True)

    return agg


def print_final(agg):
    print("\n" + "=" * 72)
    print(f"REPORTE FINAL  ({SAMPLES} muestras x {REPS} reps)")
    print("=" * 72)
    for e, st in agg.items():
        if not st["samples"]:
            continue
        print(f"\n-- {e} --")
        rates = [100 * f / max(1, t) for t, f, _ in st["samples"]]
        totals = sum(t for t, _, _ in st["samples"])
        founds = sum(f for _, f, _ in st["samples"])
        print("  1) VARIANZA entre muestras")
        print(f"     por muestra: {rates}")
        print(f"     mean={statistics.mean(rates):.1f}%  min={min(rates):.0f}%  "
              f"max={max(rates):.0f}%  stdev={statistics.pstdev(rates):.1f}  "
              f"muestras al 100%={sum(1 for r in rates if r == 100)}/{len(rates)}")

        print("  2) TOTAL acumulado")
        print(f"     reales encontradas {founds}/{totals} ({100 * founds / max(1, totals):.1f}%) | "
              f"canonic_explains {st['expl']}/{totals} ({100 * st['expl'] / max(1, totals):.1f}%)")

        print("  3) MISSES POR OPERACION")
        for op, o in sorted(st["op"].items(), key=lambda kv: -kv[1]["total"]):
            pct = 100 * o["found"] / max(1, o["total"])
            print(f"     {op:<8} total={o['total']:<3} encontradas={o['found']} ({pct:.0f}%)")

        if st["misses"]:
            print("  4) MISSES FRECUENTES")
            for (op, q), n in Counter(st["misses"]).most_common(6):
                print(f"     x{n} [{op}] {q}")
        if not st["misses"]:
            print("  4) MISSES: ninguno")

        if st["issues"]:
            print("  5) LITERALES INVEROSIMILES / NO VERIFICADAS")
            for smp, qid, op, iss in st["issues"]:
                tag = "imposible" if any(i != "query_no_verificable" for i in iss) else "no-verificada"
                print(f"     [{tag}] muestra={smp} id={qid} [{op}] {iss}")
        else:
            print("  5) PLAUSIBILIDAD: 0 literales inverosimiles en las encontradas")


def main():
    assert SAMPLES >= 1
    agg = {}
    for i in range(SAMPLES):
        try:
            run = run_sample(i)
        except Exception as ex:
            print(f"  ERROR en muestra {i + 1}: {ex}")
            continue
        for e, st in run.items():
            base = agg.setdefault(e, engine_stats())
            base["samples"] += st["samples"]
            base["expl"] += st["expl"]
            base["issues"] += st["issues"]
            for op, o in st["op"].items():
                b = base["op"].setdefault(op, {"total": 0, "found": 0})
                b["total"] += o["total"]
                b["found"] += o["found"]
            base["misses"] += st["misses"]
    print_final(agg)


if __name__ == "__main__":
    main()