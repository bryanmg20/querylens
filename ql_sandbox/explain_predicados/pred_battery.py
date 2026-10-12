"""Bateria de predicados: mismo SELECT en Postgres (generic + literal) y MySQL,
pasado por el normalizador real del pipeline. Corre dentro de querylens_pipeline;
ver README.md de esta carpeta."""
import copy
import json
import sys

sys.path.insert(0, "/app")
from sqlalchemy import create_engine, text  # noqa: E402

from stages.explain_normalizer import MysqlExplainNormalizer, PostgresExplainNormalizer  # noqa: E402
from stages.normalize import normalize_predicate  # noqa: E402

PG = create_engine("postgresql+psycopg2://ql_user:ql_pass@host.docker.internal:5432/ql_demo")
MY = create_engine("mysql+pymysql://root:ql_root@host.docker.internal:3307/ql_demo")

PG_SETUP = """
DROP TABLE IF EXISTS ql_pt_orders; DROP TABLE IF EXISTS ql_pt_customers;
CREATE TABLE ql_pt_customers (id int PRIMARY KEY, email varchar(100), name varchar(100), region int);
CREATE INDEX ql_pt_customers_email ON ql_pt_customers (email);
CREATE TABLE ql_pt_orders (id int PRIMARY KEY, customer_id int REFERENCES ql_pt_customers (id),
  status varchar(20), total numeric(10,2), created_at timestamp, note text, code char(10));
CREATE INDEX ql_pt_orders_customer ON ql_pt_orders (customer_id);
CREATE INDEX ql_pt_orders_status ON ql_pt_orders (status);
CREATE INDEX ql_pt_orders_created ON ql_pt_orders (created_at);
INSERT INTO ql_pt_customers SELECT g, 'user' || g || '@x.com', 'name' || g, g % 10 FROM generate_series(1, 5000) g;
INSERT INTO ql_pt_orders SELECT g, 1 + g % 5000,
  CASE WHEN g % 100 = 0 THEN 'raro' WHEN g % 100 < 30 THEN 'pendiente' ELSE 'entregado' END,
  (g % 1000) / 10.0, timestamp '2026-01-01' + g * interval '1 minute',
  CASE WHEN g % 10 = 0 THEN NULL ELSE 'nota' || g END, lpad((g % 1000)::text, 10, '0')
FROM generate_series(1, 100000) g;
ANALYZE ql_pt_customers; ANALYZE ql_pt_orders;
"""

MY_SETUP = [
    "DROP TABLE IF EXISTS ql_pt_orders", "DROP TABLE IF EXISTS ql_pt_customers",
    "CREATE TABLE ql_pt_customers (id int PRIMARY KEY, email varchar(100), name varchar(100), region int, INDEX ql_pt_customers_email (email))",
    """CREATE TABLE ql_pt_orders (id int PRIMARY KEY, customer_id int, status varchar(20), total decimal(10,2),
       created_at datetime, note text, code char(10),
       INDEX ql_pt_orders_status (status), INDEX ql_pt_orders_created (created_at),
       CONSTRAINT ql_pt_fk FOREIGN KEY (customer_id) REFERENCES ql_pt_customers (id))""",
    "SET SESSION cte_max_recursion_depth = 200000",
    """INSERT INTO ql_pt_customers WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 5000)
       SELECT n, CONCAT('user', n, '@x.com'), CONCAT('name', n), n % 10 FROM g""",
    """INSERT INTO ql_pt_orders WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 100000)
       SELECT n, 1 + n % 5000,
         CASE WHEN n % 100 = 0 THEN 'raro' WHEN n % 100 < 30 THEN 'pendiente' ELSE 'entregado' END,
         (n % 1000) / 10.0, TIMESTAMP '2026-01-01 00:00:00' + INTERVAL n MINUTE,
         CASE WHEN n % 10 = 0 THEN NULL ELSE CONCAT('nota', n) END, LPAD(n % 1000, 10, '0')
       FROM g""",
    "ANALYZE TABLE ql_pt_customers, ql_pt_orders",
]

O, C = "ql_pt_orders", "ql_pt_customers"
# (nombre, PG con $n como lo deja pg_stat_statements, PG con literales, MySQL con literales)
CASES = [
    ("01 igualdad con indice", f"SELECT id FROM {O} WHERE status = $1", f"SELECT id FROM {O} WHERE status = 'raro'", None),
    ("02 igualdad sin indice", f"SELECT id FROM {O} WHERE total = $1", f"SELECT id FROM {O} WHERE total = 10.5", None),
    ("03 rango fechas", f"SELECT id FROM {O} WHERE created_at >= $1 AND created_at < $2",
     f"SELECT id FROM {O} WHERE created_at >= '2026-01-02' AND created_at < '2026-01-03'", None),
    ("04 BETWEEN", f"SELECT id FROM {O} WHERE total BETWEEN $1 AND $2", f"SELECT id FROM {O} WHERE total BETWEEN 10 AND 20", None),
    ("05 IN lista", f"SELECT id FROM {O} WHERE status IN ($1, $2)", f"SELECT id FROM {O} WHERE status IN ('raro', 'pendiente')", None),
    ("06 distinto", f"SELECT id FROM {O} WHERE status <> $1", f"SELECT id FROM {O} WHERE status <> 'entregado'", None),
    ("07 OR", f"SELECT id FROM {O} WHERE status = $1 OR total > $2", f"SELECT id FROM {O} WHERE status = 'raro' OR total > 99", None),
    ("08 IS NULL", f"SELECT id FROM {O} WHERE note IS NULL", f"SELECT id FROM {O} WHERE note IS NULL", None),
    ("09 LIKE prefijo", f"SELECT id FROM {O} WHERE note LIKE $1", f"SELECT id FROM {O} WHERE note LIKE 'nota1%'", None),
    ("10 LIKE sufijo", f"SELECT id FROM {O} WHERE note LIKE $1", f"SELECT id FROM {O} WHERE note LIKE '%99'", None),
    ("11 funcion LOWER", f"SELECT id FROM {C} WHERE LOWER(email) = $1", f"SELECT id FROM {C} WHERE LOWER(email) = 'user5@x.com'", None),
    ("12 aritmetica", f"SELECT id FROM {O} WHERE total + $1 = $2", f"SELECT id FROM {O} WHERE total + 0 = 10", None),
    ("13 DATE()", f"SELECT id FROM {O} WHERE DATE(created_at) = $1", f"SELECT id FROM {O} WHERE DATE(created_at) = '2026-01-02'", None),
    ("14 CAST explicito", f"SELECT id FROM {O} WHERE CAST(code AS INTEGER) = $1", f"SELECT id FROM {O} WHERE CAST(code AS INTEGER) = 5",
     f"SELECT id FROM {O} WHERE CAST(code AS SIGNED) = 5"),
    ("15 char contra numero (solo MySQL)", None, None, f"SELECT id FROM {O} WHERE code = 5"),
    ("16 int contra decimal", f"SELECT id FROM {O} WHERE customer_id = $1", f"SELECT id FROM {O} WHERE customer_id = 5.0", None),
    ("17 indice + funcion", f"SELECT id FROM {O} WHERE status = $1 AND LOWER(note) = $2",
     f"SELECT id FROM {O} WHERE status = 'raro' AND LOWER(note) = 'nota100'", None),
    ("18 indice + otra columna", f"SELECT id FROM {O} WHERE created_at >= $1 AND total > $2",
     f"SELECT id FROM {O} WHERE created_at >= '2026-03-01' AND total > 50", None),
    ("19 JOIN con filtros", f"SELECT o.id FROM {O} o JOIN {C} c ON c.id = o.customer_id WHERE c.region = $1 AND o.status = $2",
     f"SELECT o.id FROM {O} o JOIN {C} c ON c.id = o.customer_id WHERE c.region = 3 AND o.status = 'raro'", None),
    ("20 IN subconsulta", f"SELECT id FROM {O} WHERE customer_id IN (SELECT id FROM {C} WHERE region = $1)",
     f"SELECT id FROM {O} WHERE customer_id IN (SELECT id FROM {C} WHERE region = 3)", None),
    ("21 EXISTS correlacionado", f"SELECT o.id FROM {O} o WHERE EXISTS (SELECT 1 FROM {C} c WHERE c.id = o.customer_id AND LOWER(c.email) LIKE $1)",
     f"SELECT o.id FROM {O} o WHERE EXISTS (SELECT 1 FROM {C} c WHERE c.id = o.customer_id AND LOWER(c.email) LIKE 'user1%')", None),
    ("22 subconsulta escalar", f"SELECT o.id FROM {O} o WHERE o.customer_id = (SELECT MIN(c.id) FROM {C} c WHERE LOWER(c.name) = $1)",
     f"SELECT o.id FROM {O} o WHERE o.customer_id = (SELECT MIN(c.id) FROM {C} c WHERE LOWER(c.name) = 'name7')", None),
    ("23 CTE", f"WITH r AS (SELECT id FROM {C} WHERE region = $1) SELECT o.id FROM {O} o JOIN r ON r.id = o.customer_id",
     f"WITH r AS (SELECT id FROM {C} WHERE region = 3) SELECT o.id FROM {O} o JOIN r ON r.id = o.customer_id", None),
    ("24 tabla derivada", f"SELECT t.id FROM (SELECT id, total FROM {O} WHERE total > $1) t WHERE t.id < $2",
     f"SELECT t.id FROM (SELECT id, total FROM {O} WHERE total > 50) t WHERE t.id < 1000", None),
    ("25 GROUP BY HAVING", f"SELECT status, COUNT(*) FROM {O} WHERE total > $1 GROUP BY status HAVING COUNT(*) > $2",
     f"SELECT status, COUNT(*) FROM {O} WHERE total > 50 GROUP BY status HAVING COUNT(*) > 10", None),
    ("26 ORDER BY LIMIT", f"SELECT id FROM {O} WHERE total > $1 ORDER BY created_at DESC LIMIT $2",
     f"SELECT id FROM {O} WHERE total > 50 ORDER BY created_at DESC LIMIT 10", None),
    ("27 CASE en WHERE", f"SELECT id FROM {O} WHERE CASE WHEN status = $1 THEN $2 ELSE $3 END = $4",
     f"SELECT id FROM {O} WHERE CASE WHEN status = 'raro' THEN 1 ELSE 0 END = 1", None),
    ("28 COALESCE", f"SELECT id FROM {O} WHERE COALESCE(note, $1) = $2", f"SELECT id FROM {O} WHERE COALESCE(note, '') = ''", None),
    ("29 columna contra columna", f"SELECT id FROM {O} WHERE total > id", f"SELECT id FROM {O} WHERE total > id", None),
    ("30 NOT", f"SELECT id FROM {O} WHERE NOT (status = $1)", f"SELECT id FROM {O} WHERE NOT (status = 'raro')", None),
    ("31 IN un valor", f"SELECT id FROM {O} WHERE id IN ($1)", f"SELECT id FROM {O} WHERE id IN (5)", None),
    ("32 decimal negativo", f"SELECT id FROM {O} WHERE total > $1", f"SELECT id FROM {O} WHERE total > -5.5", None),
    ("33 texto con comilla", f"SELECT id FROM {C} WHERE name = $1", f"SELECT id FROM {C} WHERE name = 'O''Brien'", None),
    ("34 LEFT JOIN anti-join", f"SELECT c.id FROM {C} c LEFT JOIN {O} o ON o.customer_id = c.id WHERE o.id IS NULL",
     f"SELECT c.id FROM {C} c LEFT JOIN {O} o ON o.customer_id = c.id WHERE o.id IS NULL", None),
    ("35 UNION", f"SELECT id FROM {O} WHERE status = $1 UNION SELECT id FROM {O} WHERE total > $2",
     f"SELECT id FROM {O} WHERE status = 'raro' UNION SELECT id FROM {O} WHERE total > 99", None),
    ("36 self join", f"SELECT a.id FROM {O} a JOIN {O} b ON b.id = a.id + $1 WHERE a.status = $2",
     f"SELECT a.id FROM {O} a JOIN {O} b ON b.id = a.id + 1 WHERE a.status = 'raro'", None),
    ("37 concatenacion", f"SELECT id FROM {C} WHERE name || $1 = $2", f"SELECT id FROM {C} WHERE name || 'x' = 'name5x'",
     f"SELECT id FROM {C} WHERE CONCAT(name, 'x') = 'name5x'"),
    ("38 intervalo", f"SELECT id FROM {O} WHERE created_at > NOW() - $1::interval", f"SELECT id FROM {O} WHERE created_at > NOW() - INTERVAL '1 day'",
     f"SELECT id FROM {O} WHERE created_at > NOW() - INTERVAL 1 DAY"),
    ("39 funcion en el ON", f"SELECT o.id FROM {O} o JOIN {C} c ON LOWER(c.email) = LOWER(o.note) WHERE o.status = $1",
     f"SELECT o.id FROM {O} o JOIN {C} c ON LOWER(c.email) = LOWER(o.note) WHERE o.status = 'raro'", None),
    ("40 OR con funcion", f"SELECT id FROM {O} WHERE status = $1 OR LOWER(note) = $2",
     f"SELECT id FROM {O} WHERE status = 'raro' OR LOWER(note) = 'nota5'", None),
]


def pg_raw_scans(node, out):
    if isinstance(node, list):
        for item in node:
            pg_raw_scans(item, out)
        return
    if not isinstance(node, dict):
        return
    if "Plan" in node:
        return pg_raw_scans(node["Plan"], out)
    if "QUERY PLAN" in node:
        return pg_raw_scans(node["QUERY PLAN"], out)
    keys = ("Filter", "Index Cond", "Recheck Cond", "Join Filter", "Hash Cond", "Merge Cond")
    if any(k in node for k in keys) or "Scan" in node.get("Node Type", ""):
        out.append({"node": node.get("Node Type"), "rel": node.get("Alias") or node.get("Relation Name"),
                    **{k: node[k] for k in keys if k in node}})
    for child in node.get("Plans", []):
        pg_raw_scans(child, out)


def my_raw_scans(node, out):
    if isinstance(node, list):
        for item in node:
            my_raw_scans(item, out)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "table" and isinstance(value, dict):
            out.append({"rel": value.get("table_name"), "access_type": value.get("access_type"),
                        **{k: value[k] for k in ("attached_condition", "index_condition", "key") if k in value}})
        if isinstance(value, (dict, list)):
            my_raw_scans(value, out)


def pg_plan(conn, sql, generic):
    conn.execute(text("SET plan_cache_mode = force_generic_plan" if generic else "RESET plan_cache_mode"))
    rows = conn.execute(text(f"EXPLAIN ({'GENERIC_PLAN, ' if generic else ''}FORMAT JSON) {sql}")).mappings()
    return [dict(r) for r in rows]


def canonical(normalizer, plan, source, mysql=False):
    stats = {"query_explain": [{"query_id": 1, "explain_source": source, "plan": copy.deepcopy(plan)}]}
    normalizer.normalize(stats)
    if mysql:
        normalize_predicate(stats)
    return [op for op in stats["canonic_explains"][0]["canonical_plan"]["physical_operations"] if op["type"] == "scan"]


def main():
    with PG.begin() as conn:
        conn.execute(text(PG_SETUP))
    with MY.begin() as conn:
        for sql in MY_SETUP:
            conn.execute(text(sql))

    results = []
    for name, pg_param, pg_lit, my_lit in CASES:
        my_lit = my_lit or pg_lit
        case = {"name": name, "sql": pg_lit or my_lit}
        for label, fn in (
            ("pg_generic", lambda: pg_plan_wrap(pg_param, True)),
            ("pg_literal", lambda: pg_plan_wrap(pg_lit, False)),
            ("mysql", lambda: my_plan_wrap(my_lit)),
        ):
            try:
                case[label] = fn()
            except Exception as e:
                case[label] = {"error": str(e).splitlines()[0][:200]}
        results.append(case)

    json.dump(results, open("/tmp/pred_battery.json", "w"), indent=1, default=str)
    with PG.begin() as conn:
        conn.execute(text("DROP TABLE ql_pt_orders; DROP TABLE ql_pt_customers;"))
    with MY.begin() as conn:
        conn.execute(text("DROP TABLE ql_pt_orders"))
        conn.execute(text("DROP TABLE ql_pt_customers"))
    print(f"{len(results)} casos")


def pg_plan_wrap(sql, generic):
    if sql is None:
        return None
    with PG.connect() as conn:
        plan = pg_plan(conn, sql, generic)
    raw = []
    pg_raw_scans(plan, raw)
    source = "generic" if generic else "auto_explain"
    return {"raw": raw, "scans": canonical(PostgresExplainNormalizer(), plan, source)}


def my_plan_wrap(sql):
    if sql is None:
        return None
    with MY.connect() as conn:
        row = conn.execute(text(f"EXPLAIN FORMAT=JSON {sql}")).mappings().first()
    plan = json.loads(row["EXPLAIN"])
    raw = []
    my_raw_scans(plan, raw)
    return {"raw": raw, "scans": canonical(MysqlExplainNormalizer(), plan, "sample", mysql=True)}


if __name__ == "__main__":
    main()
