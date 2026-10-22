"""Evaluacion de la Opcion C (seccion 9 del Segundo Informe).

Corre con un venv que tenga sus dependencias (desde telemetry_pipeline/):
    python ci/eval_opcion_c.py <setup|p1|p3|p4|p5|all> --out ../docs/evidencia/eval_opcion_c

Usa el sandbox (ql_postgres, ql_mysql, ql_sysbench). Cada prueba deja su JSON en
--out. El pipeline corre en proceso (Orchestrator.run_pipeline), sin encolar.
El brazo "generic" de Postgres es el mismo pipeline con AutoExplainStage sin
planes: solo EXPLAIN (GENERIC_PLAN).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import pymysql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors.factory import Engine_Factory  # noqa: E402
from config.connections import get_connection_mysql, get_connection_postgres  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402
from stages import auto_explain as ae  # noqa: E402
from stages import explain_normalizer as en  # noqa: E402
from stages import pg_log  # noqa: E402

SEED = 20261012
CONN = {"postgres": get_connection_postgres, "mysql": get_connection_mysql}


# ---------------------------------------------------------------- conexiones
def pg(user="ql_user", password="ql_pass"):
    c = psycopg2.connect(host="localhost", port=5432, dbname="ql_demo", user=user, password=password)
    c.autocommit = True
    return c


@contextmanager
def pgc():
    c = pg()
    try:
        yield c
    finally:
        c.close()


def my(user="app_user", password="app_pass", db="ql_demo"):
    return pymysql.connect(host="127.0.0.1", port=3307, user=user, password=password,
                           database=db, autocommit=True)


def my_root():
    return my("root", "ql_root", None)


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except Exception:
            return None


def reset_stats():
    with pgc() as c:
        q(c, "SELECT pg_stat_statements_reset()")
    c = my_root()
    q(c, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
    c.close()


# ---------------------------------------------------------------- pipeline
@contextmanager
def generic_only():
    original = ae.AutoExplainStage.execute

    def no_plans(self, stats, conn, *args, **kwargs):
        stats["auto_explain_plans"] = {}
        return stats

    ae.AutoExplainStage.execute = no_plans
    try:
        yield
    finally:
        ae.AutoExplainStage.execute = original


def run_pipeline(dialect, arm="c"):
    collector = Engine_Factory().create_collector(dialect, CONN[dialect]())
    if arm == "generic":
        with generic_only():
            return Orchestrator(collector).run_pipeline()
    return Orchestrator(collector).run_pipeline()


def sources(payload):
    return {str(e["query_id"]): e for e in payload.get("canonic_explains") or []}


def signature(canonical):
    """Lo que ve el detector: secuencia de operaciones con tipo, acceso e indice."""
    return [
        (op.get("type"), op.get("access_method"), op.get("index_name"))
        for op in (canonical or {}).get("physical_operations", [])
    ]


def normalize(dialect, plan, query_id="ref"):
    normalizer = en.EXPLAIN_NORMALIZERS[dialect]
    normalizer = normalizer() if isinstance(normalizer, type) else normalizer
    stats = {"query_explain": [{"query_id": query_id, "explain_source": "reference", "plan": plan}]}
    out = normalizer.normalize(stats)
    return out["canonic_explains"][0]["canonical_plan"]


def pg_reference(sql):
    with pgc() as c:
        rows = q(c, "EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
    return normalize("postgres", [{"QUERY PLAN": rows[0][0]}])


def my_reference(sql):
    c = my()
    plan = json.loads(q(c, "EXPLAIN FORMAT=JSON " + sql)[0][0])
    tree = q(c, "EXPLAIN ANALYZE " + sql)[0][0]
    c.close()
    return normalize("mysql", plan), tree


def pg_qid(marker):
    with pgc() as c:
        rows = q(c, "SELECT s.queryid::text FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid "
                    "WHERE r.rolname = 'ql_user' AND s.toplevel AND s.query NOT ILIKE 'EXPLAIN%%' "
                    "AND s.query LIKE %s", (f"%{marker}%",))
    assert len(rows) <= 1, (marker, rows)
    return rows[0][0] if rows else None


def my_digest(marker):
    c = my_root()
    rows = q(c, "SELECT CONCAT(SCHEMA_NAME, '/', DIGEST), QUERY_SAMPLE_TEXT "
                "FROM performance_schema.events_statements_summary_by_digest "
                "WHERE SCHEMA_NAME = 'ql_demo' AND DIGEST_TEXT NOT LIKE 'EXPLAIN%%' AND DIGEST_TEXT LIKE %s",
             (f"%{marker}%",))
    c.close()
    assert len(rows) <= 1, (marker, rows)
    return (rows[0][0], rows[0][1]) if rows else (None, None)


# ---------------------------------------------------------------- setup
SKEW_ROWS = 1_000_000


def setup():
    with pgc() as c:
        if not q(c, "SELECT to_regclass('public.ql_skew') IS NOT NULL")[0][0]:
            q(c, "CREATE TABLE ql_skew (id serial PRIMARY KEY, status int NOT NULL, note text NOT NULL)")
            q(c, f"""INSERT INTO ql_skew (status, note)
                     SELECT CASE WHEN random() < 0.97 THEN 0 ELSE 1 + floor(random() * 300)::int END,
                            substr('nmopq', 1 + floor(random() * 5)::int, 1) || md5(g::text)
                     FROM generate_series(1, {SKEW_ROWS}) g""")
            q(c, "CREATE INDEX ql_skew_status ON ql_skew (status)")
        q(c, "VACUUM ANALYZE ql_skew")
        pg_dist = q(c, "SELECT count(*) FILTER (WHERE status = 0), count(DISTINCT status), count(*) FROM ql_skew")[0]
    c = my()
    if not q(c, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ql_demo' AND table_name='ql_skew'")[0][0]:
        q(c, "CREATE TABLE ql_skew (id int AUTO_INCREMENT PRIMARY KEY, status int NOT NULL, note varchar(40) NOT NULL)")
        q(c, "SET SESSION cte_max_recursion_depth = 1000000")
        q(c, f"""INSERT INTO ql_skew (status, note)
                 WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < {SKEW_ROWS})
                 SELECT CASE WHEN RAND() < 0.97 THEN 0 ELSE 1 + FLOOR(RAND() * 300) END,
                        CONCAT(SUBSTRING('nmopq', 1 + FLOOR(RAND() * 5), 1), MD5(n))
                 FROM seq""")
        q(c, "CREATE INDEX ql_skew_status ON ql_skew (status)")
    q(c, "ANALYZE TABLE ql_skew")
    my_dist = q(c, "SELECT SUM(status = 0), COUNT(DISTINCT status), COUNT(*) FROM ql_skew")[0]
    c.close()
    return {"rows": SKEW_ROWS,
            "postgres": {"status0": pg_dist[0], "distinct": pg_dist[1], "total": pg_dist[2]},
            "mysql": {"status0": int(my_dist[0]), "distinct": my_dist[1], "total": my_dist[2]}}


# ---------------------------------------------------------------- prueba 1
def battery(reps=5, noise=50):
    subprocess.run(["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", str(reps), str(noise)],
                   check=True, stdout=subprocess.DEVNULL)


def my_sample_info():
    c = my_root()
    limit = q(c, "SELECT @@performance_schema_max_sql_text_length")[0][0]
    rows = q(c, "SELECT CONCAT(SCHEMA_NAME, '/', DIGEST), LENGTH(QUERY_SAMPLE_TEXT) "
                "FROM performance_schema.events_statements_summary_by_digest")
    c.close()
    return limit, {d: (n or 0) for d, n in rows if d}


def p1_sample(i):
    battery()
    rec = {"sample": i}
    p_c = run_pipeline("postgres", "c")
    p_g = run_pipeline("postgres", "generic")
    src_c, src_g = sources(p_c), sources(p_g)
    rec["postgres"] = [
        {"query_id": str(t["query_id"]), "mean_time_ms": t.get("mean_time_ms"),
         "c": (src_c.get(str(t["query_id"])) or {}).get("explain_source"),
         "generic": (src_g.get(str(t["query_id"])) or {}).get("explain_source")}
        for t in p_c.get("top_impact_queries") or []
    ]
    p_m = run_pipeline("mysql", "c")
    src_m = sources(p_m)
    limit, lens = my_sample_info()
    rec["mysql"] = [
        {"query_id": str(t["query_id"]), "mean_time_ms": t.get("mean_time_ms"),
         "sample_len": lens.get(t["query_id"], 0),
         "full_text": 0 < lens.get(t["query_id"], 0) < limit,
         "c": (src_m.get(str(t["query_id"])) or {}).get("explain_source")}
        for t in p_m.get("top_impact_queries") or []
    ]
    rec["mysql_text_limit"] = limit
    return rec


def p1(samples=3, threshold0=True):
    out = {"protocol": "battery.sh 5 reps, 50 noise; 3 muestras", "runs": []}
    for i in range(1, samples + 1):
        out["runs"].append({"threshold_ms": 50, **p1_sample(i)})
        print(f"p1 sample {i} (50 ms) ok", flush=True)
    if threshold0:
        with pgc() as c:
            q(c, "ALTER ROLE ql_user SET auto_explain.log_min_duration = 0")
        try:
            for i in range(1, samples + 1):
                rec = p1_sample(i)
                out["runs"].append({"threshold_ms": 0, **{k: v for k, v in rec.items() if k != "mysql"}})
                print(f"p1 sample {i} (0 ms) ok", flush=True)
        finally:
            with pgc() as c:
                q(c, "ALTER ROLE ql_user RESET auto_explain.log_min_duration")
    return out


# ---------------------------------------------------------------- prueba 3
RARE = list(range(1, 301))
FREQ_SHAPES = {
    "f1": "SELECT count(*) AS f1_cnt, max(length(note)) FROM ql_skew WHERE status = {x}",
    "f2": "SELECT id AS f2_id FROM ql_skew WHERE status = {x} AND note LIKE 'n%'",
    "f3": "SELECT note AS f3_note FROM ql_skew WHERE status = {x} ORDER BY note DESC LIMIT 10",
}
RARE_SHAPES = {
    "r1": "SELECT id AS r1_id, note FROM ql_skew WHERE status = {x} ORDER BY id",
    "r2": "SELECT count(*) AS r2_cnt FROM ql_skew WHERE status = {x} AND id > 1000",
    "r3": "SELECT max(note) AS r3_max FROM ql_skew WHERE status = {x} OR status = {y}",
}


def p3_workload(rng):
    plan = []
    for name in FREQ_SHAPES:
        plan += [(name, 0, None)] * 4 + [(name, rng.choice(RARE), None) for _ in range(12)]
    for name in RARE_SHAPES:
        plan += [(name, rng.choice(RARE), rng.choice(RARE)) for _ in range(12)]
    rng.shuffle(plan)
    return plan


def sql_of(name, x, y):
    tpl = FREQ_SHAPES.get(name) or RARE_SHAPES[name]
    return tpl.format(x=x, y=y)


def p3_rep(rep, rng):
    reset_stats()
    work = p3_workload(rng)
    slowest = {"postgres": {}, "mysql": {}}
    pc, mc = pg(), my()
    for name, x, y in work:
        sql = sql_of(name, x, y)
        for dialect, conn in (("postgres", pc), ("mysql", mc)):
            t0 = time.perf_counter()
            q(conn, sql)
            ms = (time.perf_counter() - t0) * 1000
            best = slowest[dialect].get(name)
            if best is None or ms > best[0]:
                slowest[dialect][name] = (ms, x, y)
    pc.close()
    mc.close()
    p_c, p_g, p_m = run_pipeline("postgres", "c"), run_pipeline("postgres", "generic"), run_pipeline("mysql", "c")
    sc, sg, sm = sources(p_c), sources(p_g), sources(p_m)
    cand_pg = {str(t["query_id"]) for t in p_c.get("top_impact_queries") or []}
    cand_my = {str(t["query_id"]) for t in p_m.get("top_impact_queries") or []}
    rows = []
    for name in list(FREQ_SHAPES) + list(RARE_SHAPES):
        kind = "frecuente" if name in FREQ_SHAPES else "rara"
        marker = re.search(r"AS (\w+)", sql_of(name, 0, 0)).group(1)
        # postgres
        qid = pg_qid(marker)
        ms, x, y = slowest["postgres"][name]
        ref = signature(pg_reference(sql_of(name, x, y)))
        c_e, g_e = sc.get(qid), sg.get(qid)
        rows.append({
            "rep": rep, "engine": "postgres", "shape": name, "kind": kind, "query_id": qid,
            "ref_literal": x, "ref_ms": round(ms, 1), "ref": ref, "candidate": qid in cand_pg,
            "c_source": c_e and c_e["explain_source"],
            "c_match": bool(c_e) and signature(c_e["canonical_plan"]) == ref,
            "c_sig": c_e and signature(c_e["canonical_plan"]),
            "generic_source": g_e and g_e["explain_source"],
            "generic_match": bool(g_e) and signature(g_e["canonical_plan"]) == ref,
            "generic_sig": g_e and signature(g_e["canonical_plan"]),
        })
        # mysql
        digest, sample = my_digest(marker)
        ms, x, y = slowest["mysql"][name]
        ref_c, tree = my_reference(sql_of(name, x, y))
        ref = signature(ref_c)
        m_e = sm.get(digest)
        rows.append({
            "rep": rep, "engine": "mysql", "shape": name, "kind": kind, "query_id": digest,
            "ref_literal": x, "ref_ms": round(ms, 1), "ref": ref, "ref_tree": tree.splitlines()[:3],
            "sample_text": sample, "candidate": digest in cand_my,
            "sample_has_ref_literal": bool(sample) and f"status = {x}" in sample,
            "c_source": m_e and m_e["explain_source"],
            "c_match": bool(m_e) and signature(m_e["canonical_plan"]) == ref,
            "c_sig": m_e and signature(m_e["canonical_plan"]),
        })
    return rows


def p3(reps=3):
    rng = random.Random(SEED)
    rows = []
    for rep in range(1, reps + 1):
        rows += p3_rep(rep, rng)
        print(f"p3 rep {rep} ok", flush=True)
    return {"seed": SEED, "reps": reps, "rows": rows}


# ---------------------------------------------------------------- prueba 4
# generate_series($1, $2) y $1 + $2: GENERIC_PLAN falla con 42725/42P18 (commit 02efb38).
PG_AMBIG_SLOW = [
    "SELECT count(*) AS amb1_cnt FROM ql_skew s JOIN generate_series(1, 400000) g ON g = s.id",
    "SELECT sum(g) AS amb2_sum FROM generate_series(1, 3000000) g",
    "SELECT max(note) AS amb3_max, 5 + 7 FROM ql_skew WHERE status = 0",
    "SELECT count(*) AS amb4_cnt FROM ql_skew WHERE id > 10 + 20 AND note LIKE '%7a%'",
]
PG_AMBIG_FAST = [
    "SELECT count(*) AS amb5_cnt FROM generate_series(1, 100) g",
    "SELECT id AS amb6_id, 1 + 1 FROM ql_skew WHERE id = 7",
]


def my_text_shapes():
    long_list = ", ".join(f"'{'x' * 24}{i:03d}'" for i in range(60))
    short_list = ", ".join(f"'{'x' * 24}{i:03d}'" for i in range(3))
    long_ = [f"SELECT id AS lng{i}_id FROM ql_skew WHERE status = {i} AND note NOT IN ({long_list})" for i in range(1, 5)]
    short = [f"SELECT id AS sht{i}_id FROM ql_skew WHERE status = {i} AND note NOT IN ({short_list})" for i in range(1, 5)]
    return long_, short


def p4():
    reset_stats()
    with pgc() as c:
        for sql in PG_AMBIG_SLOW + PG_AMBIG_FAST:
            for _ in range(3):
                q(c, sql)
    long_, short = my_text_shapes()
    c = my()
    for sql in long_ + short:
        for _ in range(3):
            q(c, sql)
    c.close()
    p_c, p_g, p_m = run_pipeline("postgres", "c"), run_pipeline("postgres", "generic"), run_pipeline("mysql", "c")
    sc, sg, sm = sources(p_c), sources(p_g), sources(p_m)
    tops_pg = {str(t["query_id"]): t for t in p_c.get("top_impact_queries") or []}
    rows = []
    for group, sqls in (("ambigua_lenta", PG_AMBIG_SLOW), ("ambigua_rapida", PG_AMBIG_FAST)):
        for sql in sqls:
            qid = pg_qid(re.search(r"AS (\w+)", sql).group(1))
            with pgc() as c:
                mean = q(c, "SELECT mean_exec_time FROM pg_stat_statements WHERE queryid::text = %s", (qid,))
            rows.append({"engine": "postgres", "group": group, "query_id": qid,
                         "mean_ms": round(mean[0][0], 1) if mean else None,
                         "candidate": qid in tops_pg,
                         "c": (sc.get(qid) or {}).get("explain_source"),
                         "generic": (sg.get(qid) or {}).get("explain_source")})
    tops_my = {str(t["query_id"]) for t in p_m.get("top_impact_queries") or []}
    for group, sqls in (("texto_largo", long_), ("texto_corto", short)):
        for sql in sqls:
            digest, sample = my_digest(re.search(r"AS (\w+)", sql).group(1))
            rows.append({"engine": "mysql", "group": group, "query_id": digest, "sql_bytes": len(sql.encode()),
                         "sample_bytes": len((sample or "").encode()), "candidate": digest in tops_my,
                         "c": (sm.get(digest) or {}).get("explain_source")})
    health = {"postgres_c": p_c.get("pipeline_health"), "mysql": p_m.get("pipeline_health")}
    return {"rows": rows, "health": health}


# ---------------------------------------------------------------- prueba 5
TARGET_PG = "SELECT count(*) AS tgt_cnt, max(length(note)) FROM ql_skew WHERE status = 0"
TARGET_MY = "SELECT count(*) AS tgt_cnt, MAX(LENGTH(note)) FROM ql_skew WHERE status = 0"


def pg_log_bytes():
    with pgc() as c:
        return sum(r[1] for r in q(c, "SELECT name, size FROM querylens.log_files()"))


def p5_pg(cells_mb=(0, 0.5, 1.0, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0)):
    rows = []
    for mb in cells_mb:
        reset_stats()
        with pgc() as c:
            q(c, TARGET_PG)
        before = pg_log_bytes()
        goal = int(mb * 1024 * 1024)
        plans = 0
        if goal:
            with pgc() as c:
                q(c, "SET auto_explain.log_min_duration = 0")
                i = 0
                while pg_log_bytes() - before < goal:
                    for _ in range(200):
                        i += 1
                        q(c, f"SELECT id, k FROM sbtest1 WHERE id = {i % 10000 + 1}")
                    plans += 200
        written = pg_log_bytes() - before
        p_c = run_pipeline("postgres", "c")
        qid = pg_qid("tgt_cnt")
        src = (sources(p_c).get(qid) or {}).get("explain_source")
        rows.append({"filler_goal_mb": mb, "filler_bytes": written, "filler_plans": plans,
                     "target_source": src})
        print(f"p5 pg {mb} MB -> {src}", flush=True)
    return rows


def p5_my(cells=(0, 5000, 9000, 9900, 10000, 10100, 12000)):
    rows = []
    for n in cells:
        reset_stats()
        root = my_root()
        lost0 = int(q(root, "SHOW GLOBAL STATUS LIKE 'Performance_schema_digest_lost'")[0][1])
        c = my()
        for i in range(n):
            q(c, f"SELECT {i} AS fill_{i}")
        rows_before = q(root, "SELECT COUNT(*) FROM performance_schema.events_statements_summary_by_digest")[0][0]
        q(c, TARGET_MY)
        c.close()
        lost1 = int(q(root, "SHOW GLOBAL STATUS LIKE 'Performance_schema_digest_lost'")[0][1])
        size = q(root, "SELECT @@performance_schema_digests_size")[0][0]
        root.close()
        digest, sample = my_digest("tgt_cnt")
        p_m = run_pipeline("mysql", "c")
        src = (sources(p_m).get(digest) or {}).get("explain_source") if digest else None
        rows.append({"distinct_digests_before": n, "digest_rows_before_target": rows_before,
                     "digests_size": size, "digest_lost_delta": lost1 - lost0,
                     "target_has_row": digest is not None, "target_source": src})
        print(f"p5 my {n} -> row={digest is not None} src={src}", flush=True)
    return rows


def p5():
    return {"postgres": p5_pg(), "mysql": p5_my(), "log_window_bytes": pg_log.LOG_WINDOW_BYTES}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["setup", "p1", "p3", "p4", "p5", "all"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    steps = ["setup", "p1", "p3", "p4", "p5"] if args.what == "all" else [args.what]
    for step in steps:
        t0 = time.time()
        result = globals()[step]()
        path = os.path.join(args.out, f"{step}.json")
        with open(path, "w") as f:
            json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
                       "elapsed_s": round(time.time() - t0, 1), "result": result}, f, indent=2, default=str)
        print(f"{step} -> {path}", flush=True)


if __name__ == "__main__":
    main()
