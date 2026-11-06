"""Bateria de OR/AND: cuando usa un indice cada motor.

Pregunta: con un predicado de condiciones unidas por AND y OR, el motor usa un
indice (en vez de recorrer la tabla) exactamente cuando lo predice esta regla?

  - una condicion usa indice si es columna pelada contra valor y la columna es
    la primera de un indice (estado OK)
  - un AND usa indice si ALGUNA de sus partes lo usa
  - un OR usa indice si TODAS sus ramas lo usan (BitmapOr / index_merge)

Si la regla se cumple, AP-03 (predicado no sargable) y AP-04 (indice ausente)
pueden evaluar dentro de un OR: reportar solo cuando su arreglo, y solo el
suyo, hace que el predicado pase a usar indice.

Tabla ql_or de ROWS filas con 9 familias de columnas; cada familia tiene:
  f      con indice propio
  f_ni   mismos datos, sin indice
  f_nl   mismos datos, segunda columna del indice (g, f_nl)
Estados de una condicion:
  OK           columna con indice, sin transformar
  SIN_INDICE   columna sin indice, sin transformar
  ENVUELTA     funcion / aritmetica / conversion sobre la columna con indice
  ENV_SIN_IDX  lo mismo sobre la columna sin indice
  NO_PRIMERA   columna que solo es segunda de un indice compuesto

USO (desde la raiz del repo, con ql_postgres y ql_mysql levantados)
  python ql_sandbox/explain_or/or_battery.py [--keep]

Salida: ql_sandbox/explain_or/resultados.json y resumen.txt
"""
import argparse
import itertools
import json
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS = 300_000
SEED = 20261014

# familia: (tipo PG, tipo MySQL, expr PG de n, expr MySQL de n,
#           condicion pelada, envuelta PG, envuelta MySQL)
FAMILIES = {
    "a": ("int", "int", "n % 30000", "n % 30000",
          "{c} = 12345", "{c} + 0 = 12345", "{c} + 0 = 12345"),
    "b": ("int", "int", "(n * 7) % 30000", "(n * 7) % 30000",
          "{c} IN (101, 202, 303)", "{c} + 0 IN (101, 202, 303)", "{c} + 0 IN (101, 202, 303)"),
    "d": ("int", "int", "(n * 13) % 30000", "(n * 13) % 30000",
          "{c} < 30", "{c} * 1 < 30", "{c} * 1 < 30"),
    "e": ("int", "int", "(n * 17) % 30000", "(n * 17) % 30000",
          "{c} BETWEEN 500 AND 505", "{c} + 0 BETWEEN 500 AND 505", "{c} + 0 BETWEEN 500 AND 505"),
    "s": ("varchar(20)", "varchar(20)", "'v' || ((n * 19) % 30000)", "CONCAT('v', (n * 19) % 30000)",
          "{c} = 'v4321'", "LOWER({c}) = 'v4321'", "LOWER({c}) = 'v4321'"),
    "m": ("numeric(10,1)", "decimal(10,1)", "((n * 23) % 30000) / 10.0", "((n * 23) % 30000) / 10.0",
          "{c} = 1234.5", "FLOOR({c}) = 1234", "FLOOR({c}) = 1234"),
    "t": ("timestamp", "datetime",
          "timestamp '2026-01-01' + ((n * 29) % 300000) * interval '1 minute'",
          "TIMESTAMP '2026-01-01 00:00:00' + INTERVAL ((n * 29) % 300000) MINUTE",
          "{c} BETWEEN '2026-03-01 00:00:00' AND '2026-03-01 01:00:00'",
          "DATE({c}) = '2026-03-01'", "DATE({c}) = '2026-03-01'"),
    # conversion: explicita en Postgres (no convierte solo char contra numero),
    # implicita en MySQL (char contra numero)
    "c": ("char(10)", "char(10)", "lpad(((n * 31) % 30000)::text, 10, '0')", "LPAD((n * 31) % 30000, 10, '0')",
          "{c} = '0000004321'", "CAST({c} AS int) = 4321", "{c} = 4321"),
    "z": ("int", "int", "CASE WHEN n % 3000 = 0 THEN NULL ELSE n END", "CASE WHEN n % 3000 = 0 THEN NULL ELSE n END",
          "{c} IS NULL", "COALESCE({c}, 0) = 0", "COALESCE({c}, 0) = 0"),
}
STATES = ("OK", "SIN_INDICE", "ENVUELTA", "ENV_SIN_IDX", "NO_PRIMERA")
NESTED_STATES = ("OK", "SIN_INDICE", "ENVUELTA")


def setup_sql(engine: str) -> str:
    pg = engine == "postgres"
    columns = []
    for name, (pg_type, my_type, *_rest) in FAMILIES.items():
        column_type = pg_type if pg else my_type
        columns += [f"{name} {column_type}", f"{name}_ni {column_type}", f"{name}_nl {column_type}"]
    exprs = []
    for name, (_, _, pg_expr, my_expr, *_rest) in FAMILIES.items():
        expr = pg_expr if pg else my_expr
        exprs += [expr, expr, expr]
    indexes = []
    for name in FAMILIES:
        indexes.append(f"CREATE INDEX ql_or_{name} ON ql_or ({name})")
        indexes.append(f"CREATE INDEX ql_or_g_{name}_nl ON ql_or (g, {name}_nl)")
    table = f"CREATE TABLE ql_or (id int PRIMARY KEY, g int, {', '.join(columns)})"
    if pg:
        insert = (f"INSERT INTO ql_or SELECT n, (n * 37) % 30000, {', '.join(exprs)} "
                  f"FROM generate_series(1, {ROWS}) AS n")
        statements = ["DROP TABLE IF EXISTS ql_or", table, insert, *indexes, "ANALYZE ql_or"]
    else:
        insert = (f"INSERT INTO ql_or WITH RECURSIVE s (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s "
                  f"WHERE n < {ROWS}) SELECT n, (n * 37) % 30000, {', '.join(exprs)} FROM s")
        statements = ["DROP TABLE IF EXISTS ql_or", table, f"SET SESSION cte_max_recursion_depth = {ROWS + 1}",
                      insert, *indexes, "ANALYZE TABLE ql_or"]
    return ";\n".join(statements) + ";\n"


# ---- expresiones: ("atom", familia, estado) | ("and"|"or", [hijos]) ----

def render(node, engine: str) -> str:
    if node[0] == "atom":
        _, family, state = node
        bare, wrapped_pg, wrapped_my = FAMILIES[family][4:]
        column = {"OK": family, "SIN_INDICE": f"{family}_ni", "ENVUELTA": family,
                  "ENV_SIN_IDX": f"{family}_ni", "NO_PRIMERA": f"{family}_nl"}[state]
        template = bare if state in ("OK", "SIN_INDICE", "NO_PRIMERA") else (
            wrapped_pg if engine == "postgres" else wrapped_my)
        return template.format(c=column)
    joiner = " AND " if node[0] == "and" else " OR "
    return "(" + joiner.join(render(child, engine) for child in node[1]) + ")"


def predicted(node) -> bool:
    if node[0] == "atom":
        return node[2] == "OK"
    results = [predicted(child) for child in node[1]]
    return any(results) if node[0] == "and" else all(results)


def shape(node) -> str:
    if node[0] == "atom":
        return "X"
    joiner = " AND " if node[0] == "and" else " OR "
    return "(" + joiner.join(shape(child) for child in node[1]) + ")"


def atom(family, state):
    return ("atom", family, state)


def build_cases():
    rng = random.Random(SEED)
    families = list(FAMILIES)
    cases = []

    def add(group, node):
        cases.append({"grupo": group, "forma": shape(node), "nodo": node})

    # 1. condicion sola: valida que cada familia use su indice
    for family in families:
        for state in STATES:
            add("1 condicion sola", atom(family, state))
    # 2. OR de 2 ramas: todas las parejas de familias x todos los estados
    for f1, f2 in itertools.combinations(families, 2):
        for s1, s2 in itertools.product(STATES, repeat=2):
            add("2 OR de 2", ("or", [atom(f1, s1), atom(f2, s2)]))
    # misma columna en las dos ramas
    for family in families:
        for s1, s2 in itertools.product(("OK", "SIN_INDICE", "ENVUELTA"), repeat=2):
            add("2b OR misma columna", ("or", [atom(family, s1), atom(family, s2)]))
    # 3. OR de 3: todas OK y exactamente una rama mala en cada posicion
    for trio in itertools.combinations(families, 3):
        add("3 OR de 3", ("or", [atom(f, "OK") for f in trio]))
        for position, bad in itertools.product(range(3), STATES[1:]):
            states = ["OK"] * 3
            states[position] = bad
            add("3 OR de 3", ("or", [atom(f, s) for f, s in zip(trio, states)]))
    # 4. OR de 4: todas OK y una SIN_INDICE
    for quad in itertools.combinations(families, 4):
        add("4 OR de 4", ("or", [atom(f, "OK") for f in quad]))
        states = ["OK"] * 4
        states[rng.randrange(4)] = rng.choice(STATES[1:])
        add("4 OR de 4", ("or", [atom(f, s) for f, s in zip(quad, states)]))
    # 5. anidados, con familias al azar
    for _ in range(12):
        f1, f2, f3 = rng.sample(families, 3)
        for s1, s2, s3 in itertools.product(NESTED_STATES, repeat=3):
            add("5a (A AND B) OR C", ("or", [("and", [atom(f1, s1), atom(f2, s2)]), atom(f3, s3)]))
            add("5b (A OR B) AND C", ("and", [("or", [atom(f1, s1), atom(f2, s2)]), atom(f3, s3)]))
    for _ in range(8):
        f1, f2, f3, f4 = rng.sample(families, 4)
        for states in itertools.product(NESTED_STATES, repeat=4):
            s1, s2, s3, s4 = states
            add("5c (A OR B) AND (C OR D)",
                ("and", [("or", [atom(f1, s1), atom(f2, s2)]), ("or", [atom(f3, s3), atom(f4, s4)])]))
    for _ in range(6):
        f1, f2, f3, f4 = rng.sample(families, 4)
        for states in itertools.product(NESTED_STATES, repeat=4):
            s1, s2, s3, s4 = states
            add("5d (A AND B) OR (C AND D)",
                ("or", [("and", [atom(f1, s1), atom(f2, s2)]), ("and", [atom(f3, s3), atom(f4, s4)])]))
    return cases


# ---- ejecucion de los EXPLAIN ----

MARK = "@@QL@@"
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|(?<![\w$])\d+(?:\.\d+)?")


def to_generic(sql: str) -> str:
    counter = itertools.count(1)
    return _LITERAL_RE.sub(lambda _: f"${next(counter)}", sql)


def run(cmd, stdin):
    result = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    return result.stdout


def run_postgres(sql: str):
    return run(["docker", "exec", "-i", "ql_postgres", "psql", "-U", "ql_user", "-d", "ql_demo",
                "-At", "-v", "ON_ERROR_STOP=0", "-f", "-"], sql)


def run_mysql(sql: str):
    return run(["docker", "exec", "-i", "ql_mysql", "mysql", "-uroot", "-pql_root", "ql_demo",
                "-N", "-B", "--force"], sql)


def explain_postgres(queries, generic: bool):
    option = "GENERIC_PLAN, FORMAT JSON" if generic else "FORMAT JSON"
    script = "".join(f"\\echo {MARK}\nEXPLAIN ({option}) {query};\n" for query in queries)
    chunks = run_postgres(script).split(MARK + "\n")[1:]
    return [_postgres_access(chunk) for chunk in chunks]


def _postgres_access(chunk: str):
    try:
        plan = json.loads(chunk)[0]["Plan"]
    except Exception:
        return {"acceso": "ERROR", "detalle": chunk.strip()[:200]}
    nodes = []

    def walk(node):
        nodes.append(node)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan)
    kinds = [node["Node Type"] for node in nodes]
    if "Seq Scan" in kinds:
        access = "completo"
    elif any(node["Node Type"] in ("Index Scan", "Index Only Scan") and "Index Cond" not in node
             for node in nodes):
        access = "completo"  # recorre un indice entero sin filtrar
    else:
        access = "indice"
    detail = " > ".join(
        node["Node Type"] + (f"[{node['Index Name']}]" if "Index Name" in node else "") for node in nodes
    )
    return {"acceso": access, "detalle": detail}


def explain_mysql(queries):
    script = "".join(f"SELECT '{MARK}';\nEXPLAIN {query};\n" for query in queries)
    chunks = run_mysql(script).split(MARK + "\n")[1:]
    results = []
    for chunk in chunks:
        line = chunk.strip().splitlines()[0] if chunk.strip() else ""
        fields = line.split("\t")
        if len(fields) < 7:
            results.append({"acceso": "ERROR", "detalle": chunk.strip()[:200]})
            continue
        access_type, key = fields[4], fields[6]
        access = "completo" if access_type in ("ALL", "index") else "indice"
        extra = fields[-1]
        results.append({"acceso": access, "detalle": f"{access_type}[{key}] {extra}"})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="no borrar ql_or al terminar")
    args = parser.parse_args()

    cases = build_cases()
    print(f"{len(cases)} casos; preparando ql_or ({ROWS:,} filas) en los dos motores...", flush=True)
    run_postgres(setup_sql("postgres"))
    run_mysql(setup_sql("mysql"))

    queries = {
        engine: [f"SELECT id FROM ql_or WHERE {render(case['nodo'], engine)}" for case in cases]
        for engine in ("postgres", "mysql")
    }
    print("EXPLAIN Postgres (valores reales)...", flush=True)
    pg_literal = explain_postgres(queries["postgres"], generic=False)
    print("EXPLAIN Postgres (plan generico)...", flush=True)
    pg_generic = explain_postgres([to_generic(q) for q in queries["postgres"]], generic=True)
    print("EXPLAIN MySQL...", flush=True)
    my_literal = explain_mysql(queries["mysql"])

    results = []
    for index, case in enumerate(cases):
        expected = "indice" if predicted(case["nodo"]) else "completo"
        results.append({
            "grupo": case["grupo"],
            "forma": case["forma"],
            "prediccion": expected,
            "postgres_sql": queries["postgres"][index],
            "mysql_sql": queries["mysql"][index],
            "postgres": pg_literal[index],
            "postgres_generico": pg_generic[index],
            "mysql": my_literal[index],
        })

    if not args.keep:
        run_postgres("DROP TABLE IF EXISTS ql_or;")
        run_mysql("DROP TABLE IF EXISTS ql_or;")

    (HERE / "resultados.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = summarize(results)
    (HERE / "resumen.txt").write_text(summary, encoding="utf-8")
    print(summary)


def _uses_second_column(item) -> bool:
    # alguna condicion usa una columna que solo es segunda de un indice
    # compuesto (estado NO_PRIMERA)
    return "_nl" in item["postgres_sql"]


def _table(title: str, results, sources) -> list[str]:
    header = f"{'grupo':28}" + "".join(f"{source:>22}" for source in sources)
    lines = [title, header]
    by_group = defaultdict(list)
    for result in results:
        by_group[result["grupo"]].append(result)
    for group, items in sorted(by_group.items()):
        row = f"{group:28}"
        for source in sources:
            ok = sum(item[source]["acceso"] == item["prediccion"] for item in items)
            row += f"{f'{ok}/{len(items)}':>22}"
        lines.append(row)
    total = f"{'TOTAL':28}"
    for source in sources:
        ok = sum(item[source]["acceso"] == item["prediccion"] for item in results)
        total += f"{f'{ok}/{len(results)}':>22}"
    return lines + [total, ""]


def summarize(results) -> str:
    sources = ("postgres", "postgres_generico", "mysql")
    rule = [item for item in results if not _uses_second_column(item)]
    second = [item for item in results if _uses_second_column(item)]
    lines = [
        f"Casos: {len(results)} ({len(rule)} para la regla AND/OR, {len(second)} con NO_PRIMERA)",
        "",
    ]
    lines += _table(
        "Regla AND/OR (plan real == prediccion), sin NO_PRIMERA, por grupo:", rule, sources,
    )
    lines += _table(
        "Aparte: casos con NO_PRIMERA (columna que solo es segunda de un indice compuesto).\n"
        "La prediccion dice 'no usa indice'. Postgres si lo usa (recorre el indice compuesto\n"
        "entero) y MySQL no (salvo skip scan si la primera columna tiene pocos valores).\n"
        "Los detectores lo tratan como sin indice en los dos motores.",
        second, sources,
    )

    for source in sources:
        misses = [item for item in rule if item[source]["acceso"] != item["prediccion"]]
        lines.append(f"== {source}: {len(misses)} fallos de la regla (sin NO_PRIMERA)")
        kinds = Counter((item["prediccion"], item[source]["acceso"]) for item in misses)
        for (expected, actual), count in kinds.most_common():
            lines.append(f"   predijo {expected:8} y el plan fue {actual:8}: {count}")
        sql_key = "mysql_sql" if source == "mysql" else "postgres_sql"
        for item in misses[:40]:
            lines.append(f"   [{item['grupo']}] pred={item['prediccion']} plan={item[source]['acceso']}")
            lines.append(f"      {item[sql_key]}")
            lines.append(f"      {item[source]['detalle']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
