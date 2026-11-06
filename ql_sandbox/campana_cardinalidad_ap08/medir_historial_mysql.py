"""Cobertura del historial de MySQL para emparejar estimacion y realidad (AP-08).

Dos formas de obtener las filas reales de la misma ejecucion que se explica:
  sample     buscar la ejecucion de QUERY_SAMPLE_TEXT en
             events_statements_history (10 por hilo) o events_statements_history_long
             (10.000 en total, apagado por defecto en MySQL; en el sandbox esta
             prendido). Se identifica por DIGEST + SQL_TEXT + TIMER_WAIT.
  historial  elegir directamente una ejecucion de ese digest que este en
             events_statements_history (la mas lenta de las que haya) y explicar
             su texto: sus ROWS_SENT estan siempre.

Esto mide, consultando como lo haria el pipeline en momentos al azar mientras
corre la carga, en que porcentaje de las consultas funciona cada forma. Cada
consulta es independiente, asi que el resultado no depende del intervalo de
snapshots.

Cargas (4 sesiones sin pausa, como la bateria), cada una sola y con ruido
(sysbench oltp_read_only con 8 hilos en paralelo). En las de "1 de cada N" la
consulta comparte la conexion con otras N - 1 distintas, como en un pool de
conexiones de una aplicacion.

USO (desde la raiz del repo)
  python ql_sandbox/campana_cardinalidad_ap08/medir_historial_mysql.py [--seconds 60] [--every 5]
"""
import argparse
import json
import random
import statistics
import subprocess
import sys
import time

from campaign import (
    HERE, LOAD_CONTAINER, LOAD_SCRIPT, SCENARIOS, SETUP, TEARDOWN, engine_sql, log, run, start_load, stop_load,
)

# (escenario de la bateria, 1 de cada N sentencias de la conexion)
LOADS = (("card01", 1), ("card04", 1), ("card07", 1), ("card01", 50), ("card04", 50), ("card04", 200))
NOISE_THREADS = 8
FILLER = "SELECT id AS relleno FROM ql_card WHERE k = {v}"


def digest_sample(alias: str) -> dict | None:
    raw = engine_sql("mysql", f"""
SELECT JSON_OBJECT(
  'digest', DIGEST, 'text', QUERY_SAMPLE_TEXT, 'timer_wait', QUERY_SAMPLE_TIMER_WAIT,
  'age_s', TIMESTAMPDIFF(MICROSECOND, QUERY_SAMPLE_SEEN, NOW(6)) / 1e6, 'calls', COUNT_STAR)
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST_TEXT LIKE '%{alias}%' AND QUERY_SAMPLE_TEXT IS NOT NULL
ORDER BY COUNT_STAR DESC LIMIT 1;""").strip()
    return json.loads(raw) if raw else None


def find_sample_in(table: str, sample: dict) -> int | None:
    # ROWS_SENT de la ejecucion del sample, o None si ya no esta en esa tabla
    text = sample["text"].replace("\\", "\\\\").replace("'", "\\'")
    raw = engine_sql("mysql", f"""
SELECT ROWS_SENT FROM performance_schema.{table}
WHERE DIGEST = '{sample["digest"]}' AND TIMER_WAIT = {sample["timer_wait"]} AND SQL_TEXT = '{text}'
LIMIT 1;""").strip()
    return int(raw) if raw else None


def slowest_in_history(digest: str) -> tuple[int, int] | None:
    # (ejecuciones del digest en history, ROWS_SENT de la mas lenta), o None si no hay ninguna
    raw = engine_sql("mysql", f"""
SELECT COUNT(*) OVER (), ROWS_SENT FROM performance_schema.events_statements_history
WHERE DIGEST = '{digest}' AND SQL_TEXT IS NOT NULL AND END_EVENT_ID IS NOT NULL
ORDER BY TIMER_WAIT DESC LIMIT 1;""").strip()
    if not raw:
        return None
    count, rows = raw.split("\t")
    return int(count), int(rows)


def start_noise(seconds: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["docker", "exec", LOAD_CONTAINER, "sysbench", "oltp_read_only", "--db-driver=mysql",
         "--mysql-host=mysql", "--mysql-port=3306", "--mysql-user=app_user", "--mysql-password=app_pass",
         "--mysql-db=ql_demo", "--tables=10", "--table-size=10000", f"--threads={NOISE_THREADS}",
         f"--time={seconds}", "run"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def measure(scenario, one_in: int, noise: bool, seconds: int, every: int, rng: random.Random) -> dict:
    alias = scenario.alias.replace("card", "hist")
    file_name = f"/tmp/ql_hist_{alias}.sql"
    local = HERE / f".{alias}.sql"
    lines = []
    for _ in range(2000):
        if one_in == 1 or rng.randrange(one_in) == 0:
            lines.append(scenario.template.format(alias=alias, v=scenario.value(rng)))
        else:
            lines.append(FILLER.format(v=rng.randrange(100000)))
    local.write_text("".join(line + ";\n" for line in lines), encoding="utf-8")
    run(["docker", "cp", str(local), f"{LOAD_CONTAINER}:{file_name}"])
    local.unlink()

    engine_sql("mysql", "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")
    noise_process = start_noise(seconds + 15) if noise else None
    start_load("mysql", file_name, seconds + 15)
    time.sleep(10)  # que haya un sample
    polls = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            sample = digest_sample(alias)
            if sample:
                history = find_sample_in("events_statements_history", sample)
                history_long = find_sample_in("events_statements_history_long", sample)
                own = slowest_in_history(sample["digest"])
                polls.append({
                    "edad_sample_s": sample["age_s"],
                    "sample_en_history": history is not None,
                    "sample_en_history_long": history_long is not None,
                    "historial": own is not None,
                    "ejecuciones_en_history": own[0] if own else 0,
                    "rows_sent_historial": own[1] if own else None,
                })
            time.sleep(every)
    finally:
        stop_load()
        if noise_process:
            noise_process.terminate()
            run(["docker", "exec", LOAD_CONTAINER, "pkill", "-f", "sysbench"], check=False)

    total = len(polls)

    def pct(key):
        return 100 * sum(p[key] for p in polls) / total if total else 0

    return {
        "carga": scenario.alias, "descripcion": scenario.descripcion, "una_de_cada": one_in, "ruido": noise,
        "consultas": total,
        "sample_en_history_pct": pct("sample_en_history"),
        "sample_en_history_long_pct": pct("sample_en_history_long"),
        "historial_pct": pct("historial"),
        "ejecuciones_en_history_mediana": statistics.median(p["ejecuciones_en_history"] for p in polls) if polls else 0,
        "edad_sample_mediana_s": statistics.median(p["edad_sample_s"] for p in polls) if polls else None,
        "filas_reales_historial": sorted({p["rows_sent_historial"] for p in polls if p["rows_sent_historial"] is not None}),
        "muestras": polls,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--every", type=int, default=5)
    args = parser.parse_args()

    consumers = engine_sql("mysql", "SELECT NAME, ENABLED FROM performance_schema.setup_consumers "
                                    "WHERE NAME LIKE 'events_statements_history%';")
    log(f"consumidores: {consumers.strip()}")
    log("Preparando ql_card...")
    engine_sql("mysql", SETUP["mysql"])
    run(["docker", "cp", str(LOAD_SCRIPT), f"{LOAD_CONTAINER}:/tmp/cmp_load.sh"])

    by_alias = {s.alias: s for s in SCENARIOS}
    rng = random.Random("historial")
    results = []
    try:
        for alias, one_in in LOADS:
            for noise in (False, True):
                r = measure(by_alias[alias], one_in, noise, args.seconds, args.every, rng)
                results.append(r)
                log(f"{alias} 1/{one_in} {'con ruido' if noise else 'sola'}: "
                    f"sample history {r['sample_en_history_pct']:.0f} % | "
                    f"sample history_long {r['sample_en_history_long_pct']:.0f} % | "
                    f"historial {r['historial_pct']:.0f} % ({r['ejecuciones_en_history_mediana']} ejec.) | "
                    f"filas {r['filas_reales_historial'][:5]}")
    finally:
        stop_load()
        engine_sql("mysql", TEARDOWN)

    lines = ["# Cobertura del historial de MySQL para AP-08", "",
             f"Consultas cada {args.every} s durante {args.seconds} s por carga.", "",
             "| Carga | 1 de cada | Ruido | Consultas | Sample en history | Sample en history_long "
             "| Alguna ejecucion en history | Ejecuciones en history (mediana) | Edad del sample |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r['carga']} {r['descripcion']} | {r['una_de_cada']} | {'sí' if r['ruido'] else 'no'} | "
            f"{r['consultas']} | {r['sample_en_history_pct']:.0f} % | {r['sample_en_history_long_pct']:.0f} % | "
            f"{r['historial_pct']:.0f} % | {r['ejecuciones_en_history_mediana']} | {r['edad_sample_mediana_s']:.1f} s |")
    text = "\n".join(lines) + "\n"
    stamp = time.strftime("%Y%m%d_%H%M")
    (HERE / f"historial_mysql_{stamp}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                                        encoding="utf-8")
    (HERE / f"historial_mysql_{stamp}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
