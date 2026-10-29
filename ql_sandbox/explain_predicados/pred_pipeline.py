"""Bateria de predicados por el pipeline REAL: carga desde ql_sysbench, snapshot
con pipeline_one_engine.py dentro del servicio pipeline y lectura del JSON que
quedo en la cola (pgmq analyze_job de querylens_db).

pred_battery.py llama al EXPLAIN y al normalizador directamente; este script
comprueba lo mismo con el recorrido completo (colector -> seleccion -> EXPLAIN
-> normalizacion -> cola).

Usa los CASES y el setup de pred_battery.py, con ql_pt_orders de 1.000.000 de
filas para que los escaneos completos de Postgres superen los 50 ms de
auto_explain. Como top_impact_queries son las 10 consultas con mas tiempo, corre
lotes de 8 casos y reinicia las estadisticas antes de cada lote
(pg_stat_statements_reset / TRUNCATE de events_statements_summary_by_digest).

Requisitos: sandbox (ql_postgres, ql_mysql, ql_sysbench) y compose de la raiz
(servicios pipeline, postgres y detection-engine) levantados.

USO (desde la raiz del repo)
  python ql_sandbox/explain_predicados/pred_pipeline.py [--engine postgres|mysql|both] [--seconds 30]

Salida: ql_sandbox/explain_predicados/snapshots_pipeline/<motor>_lote<N>.json
(el mensaje encolado completo) y resumen.txt (predicados de cada scan).
"""
import argparse
import functools
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT_DIR = HERE / "snapshots_pipeline"
PIPELINE_ONE_ENGINE = ROOT / "ql_sandbox" / "scenarios" / "pipeline_one_engine.py"

LOAD_CONTAINER = "ql_sysbench"
STOP_FILE = "/tmp/ql_pred_stop"
BATCH_SIZE = 8
WORKERS = 2
ROWS = 1_000_000


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(cmd, input_text=None, check=True) -> str:
    result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}... fallo: {result.stderr.strip()[:500]}")
    return result.stdout


@functools.cache
def compose_container(service):
    # Id del contenedor de <service> en el compose de la raiz. Sin container_name
    # fijo el nombre depende del proyecto (docker compose -p, COMPOSE_PROJECT_NAME).
    out = run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "ps", "-q", service]).strip()
    if not out:
        raise RuntimeError(f"el servicio {service} del compose de la raiz no esta corriendo")
    return out


def load_battery():
    # CASES y setup de pred_battery.py sin importarlo (importa el pipeline)
    tree = ast.parse((HERE / "pred_battery.py").read_text(encoding="utf-8"))
    namespace: dict = {}
    wanted = {"O", "C", "CASES", "PG_SETUP", "MY_SETUP"}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = set()
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Tuple):
                names.update(elt.id for elt in target.elts if isinstance(elt, ast.Name))
        if names & wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "pred_battery.py", "exec"), namespace)
    pg_setup = namespace["PG_SETUP"].replace("generate_series(1, 100000)", f"generate_series(1, {ROWS})")
    my_setup = [
        sql.replace("n < 100000", f"n < {ROWS}").replace("= 200000", f"= {ROWS * 2}")
        for sql in namespace["MY_SETUP"]
    ]
    return namespace["CASES"], pg_setup, my_setup


def exec_engine_sql(engine: str, sql: str) -> str:
    if engine == "postgres":
        cmd = ["docker", "exec", "-i", "ql_postgres", "psql", "-U", "ql_user", "-d", "ql_demo",
               "-v", "ON_ERROR_STOP=1", "-q", "-At"]
    else:
        cmd = ["docker", "exec", "-i", "ql_mysql", "mysql", "-uroot", "-pql_root", "ql_demo", "-N"]
    return run(cmd, input_text=sql)


def querylens_sql(sql: str) -> str:
    return run(["docker", "exec", "-i", compose_container("postgres"), "psql", "-U", "ql_user", "-d", "ql_demo", "-At"],
               input_text=sql).strip()


def reset_stats(engine: str) -> None:
    if engine == "postgres":
        exec_engine_sql(engine, "SELECT pg_stat_statements_reset();")
    else:
        exec_engine_sql(engine, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")


def start_load(engine: str, seconds: int) -> None:
    run(["docker", "exec", LOAD_CONTAINER, "rm", "-f", STOP_FILE])
    for _ in range(WORKERS):
        run(["docker", "exec", "-d", LOAD_CONTAINER, "bash", "/tmp/pred_load.sh", engine, "/tmp/ql_pred_batch.sql",
             str(seconds)])


def stop_load() -> None:
    run(["docker", "exec", LOAD_CONTAINER, "touch", STOP_FILE], check=False)
    kill_script = (
        'for p in /proc/[0-9]*; do '
        'grep -qa "[q]l_pred_workload" "$p/cmdline" 2>/dev/null && kill "${p#/proc/}" 2>/dev/null; '
        'done; true'
    )
    run(["docker", "exec", LOAD_CONTAINER, "bash", "-c", kill_script], check=False)


def run_pipeline(engine: str) -> int:
    run(["docker", "cp", str(PIPELINE_ONE_ENGINE), f"{compose_container('pipeline')}:/tmp/pipeline_one_engine.py"])
    output = run(["docker", "exec", "-e", "QL_PIPELINE_DIR=/app", "-w", "/app", compose_container("pipeline"),
                  "python", "/tmp/pipeline_one_engine.py", engine])
    # "<motor> | db_id=... | encolado con ID: N"
    return int(output.strip().splitlines()[-1].rsplit(":", 1)[1])


def read_message(msg_id: int, timeout: int = 120) -> dict:
    # el detection_engine lo consume y lo archiva; se lee de la cola o del archivo
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = querylens_sql(
            f"SELECT message::text FROM pgmq.q_analyze_job WHERE msg_id = {msg_id} "
            f"UNION ALL SELECT message::text FROM pgmq.a_analyze_job WHERE msg_id = {msg_id} LIMIT 1;"
        )
        if raw:
            return json.loads(raw)
        time.sleep(2)
    raise RuntimeError(f"no aparecio el mensaje {msg_id} en la cola")


def summarize(snapshot: dict) -> list[str]:
    # predicados de cada scan de las consultas de la bateria que recibieron plan
    lines = []
    candidates = {str(c.get("query_id")): c for c in snapshot.get("top_impact_queries") or []}
    for explain in snapshot.get("canonic_explains") or []:
        candidate = candidates.get(str(explain.get("query_id"))) or {}
        canonic = candidate.get("canonic_query") or "?"
        if "ql_pt_" not in canonic:
            continue
        lines.append(f"  [{explain.get('explain_source')}] {canonic}")
        for op in explain["canonical_plan"]["physical_operations"]:
            if op.get("type") != "scan":
                continue
            lines.append(
                f"      scan {op.get('relation')} ({op.get('table_name')}) {op.get('access_method')} "
                f"idx={op.get('index_name')} est={op.get('estimated_rows')} pred={op.get('predicate')}"
            )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    parser.add_argument("--seconds", type=int, default=30, help="carga antes de tomar el snapshot")
    args = parser.parse_args()
    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]

    cases, pg_setup, my_setup = load_battery()
    OUT_DIR.mkdir(exist_ok=True)
    run(["docker", "cp", str(HERE / "pred_load.sh"), f"{LOAD_CONTAINER}:/tmp/pred_load.sh"])
    summary: list[str] = []

    for engine in engines:
        log(f"===== {engine}: preparando tablas ({ROWS:,} filas en ql_pt_orders)")
        if engine == "postgres":
            exec_engine_sql(engine, pg_setup)
        else:
            exec_engine_sql(engine, ";\n".join(my_setup) + ";")

        # (nombre, sql con literales) que aplican a este motor
        engine_cases = []
        for name, _pg_param, pg_lit, my_lit in cases:
            sql = pg_lit if engine == "postgres" else (my_lit or pg_lit)
            if sql:
                engine_cases.append((name, sql))
        batches = [engine_cases[i:i + BATCH_SIZE] for i in range(0, len(engine_cases), BATCH_SIZE)]

        try:
            for number, batch in enumerate(batches, start=1):
                names = ", ".join(name.split(" ")[0] for name, _ in batch)
                log(f"{engine} lote {number}/{len(batches)}: casos {names}")
                batch_file = OUT_DIR / "_lote.sql"
                batch_file.write_text("".join(f"{sql};\n" for _, sql in batch), encoding="utf-8")
                run(["docker", "cp", str(batch_file), f"{LOAD_CONTAINER}:/tmp/ql_pred_batch.sql"])

                reset_stats(engine)
                start_load(engine, args.seconds + 120)
                time.sleep(args.seconds)
                msg_id = run_pipeline(engine)
                stop_load()
                snapshot = read_message(msg_id)

                out_file = OUT_DIR / f"{engine}_lote{number}.json"
                out_file.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
                log(f"  msg_id={msg_id} -> {out_file.relative_to(ROOT)}")

                summary.append(f"=== {engine} lote {number} (msg_id {msg_id}): casos {names}")
                summary.extend(summarize(snapshot))
                time.sleep(3)
        finally:
            stop_load()
            log(f"{engine}: eliminando tablas")
            exec_engine_sql(engine, "DROP TABLE IF EXISTS ql_pt_orders; DROP TABLE IF EXISTS ql_pt_customers;")
            (OUT_DIR / "_lote.sql").unlink(missing_ok=True)

    (OUT_DIR / "resumen.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    log(f"resumen: {(OUT_DIR / 'resumen.txt').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
