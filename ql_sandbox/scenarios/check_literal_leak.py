"""
QUERYLENS — prueba M6: 0 literales persistidos.

Inyecta en cada motor consultas con literales marcadores unicos (un texto y un
numero generados en cada corrida), corre la ruta real de QueryLens
(telemetry_pipeline -> cola PGMQ -> detection_engine) y busca los marcadores en
todo el almacenamiento de QueryLens: cada fila de cada tabla de usuario de
querylens_db (incluidas las tablas de la cola y su archivo), y ademas en los
logs del pipeline y del detection_engine.

Para que un 0 signifique algo, la prueba exige tres controles positivos:
  1. Los marcadores llegaron a las fuentes del motor: el registro de
     auto_explain en Postgres y QUERY_SAMPLE_TEXT en MySQL los traen.
  2. El pipeline tomo las consultas como candidatas y obtuvo su plan.
  3. El buscador encuentra en el almacenamiento el alias de las consultas
     (m6_...), que no es un literal y si se guarda.

REQUISITOS
  - Sandbox levantado (ql_sandbox: docker compose up -d) y el compose de la raiz.
  - Un python con las dependencias de telemetry_pipeline y detection_engine.

USO (desde la raiz del repo)
  python ql_sandbox/scenarios/check_literal_leak.py
  python ql_sandbox/scenarios/check_literal_leak.py --engine postgres
  python ql_sandbox/scenarios/check_literal_leak.py --scan-only qlm6abc123,86420017

  --scan-only: no inyecta ni corre nada; solo busca los marcadores dados. Sirve
  para revisar el almacenamiento al final de la campana de validacion.

SALIDA
  exit 0: 0 apariciones y controles positivos OK
  exit 1: aparecio algun marcador (fuga)
  exit 2: inconcluso (fallo un control positivo)
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = ROOT / "telemetry_pipeline"
ENGINE_DIR = ROOT / "detection_engine"
sys.path.insert(0, str(PIPELINE_DIR))

import psycopg2  # noqa: E402
import pymysql  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from config.connections import get_connection_mysql, get_connection_postgres  # noqa: E402
from main import run_engine  # noqa: E402

QUEUE = "analyze_job"
TABLE_ROWS = 1_000_000
LOG_FILES = [
    PIPELINE_DIR / "logs" / "pipeline.log",
    PIPELINE_DIR / "logs" / "detection_engine.log",
    ENGINE_DIR / "logs" / "detection_engine.log",
]


# ---------------------------------------------------------------- conexiones
def pg_target():
    c = psycopg2.connect(host="localhost", port=5432, dbname="ql_demo", user="ql_user", password="ql_pass")
    c.autocommit = True
    return c


def my_target(user="app_user", password="app_pass", db="ql_demo"):
    return pymysql.connect(host="127.0.0.1", port=3307, user=user, password=password,
                           database=db, autocommit=True)


def querylens_db():
    c = psycopg2.connect(
        host=os.getenv("DB_HOST") or "127.0.0.1",
        port=int(os.getenv("DB_PORT") or 5433),
        dbname=os.getenv("QUERYLENS_DB") or "ql_demo",
        user=os.getenv("QUERYLENS_USER") or "ql_user",
        password=os.getenv("QUERYLENS_PASSWORD") or "ql_pass",
    )
    c.autocommit = True
    return c


def run(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except Exception:
            return None


# ---------------------------------------------------------------- consultas
def queries(text_marker, num_marker):
    """Literales marcadores en cada posicion donde un literal puede aparecer."""
    return [
        f"SELECT count(*) AS m6_eq FROM ql_m6 WHERE note = '{text_marker}'",
        f"SELECT count(*) AS m6_like FROM ql_m6 WHERE note LIKE '%{text_marker}%'",
        f"SELECT count(*) AS m6_num FROM ql_m6 WHERE id = {num_marker} OR note = '{text_marker}x'",
        f"SELECT count(*) AS m6_in FROM ql_m6 WHERE note IN ('{text_marker}a', '{text_marker}b')",
        f"SELECT '{text_marker}' AS m6_sel, count(*) FROM ql_m6 WHERE id > {num_marker} - {num_marker}",
        f"UPDATE ql_m6 SET note = note WHERE note = '{text_marker}u'",
    ]


def setup_tables():
    c = pg_target()
    if not run(c, "SELECT to_regclass('public.ql_m6') IS NOT NULL")[0][0]:
        run(c, "CREATE TABLE ql_m6 (id bigint PRIMARY KEY, note text NOT NULL)")
        run(c, f"INSERT INTO ql_m6 SELECT g, md5(g::text) FROM generate_series(1, {TABLE_ROWS}) g")
        run(c, "ANALYZE ql_m6")
    c.close()
    c = my_target()
    if not run(c, "SELECT COUNT(*) FROM information_schema.tables "
                  "WHERE table_schema = 'ql_demo' AND table_name = 'ql_m6'")[0][0]:
        run(c, "CREATE TABLE ql_m6 (id bigint PRIMARY KEY, note varchar(40) NOT NULL)")
        run(c, "SET SESSION cte_max_recursion_depth = 2000000")
        run(c, f"INSERT INTO ql_m6 WITH RECURSIVE seq (n) AS "
               f"(SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < {TABLE_ROWS}) "
               f"SELECT n, MD5(n) FROM seq")
        run(c, "ANALYZE TABLE ql_m6")
    c.close()


def drop_tables():
    c = pg_target()
    run(c, "DROP TABLE IF EXISTS ql_m6")
    c.close()
    c = my_target()
    run(c, "DROP TABLE IF EXISTS ql_m6")
    c.close()


def reset_stats(engines):
    if "postgres" in engines:
        c = pg_target()
        run(c, "SELECT pg_stat_statements_reset()")
        c.close()
    if "mysql" in engines:
        c = my_target("root", "ql_root", None)
        run(c, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
        c.close()


def inject(engines, sqls, reps=3):
    for dialect in engines:
        c = pg_target() if dialect == "postgres" else my_target()
        for _ in range(reps):
            for sql in sqls:
                run(c, sql)
        c.close()


# ---------------------------------------------------------------- controles
def pg_log_has(marker, window=4 * 1024 * 1024):
    c = pg_target()
    hits = 0
    for name, size in run(c, "SELECT name, size FROM querylens.log_files() ORDER BY modification DESC"):
        start = max(0, size - window)
        chunk = run(c, "SELECT querylens.read_log(%s, %s, %s)", (name, start, size - start))[0][0]
        hits += bytes(chunk or b"").count(marker.encode())
        break
    c.close()
    return hits


def my_sample_has(marker):
    c = my_target("root", "ql_root", None)
    n = run(c, "SELECT COUNT(*) FROM performance_schema.events_statements_summary_by_digest "
               "WHERE QUERY_SAMPLE_TEXT LIKE %s", (f"%{marker}%",))[0][0]
    c.close()
    return n


def captured(payload):
    """Candidatas m6_ que el pipeline tomo, y la fuente del plan de cada una."""
    cands = (payload.get("top_impact_queries") or []) + (payload.get("non_explainable_candidates") or [])
    sources = {str(e.get("query_id")): e.get("explain_source") for e in payload.get("canonic_explains") or []}
    out = []
    for cand in cands:
        text = cand.get("query_text") or ""
        if "m6_" in text or "ql_m6" in text:
            out.append({"query_id": str(cand.get("query_id")), "query_text": text[:120],
                        "plan_source": sources.get(str(cand.get("query_id")))})
    return out


# ---------------------------------------------------------------- ruta real
def run_pipeline(engines):
    """telemetry_pipeline real: collect -> validate -> encolar en PGMQ."""
    factories = {"postgres": get_connection_postgres, "mysql": get_connection_mysql}
    msg_ids = {}
    for dialect in engines:
        msg_ids[dialect] = run_engine(dialect, factories[dialect])
    return msg_ids


def queued_payloads(msg_ids):
    c = querylens_db()
    out = {}
    for dialect, msg_id in msg_ids.items():
        row = run(c, f"SELECT message FROM pgmq.q_{QUEUE} WHERE msg_id = %s", (msg_id,))
        out[dialect] = row[0][0] if row else {}
    c.close()
    return out


def drain_detection_engine(max_jobs=500):
    """detection_engine real, un job por llamada, hasta vaciar la cola."""
    c = querylens_db()
    processed = 0
    for _ in range(max_jobs):
        pending = run(c, f"SELECT count(*) FROM pgmq.q_{QUEUE}")[0][0]
        if not pending:
            break
        subprocess.run([sys.executable, "main.py", "--oneshot"], cwd=ENGINE_DIR, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processed += 1
    c.close()
    return processed


# ---------------------------------------------------------------- busqueda
def storage_tables(conn):
    return run(conn, """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY 1, 2""")


def scan_storage(markers):
    c = querylens_db()
    hits = {m: {} for m in markers}
    tables = storage_tables(c)
    for schema, table in tables:
        for marker in markers:
            n = run(c, f'SELECT count(*) FROM "{schema}"."{table}" t WHERE t::text LIKE %s',
                    (f"%{marker}%",))[0][0]
            if n:
                hits[marker][f"{schema}.{table}"] = n
    c.close()
    return hits, [f"{s}.{t}" for s, t in tables]


def scan_logs(markers):
    hits = {m: {} for m in markers}
    for path in LOG_FILES:
        if not path.exists():
            continue
        data = path.read_bytes()
        for marker in markers:
            n = data.count(marker.encode())
            if n:
                hits[marker][str(path.relative_to(ROOT))] = n
    return hits


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Prueba M6: 0 literales persistidos.")
    ap.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    ap.add_argument("--scan-only", help="marcadores separados por coma; solo busca")
    ap.add_argument("--keep-table", action="store_true", help="no borrar ql_m6 al terminar")
    ap.add_argument("--out", help="archivo JSON con el resultado")
    args = ap.parse_args()

    if args.scan_only:
        markers = [m.strip() for m in args.scan_only.split(",") if m.strip()]
        storage, tables = scan_storage(markers)
        logs = scan_logs(markers)
        leaks = sum(sum(v.values()) for v in storage.values())
        print(json.dumps({"markers": markers, "tables_scanned": len(tables),
                          "storage_hits": storage, "log_hits": logs}, indent=2))
        sys.exit(1 if leaks else 0)

    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]
    text_marker = f"qlm6{secrets.token_hex(6)}"
    num_marker = str(8_640_000_000 + secrets.randbelow(9_999_999))
    markers = [text_marker, num_marker]
    print(f"marcadores: texto={text_marker} numero={num_marker}", flush=True)

    setup_tables()
    reset_stats(engines)
    inject(engines, queries(text_marker, num_marker))

    sources = {}
    if "postgres" in engines:
        sources["postgres_auto_explain_log"] = pg_log_has(text_marker)
    if "mysql" in engines:
        sources["mysql_query_sample_text"] = my_sample_has(text_marker)

    msg_ids = run_pipeline(engines)
    payloads = queued_payloads(msg_ids)
    capture = {d: captured(p) for d, p in payloads.items()}
    jobs = drain_detection_engine()

    storage, tables = scan_storage(markers)
    alias_hits, _ = scan_storage(["m6_eq"])
    logs = scan_logs(markers)

    if not args.keep_table:
        drop_tables()
        # Sin la tabla, sus consultas siguen en pg_stat_statements y en los
        # digests: el proximo ciclo del pipeline las tomaria como candidatas y
        # su EXPLAIN fallaria.
        reset_stats(engines)

    leaks = sum(sum(v.values()) for v in storage.values())
    log_leaks = sum(sum(v.values()) for v in logs.values())
    controls = {
        "marcador_en_fuentes": all(v > 0 for v in sources.values()),
        "candidatas_capturadas": all(len(capture.get(d, [])) > 0 for d in engines),
        "buscador_ve_el_alias": sum(alias_hits["m6_eq"].values()) > 0,
    }
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engines": engines,
        "markers": {"text": text_marker, "number": num_marker},
        "sources_with_marker": sources,
        "msg_ids": msg_ids,
        "captured_candidates": capture,
        "detection_jobs_processed": jobs,
        "tables_scanned": tables,
        "storage_hits": storage,
        "alias_hits_control": alias_hits["m6_eq"],
        "log_hits": logs,
        "controls": controls,
        "storage_leaks": leaks,
        "log_leaks": log_leaks,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, default=str))

    print(json.dumps({k: result[k] for k in ("sources_with_marker", "msg_ids", "detection_jobs_processed",
                                             "storage_hits", "log_hits", "controls")}, indent=2, default=str))
    print(f"candidatas capturadas: " + ", ".join(f"{d}={len(v)}" for d, v in capture.items()))
    print(f"tablas revisadas: {len(tables)} | apariciones en almacenamiento: {leaks} | en logs: {log_leaks}")
    if leaks:
        print("RESULTADO: FUGA")
        sys.exit(1)
    if not all(controls.values()):
        print("RESULTADO: INCONCLUSO (fallo un control positivo)")
        sys.exit(2)
    print("RESULTADO: 0 literales persistidos")


if __name__ == "__main__":
    main()
