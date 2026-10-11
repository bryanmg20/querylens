"""
PROTOTIPO: variantes de plan en MySQL, como las de la version 3 en Postgres.

No es parte de ningun pipeline. Toma los snapshots MySQL de una corrida de
run_ab.py y, por cada caso, pide tres planes y se queda con uno:

  generic    la consulta preparada con `?` (PREPARE + EXECUTE). MySQL no usa el
             histograma para un parametro: estima con sus valores por defecto
             (10 % para =, 33 % para rangos), el equivalente del GENERIC_PLAN.
  many_rows  el texto con el valor del histograma que trae mas filas.
  few_rows   el texto con el valor que trae menos.

Elige igual que la v3 en Postgres (plan_variants.select_plan de la v3): el de
menor q-error entre las filas estimadas de la raiz (output_rows del normalizer
MySQL de la v3) y avg_rows_per_call (SUM_ROWS_SENT / COUNT_STAR). En empate
queda el generico. Si algun literal no tiene histograma no hay variantes y
queda el plan que ya traia el snapshot (QUERY_SAMPLE_TEXT), como hoy.

Despues reemplaza el plan en una copia del snapshot y corre la regla del
patron, igual que run_ab.py. Escribe results/<patron>/mysql_<version>.json con
el mismo formato, asi que `run_ab.py compare` lo lee.

Los literales se buscan en QUERY_SAMPLE_TEXT con una expresion regular
(`col <op> literal`, col con alias opcional); LIMIT y otros numeros sin
operador quedan como estan. Alcanza para los casos del sandbox, no para SQL
arbitrario.

REQUISITO: las tablas del patron tienen que seguir creadas, con sus
histogramas, tal como estaban en la corrida (run_ab.py ... --keep).

USO (desde la raiz, con el python del venv de telemetry_pipeline/)
  telemetry_pipeline/venv/Scripts/python.exe ql_sandbox/explain_ab/mysql_variants.py \\
      ap02b --from-version v3_hist --version v3_hist_variantes \\
      --pipeline-dir ../querylens-v3/telemetry_pipeline
"""

import argparse
import base64
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS_DIR = HERE / "results"
SCHEMA = "ql_demo"

_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+`?(\w+)`?(?:\s+(?:AS\s+)?`?(?!(?:WHERE|JOIN|ON|INNER|LEFT|RIGHT|GROUP|ORDER|LIMIT)\b)(\w+)`?)?",
    re.IGNORECASE,
)
_PREDICATE = re.compile(
    r"(?P<col>(?:`?\w+`?\.)?`?\w+`?)\s*(?P<op>>=|<=|=|>|<)\s*(?P<lit>'(?:[^']|'')*'|-?\d+(?:\.\d+)?)",
)


def find_pattern(name: str) -> Path:
    matches = [p for p in (HERE / "patterns").iterdir()
               if p.is_dir() and (p.name == name or p.name.startswith(name + "_"))]
    if len(matches) != 1:
        raise SystemExit(f"Patron '{name}' no encontrado o ambiguo")
    return matches[0]


def table_aliases(sql: str) -> dict[str, str]:
    aliases = {}
    for table, alias in _TABLE_REF.findall(sql):
        aliases[table.lower()] = table
        if alias:
            aliases[alias.lower()] = table
    return aliases


def literal_value(literal: str):
    if literal.startswith("'"):
        return literal[1:-1].replace("''", "'")
    return float(literal) if "." in literal else int(literal)


def parse_predicates(sql: str, conn) -> list[dict] | None:
    """[{table, column, op, value, span}] por cada `col <op> literal`. None si
    alguna columna no se puede asociar a una tabla."""
    aliases = table_aliases(sql)
    tables = sorted(set(aliases.values()))
    found = []
    for match in _PREDICATE.finditer(sql):
        col = match.group("col").replace("`", "")
        qualifier, _, column = col.rpartition(".")
        if qualifier:
            table = aliases.get(qualifier.lower())
        elif len(tables) == 1:
            table = tables[0]
        else:
            table = column_owner(conn, tables, column)
        if table is None:
            return None
        found.append({
            "table": table, "column": column, "op": match.group("op"),
            "value": literal_value(match.group("lit")), "span": match.span("lit"),
        })
    return found


def column_owner(conn, tables: list[str], column: str) -> str | None:
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT TABLE_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = :s AND COLUMN_NAME = :c"
    ), {"s": SCHEMA, "c": column}).scalars().all()
    owners = [t for t in tables if t in rows]
    return owners[0] if len(owners) == 1 else None


def decode(value):
    # strings del histograma: "base64:type254:ZG9uZQ=="
    if isinstance(value, str) and value.startswith("base64:"):
        return base64.b64decode(value.split(":", 2)[2]).decode("utf-8")
    return value


def histogram(conn, table: str, column: str) -> dict | None:
    from sqlalchemy import text
    raw = conn.execute(text(
        "SELECT HISTOGRAM FROM information_schema.COLUMN_STATISTICS "
        "WHERE SCHEMA_NAME = :s AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"s": SCHEMA, "t": table, "c": column}).scalar()
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def pick_values(op: str, hist: dict):
    """(many, few) como pick_values de la v3: igualdad -> el valor mas
    frecuente y el menos frecuente (singleton) o el del medio (equi-height);
    rango -> los extremos del histograma."""
    buckets = hist.get("buckets") or []
    if not buckets:
        return None
    singleton = hist.get("histogram-type") == "singleton"
    if op == "=":
        if singleton:
            freqs, previous = [], 0.0
            for value, cumulative in buckets:
                freqs.append((cumulative - previous, decode(value)))
                previous = cumulative
            return max(freqs)[1], min(freqs)[1]
        # equi-height: [lower, upper, cumulative, ndv]; filas por valor = freq / ndv
        per_value, previous = [], 0.0
        for lower, _upper, cumulative, ndv in buckets:
            per_value.append(((cumulative - previous) / max(ndv, 1), decode(lower)))
            previous = cumulative
        return max(per_value)[1], decode(buckets[len(buckets) // 2][0])
    # singleton: [valor, acumulada]; equi-height: [lower, upper, acumulada, ndv]
    first = decode(buckets[0][0])
    last = decode(buckets[-1][0] if singleton else buckets[-1][1])
    if op in (">", ">="):
        return first, last
    return last, first


def sql_literal(value) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def with_values(sql: str, predicates: list[dict], values: list) -> str:
    out = sql
    for predicate, value in sorted(zip(predicates, values), key=lambda pv: pv[0]["span"][0], reverse=True):
        start, end = predicate["span"]
        out = out[:start] + sql_literal(value) + out[end:]
    return out


def explain_text(conn, sql: str) -> dict:
    from sqlalchemy import text
    return json.loads(conn.execute(text(f"EXPLAIN FORMAT=JSON {sql}")).scalar())


def explain_prepared(conn, sql: str, predicates: list[dict]) -> dict:
    """Plan "generico": los literales pasan como parametros (`?`)."""
    from sqlalchemy import text
    template = with_values(sql, predicates, ["\0"] * len(predicates)).replace("'\0'", "?")
    conn.execute(text("SET @ql_q = :q"), {"q": "EXPLAIN FORMAT=JSON " + template})
    conn.execute(text("PREPARE ql_variant FROM @ql_q"))
    try:
        names = []
        for i, predicate in enumerate(predicates, 1):
            conn.execute(text(f"SET @ql_p{i} = :v"), {"v": predicate["value"]})
            names.append(f"@ql_p{i}")
        return json.loads(conn.execute(text(f"EXECUTE ql_variant USING {', '.join(names)}")).scalar())
    finally:
        conn.execute(text("DEALLOCATE PREPARE ql_variant"))


def canonical(normalizer, plan: dict) -> dict:
    stats = normalizer.normalize({"query_explain": [{"query_id": 0, "plan": plan}]})
    return stats["canonic_explains"][0]["canonical_plan"]


def scans_of(canonical_plan: dict) -> list[dict]:
    return [
        {key: op.get(key) for key in ("relation", "access_method", "index_name", "estimated_rows", "predicate")}
        for op in canonical_plan.get("physical_operations", [])
        if op.get("type") == "scan"
    ]


def classify(expected, detected: bool) -> str:
    if expected is None:
        return "detecta" if detected else "no_detecta"
    if expected:
        return "VP" if detected else "FN"
    return "FP" if detected else "VN"


def evaluate(engine_python: str, rule: str, snapshot_file: Path) -> list[dict]:
    out = subprocess.run(
        [engine_python, str(HERE / "evaluate.py"), str(ROOT / "detection_engine"), rule, str(snapshot_file)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def process_case(conn, normalizer, select_plan, case: dict, snapshot: dict) -> dict:
    """Devuelve {plan elegido, fuente, detalle de variantes} para el caso."""
    query_id = case["query_id"]
    statement = next(s for s in snapshot["statements"] if str(s.get("query_id")) == query_id)
    explain = next(e for e in snapshot["canonic_explains"] if str(e.get("query_id")) == query_id)
    sample = statement.get("query_sample_text")
    actual = statement.get("avg_rows_per_call")
    info = {"sample_text": sample, "avg_rows_per_call": actual, "variants": {}}

    predicates = parse_predicates(sample, conn) if sample else None
    picks = []
    for predicate in predicates or []:
        hist = histogram(conn, predicate["table"], predicate["column"])
        pick = pick_values(predicate["op"], hist) if hist else None
        if pick is None:
            picks = []
            break
        picks.append(pick)
    if not predicates or not picks:
        info["motivo"] = "sin literales" if not predicates else "algun literal sin histograma"
        return {"canonical_plan": explain["canonical_plan"], "source": "sample", **info}

    plans = {
        "generic": canonical(normalizer, explain_prepared(conn, sample, predicates)),
        "many_rows": canonical(normalizer, explain_text(conn, with_values(sample, predicates, [p[0] for p in picks]))),
        "few_rows": canonical(normalizer, explain_text(conn, with_values(sample, predicates, [p[1] for p in picks]))),
    }
    for label, plan in plans.items():
        info["variants"][label] = {"output_rows": plan["estimates"].get("output_rows"), "scans": scans_of(plan)}
    labels = ["many_rows", "few_rows"]
    chosen = select_plan(
        plans["generic"]["estimates"].get("output_rows"),
        [plans[label]["estimates"].get("output_rows") for label in labels],
        actual,
    )
    source = "generic" if chosen is None else labels[chosen]
    return {"canonical_plan": plans[source], "source": source, **info}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prototipo de variantes de plan en MySQL.")
    parser.add_argument("pattern")
    parser.add_argument("--from-version", required=True, help="corrida MySQL de run_ab.py a reprocesar")
    parser.add_argument("--version", required=True, help="etiqueta del resultado nuevo")
    parser.add_argument("--pipeline-dir", type=Path, required=True, help="pipeline de la v3 (normalizer y select_plan)")
    opts = parser.parse_args()

    pattern_dir = find_pattern(opts.pattern)
    out_dir = RESULTS_DIR / pattern_dir.name
    source_report = json.loads((out_dir / f"mysql_{opts.from_version}.json").read_text(encoding="utf-8"))
    snapshots_dir = out_dir / "snapshots" / opts.version
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    engine_python = str(ROOT / "detection_engine" / "venv" / "Scripts" / "python.exe")

    pipeline_dir = opts.pipeline_dir.resolve()
    sys.path.insert(0, str(pipeline_dir))
    cwd = os.getcwd()
    os.chdir(pipeline_dir)
    from config.connections import get_connection_mysql
    from stages.explain_normalizer import MysqlExplainNormalizer
    from stages.plan_variants import select_plan
    from sqlalchemy import text
    os.chdir(cwd)

    normalizer = MysqlExplainNormalizer()
    engine = get_connection_mysql()
    cases = []
    try:
        with engine.connect() as conn:
            conn.execute(text(f"USE {SCHEMA}"))
            for case in source_report["cases"]:
                result = copy.deepcopy(case)
                result.pop("findings", None)
                if case["outcome"] in ("sin_explain", "no_candidata", "no_encontrada"):
                    cases.append(result)
                    continue
                snapshot_path = HERE / case["snapshot"]
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                chosen = process_case(conn, normalizer, select_plan, case, snapshot)
                for explain in snapshot["canonic_explains"]:
                    if str(explain.get("query_id")) == case["query_id"]:
                        explain["canonical_plan"] = chosen["canonical_plan"]
                        explain["explain_source"] = chosen["source"]
                new_snapshot = snapshots_dir / snapshot_path.name
                new_snapshot.write_text(json.dumps(snapshot, default=str), encoding="utf-8")

                findings = [f for f in evaluate(engine_python, source_report["rule"], new_snapshot)
                            if str(f.get("query_id")) == case["query_id"]]
                result.update({
                    "snapshot": str(new_snapshot.relative_to(HERE)),
                    "explain_source": chosen["source"],
                    "plan_output_rows": chosen["canonical_plan"]["estimates"].get("output_rows"),
                    "avg_rows_per_call": chosen["avg_rows_per_call"],
                    "plan_scans": scans_of(chosen["canonical_plan"]),
                    "variants": chosen["variants"],
                    "motivo": chosen.get("motivo"),
                    "findings": findings,
                    "outcome": classify(case["expected"], bool(findings)),
                })
                print(f"  {case['id']:<28} {case['outcome']:<11} -> {result['outcome']:<11} plan={chosen['source']}")
                cases.append(result)
    finally:
        engine.dispose()

    report = {**source_report, "version": opts.version, "cases": cases,
              "prototipo": f"mysql_variants.py sobre mysql_{opts.from_version}.json"}
    out_file = out_dir / f"mysql_{opts.version}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"resultado: {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
