"""Cobertura del emparejamiento de AP-08 en MySQL con una carga realista.

El pipeline (con el cambio de STATEMENT_HISTORY_QUERY) explica, para cada
candidato de top_impact_queries, una ejecucion que siga en
events_statements_history (explain_source = "history", con sus filas reales)
y, si no hay ninguna, el sample como antes (explain_source = "sample", filas
reales en null). Esto mide por el flujo real, leyendo los snapshots que
archiva la cola, cuantos candidatos quedan emparejados.

Carga: 16 conexiones "de aplicacion", cada una con su propia mezcla de 10
consultas de duraciones y frecuencias distintas (de busquedas por clave
primaria a escaneos completos y agregados), mas sysbench oltp_read_only con 8
hilos en otras conexiones. Asi las consultas compiten por los 10 lugares del
historial de cada conexion como en un pool de conexiones.

Relleno (--filler-types N): ademas, N tipos de consulta rapidos (busquedas por
indice, cada uno con su propio digest) por las MISMAS conexiones, que se llevan
--filler-share de las sentencias. Representa las muchas consultas de una
aplicacion que no llegan a top impact pero ocupan los lugares del historial.

Variantes (cada una reinicia ql_mysql del sandbox):
  history_10   performance_schema_events_statements_history_size = 10 (por defecto)
  history_100  = 100 (de arranque; setup_target_mysql.sql lo deja asi con PERSIST_ONLY)
Al terminar, ql_mysql vuelve a su configuracion del compose.

REQUISITOS
  - Pipeline con STATEMENT_HISTORY_QUERY (imagen reconstruida) y la base MySQL del sandbox registrada.
  - Nada mas corriendo contra el sandbox (las mediciones se pisan).

USO (desde la raiz del repo)
  python ql_sandbox/campana_cardinalidad_ap08/medir_carga_realista_mysql.py [--minutes 10] [--variants 10,100]
         [--filler-types 200] [--filler-share 0.85]
"""
import argparse
import json
import random
import statistics
import subprocess
import sys
import time

from campaign import (
    HERE, LOAD_CONTAINER, LOAD_SCRIPT, SETUP, TEARDOWN, engine_sql, log, querylens_sql, registered_db_id, run,
    stop_load,
)

SANDBOX_COMPOSE = HERE.parent / "docker-compose.yml"
APP_CONNECTIONS = 16
NOISE_THREADS = 8
LINES_PER_FILE = 6000
FILLER = "SELECT id AS fill{n:03d} FROM ql_card WHERE k = {v}"

# (alias, plantilla, generador del valor, peso cada 1.000 sentencias de una conexion)
QUERIES = [
    ("real01", "SELECT pad AS real01 FROM ql_card WHERE id = {v}", lambda r: r.randrange(1, 1_000_001), 400),
    ("real02", "SELECT id AS real02 FROM ql_card WHERE k = {v}", lambda r: r.randrange(100000), 300),
    ("real03", "SELECT id AS real03 FROM ql_card WHERE sk = {v}", lambda r: r.randrange(500000) * 2 + 1, 150),
    ("real04", "SELECT id AS real04 FROM ql_card WHERE k BETWEEN {v} AND {v} + 100", lambda r: r.randrange(99900), 80),
    ("real05", "SELECT q.id AS real05 FROM ql_card AS q JOIN ql_card_dim AS d ON d.id = q.g WHERE q.k = {v}",
     lambda r: r.randrange(100000), 50),
    ("real06", "SELECT id AS real06 FROM ql_card WHERE u = {v}", lambda r: r.randrange(1000), 8),
    ("real07", "SELECT id AS real07 FROM ql_card WHERE a = {v} AND b = {v} AND c = {v}", lambda r: r.randrange(50), 5),
    ("real08", "SELECT id AS real08 FROM ql_card WHERE sk2 = {v}",
     lambda r: 0 if r.random() < 0.1 else r.randrange(500000) * 2 + 1, 4),
    ("real09", "SELECT g, COUNT(*) AS real09 FROM ql_card WHERE u < {v} GROUP BY g", lambda r: r.randrange(1, 1000), 2),
    ("real10", "SELECT id AS real10 FROM ql_card WHERE k > {v} ORDER BY pad LIMIT 10", lambda r: r.randrange(100000), 1),
]
ALIASES = [q[0] for q in QUERIES]


def restart_mysql(history_size: int | None) -> None:
    # None: la configuracion del compose tal cual
    files = ["-f", str(SANDBOX_COMPOSE)]
    override = HERE / ".mysql_history_override.yml"
    if history_size is not None:
        command = run(["docker", "inspect", "ql_mysql", "--format", "{{json .Config.Cmd}}"]).strip()
        flags = [flag for flag in json.loads(command) if not flag.startswith("--performance-schema-events-statements-history-size")]
        flags.append(f"--performance-schema-events-statements-history-size={history_size}")
        override.write_text("services:\n  mysql:\n    command:\n" + "".join(f"      - {json.dumps(f)}\n" for f in flags),
                            encoding="utf-8")
        files += ["-f", str(override)]
    run(["docker", "compose", *files, "up", "-d", "--force-recreate", "mysql"])
    override.unlink(missing_ok=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if subprocess.run(["docker", "exec", "ql_mysql", "mysqladmin", "ping", "-uroot", "-pql_root"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(2)
    time.sleep(5)
    size = engine_sql("mysql", "SELECT @@performance_schema_events_statements_history_size;").strip()
    log(f"ql_mysql reiniciado | history_size = {size}")


def history_memory_mb() -> float:
    # memoria de performance_schema para events_statements_history (filas, texto y digest)
    raw = engine_sql("mysql", """
SELECT ROUND(SUM(CURRENT_NUMBER_OF_BYTES_USED) / 1024 / 1024, 1)
FROM performance_schema.memory_summary_global_by_event_name
WHERE EVENT_NAME LIKE 'memory/performance_schema/events_statements_history%'
  AND EVENT_NAME NOT LIKE '%history_long%';""").strip()
    return float(raw or 0)


def write_load_files(rng: random.Random, filler_types: int, filler_share: float) -> list[str]:
    weights = [q[3] for q in QUERIES]
    names = []
    for connection in range(APP_CONNECTIONS):
        lines = []
        for _ in range(LINES_PER_FILE):
            if filler_types and rng.random() < filler_share:
                lines.append(FILLER.format(n=rng.randrange(filler_types), v=rng.randrange(100000)) + ";\n")
                continue
            alias, template, value, _ = rng.choices(QUERIES, weights=weights)[0]
            lines.append(template.format(v=value(rng)) + ";\n")
        local = HERE / f".real_{connection}.sql"
        local.write_text("".join(lines), encoding="utf-8")
        name = f"/tmp/ql_real_{connection}.sql"
        run(["docker", "cp", str(local), f"{LOAD_CONTAINER}:{name}"])
        local.unlink()
        names.append(name)
    return names


def start_app_load(files: list[str], seconds: int) -> None:
    run(["docker", "exec", LOAD_CONTAINER, "rm", "-f", "/tmp/ql_cmp_stop"])
    for name in files:
        run(["docker", "exec", "-d", LOAD_CONTAINER, "bash", "/tmp/cmp_load.sh", "mysql", name, str(seconds)])


def start_noise(seconds: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["docker", "exec", LOAD_CONTAINER, "sysbench", "oltp_read_only", "--db-driver=mysql",
         "--mysql-host=mysql", "--mysql-port=3306", "--mysql-user=app_user", "--mysql-password=app_pass",
         "--mysql-db=ql_demo", "--tables=10", "--table-size=10000", f"--threads={NOISE_THREADS}",
         f"--time={seconds}", "run"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def label_for(canonic_query: str | None) -> str:
    text = (canonic_query or "").lower()
    for alias in ALIASES:
        if alias in text:
            return alias
    if " as fill" in text:
        return "relleno"
    return "sysbench" if "sbtest" in text else "otra"


def read_snapshots(db_id: str, since: str) -> list[dict]:
    raw = querylens_sql(
        "SELECT coalesce(json_agg(json_build_object("
        " 'top', message->'top_impact_queries', 'explains', message->'canonic_explains') ORDER BY msg_id), '[]')"
        f" FROM pgmq.a_analyze_job WHERE message->>'db_id' = '{db_id}' AND enqueued_at >= '{since}';"
    )
    return json.loads(raw or "[]")


def analyze(snapshots: list[dict]) -> dict:
    per_snapshot = []
    by_label: dict[str, dict] = {}
    for snapshot in snapshots:
        sources = {e.get("query_id"): e.get("explain_source") for e in snapshot.get("explains") or []}
        actual = {e.get("query_id"): (e.get("canonical_plan") or {}).get("estimates", {}).get("actual_rows")
                  for e in snapshot.get("explains") or []}
        candidates = snapshot.get("top") or []
        if not candidates:
            continue
        paired = 0
        for candidate in candidates:
            query_id = candidate.get("query_id")
            label = label_for(candidate.get("canonic_query"))
            entry = by_label.setdefault(label, {"en_top": 0, "history": 0, "sample": 0, "sin_plan": 0, "media_ms": []})
            entry["en_top"] += 1
            source = sources.get(query_id)
            if source == "history" and actual.get(query_id) is not None:
                entry["history"] += 1
                paired += 1
            elif source is not None:
                entry["sample"] += 1
            else:
                entry["sin_plan"] += 1
            if candidate.get("mean_time_ms") is not None:
                entry["media_ms"].append(candidate["mean_time_ms"])
        per_snapshot.append({"candidatos": len(candidates), "emparejados": paired})
    for entry in by_label.values():
        entry["media_ms"] = round(statistics.median(entry["media_ms"]), 2) if entry["media_ms"] else None
        explained = entry["history"] + entry["sample"]
        entry["emparejado_pct"] = round(100 * entry["history"] / explained, 1) if explained else None
    return {
        "snapshots": len(per_snapshot),
        "candidatos_por_snapshot": statistics.median(s["candidatos"] for s in per_snapshot) if per_snapshot else 0,
        "emparejados_por_snapshot": statistics.median(s["emparejados"] for s in per_snapshot) if per_snapshot else 0,
        "por_consulta": by_label,
        "detalle": per_snapshot,
    }


def run_variant(history_size: int, db_id: str, minutes: int, rng: random.Random,
                filler_types: int, filler_share: float) -> dict:
    restart_mysql(history_size)
    run(["docker", "cp", str(LOAD_SCRIPT), f"{LOAD_CONTAINER}:/tmp/cmp_load.sh"])
    files = write_load_files(rng, filler_types, filler_share)
    engine_sql("mysql", "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")
    seconds = minutes * 60
    noise = start_noise(seconds + 60)
    start_app_load(files, seconds + 60)
    time.sleep(30)  # que se arme el top
    since = querylens_sql("SELECT now();")
    log(f"history_size {history_size}: midiendo {minutes} min desde {since}")
    try:
        time.sleep(seconds)
        memory_mb = history_memory_mb()
    finally:
        stop_load()
        noise.terminate()
        run(["docker", "exec", LOAD_CONTAINER, "pkill", "-f", "sysbench"], check=False)
    time.sleep(20)
    result = analyze(read_snapshots(db_id, since))
    result["history_size"] = history_size
    result["relleno"] = {"tipos": filler_types, "proporcion": filler_share if filler_types else 0}
    result["memoria_history_mb"] = memory_mb
    log(f"history_size {history_size}: {result['snapshots']} snapshots | candidatos {result['candidatos_por_snapshot']} "
        f"| emparejados {result['emparejados_por_snapshot']} (medianas por snapshot)")
    return result


def report(results: list[dict]) -> str:
    lines = ["# Carga realista en MySQL: candidatos emparejados con su ejecucion", ""]
    for r in results:
        filler = r.get("relleno") or {}
        lines += [f"## history_size = {r['history_size']}"
                  + (f" | relleno: {filler['tipos']} tipos, {filler['proporcion']:.0%} de las sentencias"
                     if filler.get("tipos") else ""), "",
                  f"{r['snapshots']} snapshots; por snapshot (mediana): {r['candidatos_por_snapshot']} candidatos, "
                  f"{r['emparejados_por_snapshot']} emparejados. Memoria de events_statements_history: "
                  f"{r.get('memoria_history_mb')} MB.", "",
                  "| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |",
                  "|---|---|---|---|---|---|"]
        order = ALIASES + ["relleno", "sysbench", "otra"]
        for label in sorted(r["por_consulta"], key=lambda name: order.index(name) if name in order else 99):
            e = r["por_consulta"][label]
            pct = f"{e['emparejado_pct']:.0f} %" if e["emparejado_pct"] is not None else "-"
            lines.append(f"| {label} | {e['media_ms']} ms | {e['en_top']} | {e['history']} ({pct}) | "
                         f"{e['sample']} | {e['sin_plan']} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--variants", default="10,100", help="tamaños de history separados por coma")
    parser.add_argument("--filler-types", type=int, default=0, help="tipos de consulta de relleno (0 = sin relleno)")
    parser.add_argument("--filler-share", type=float, default=0.85, help="fraccion de sentencias de relleno")
    args = parser.parse_args()

    db_id = registered_db_id("mysql")
    if db_id is None:
        log("no hay una base MySQL registrada y activa")
        return 1
    log("Preparando ql_card...")
    engine_sql("mysql", SETUP["mysql"])

    rng = random.Random("carga-realista")
    results = []
    try:
        for size in (int(v) for v in args.variants.split(",")):
            results.append(run_variant(size, db_id, args.minutes, rng, args.filler_types, args.filler_share))
    finally:
        stop_load()
        restart_mysql(None)
        engine_sql("mysql", TEARDOWN)

    text = report(results)
    stamp = time.strftime("%Y%m%d_%H%M")
    (HERE / f"carga_realista_{stamp}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                                       encoding="utf-8")
    (HERE / f"carga_realista_{stamp}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
