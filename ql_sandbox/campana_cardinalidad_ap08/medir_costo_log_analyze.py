"""Costo de auto_explain.log_analyze en Postgres (medicion para AP-08).

Con log_analyze = on, Postgres cuenta las filas reales de cada nodo en TODAS
las consultas (no sabe de antemano cuales van a pasar log_min_duration), y
las que pasan quedan en el log con estimadas y reales. Esto mide cuanto cuesta
ese conteo con la misma carga en tres modos, alternados por rondas:
  off                 lo de hoy (solo plan estimado en el log)
  analyze_sin_tiempo  log_analyze = on, log_timing = off  (la propuesta)
  analyze_con_tiempo  log_analyze = on, log_timing = on   (referencia)
El modo se aplica por sesion con PGOPTIONS, sin tocar la configuracion del servidor.

Cargas (4 sesiones, cada una sin pausa):
  oltp    sysbench oltp_read_only sobre sbtest (consultas cortas por indice)
  scan    SELECT id FROM ql_card WHERE a = ? AND b = ? AND c = ? (escaneo de 1M filas)
  join    la misma con JOIN a ql_card_dim (mas nodos por consulta)

USO (desde la raiz del repo)
  python ql_sandbox/campana_cardinalidad_ap08/medir_costo_log_analyze.py [--rounds 3] [--seconds 30]
"""
import argparse
import json
import random
import re
import statistics
import sys
import time

from campaign import (
    HERE, LOAD_CONTAINER, LOAD_SCRIPT, SETUP, TEARDOWN, WORKERS, engine_sql, log, run, stop_load,
)

MODES = {
    "off": "-c auto_explain.log_analyze=off",
    "analyze_sin_tiempo": "-c auto_explain.log_analyze=on -c auto_explain.log_timing=off",
    "analyze_con_tiempo": "-c auto_explain.log_analyze=on -c auto_explain.log_timing=on",
}
LOADS = {
    "scan": "SELECT id AS costo_scan FROM ql_card WHERE a = {v} AND b = {v} AND c = {v}",
    "join": "SELECT q.id AS costo_join FROM ql_card AS q JOIN ql_card_dim AS d ON d.id = q.g "
            "WHERE q.a = {v} AND q.b = {v} AND q.c = {v}",
}


def sysbench(pgoptions: str, seconds: int) -> dict:
    out = run([
        "docker", "exec", "-e", f"PGOPTIONS={pgoptions}", LOAD_CONTAINER,
        "sysbench", "oltp_read_only", "--db-driver=pgsql", "--pgsql-host=postgres", "--pgsql-port=5432",
        "--pgsql-user=ql_user", "--pgsql-password=ql_pass", "--pgsql-db=ql_demo",
        "--tables=10", "--table-size=10000", f"--threads={WORKERS}", f"--time={seconds}", "run",
    ])
    tps = float(re.search(r"transactions:\s+\d+\s+\(([\d.]+) per sec", out).group(1))
    p95 = float(re.search(r"95th percentile:\s+([\d.]+)", out).group(1))
    return {"por_segundo": tps, "p95_ms": p95}


def psql_load(load: str, pgoptions: str, seconds: int) -> dict:
    alias = f"costo_{load}"
    file_name = f"/tmp/ql_costo_{load}.sql"
    engine_sql("postgres", "SELECT pg_stat_statements_reset();")
    # start_load no pasa variables de entorno: se lanza aca con PGOPTIONS
    run(["docker", "exec", LOAD_CONTAINER, "rm", "-f", "/tmp/ql_cmp_stop"])
    for _ in range(WORKERS):
        run(["docker", "exec", "-d", "-e", f"PGOPTIONS={pgoptions}", LOAD_CONTAINER,
             "bash", "/tmp/cmp_load.sh", "postgres", file_name, str(seconds + 30)])
    time.sleep(seconds)
    raw = engine_sql(
        "postgres",
        "SELECT coalesce(sum(calls), 0), coalesce(sum(total_exec_time) / nullif(sum(calls), 0), 0) "
        f"FROM pg_stat_statements WHERE query ILIKE '%{alias}%';",
    ).strip()
    stop_load()
    calls, mean_ms = raw.split("|")
    return {"por_segundo": int(calls) / seconds, "media_ms": float(mean_ms)}


def prepare_files() -> None:
    rng = random.Random("costo")
    for load, template in LOADS.items():
        local = HERE / f".costo_{load}.sql"
        local.write_text("".join(template.format(v=rng.randrange(50)) + ";\n" for _ in range(200)), encoding="utf-8")
        run(["docker", "cp", str(local), f"{LOAD_CONTAINER}:/tmp/ql_costo_{load}.sql"])
        local.unlink()


def logged_plan_has_actual_rows() -> bool:
    # comprobacion de que con analyze_sin_tiempo el log trae filas reales
    out = run(["docker", "exec", "ql_postgres", "bash", "-c",
               "tail -c 2000000 /var/lib/postgresql/data/log/$(ls -t /var/lib/postgresql/data/log | grep json | head -1)"])
    for line in reversed(out.splitlines()):
        if "costo_scan" in line and "Actual Rows" in line:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()

    log("Preparando ql_card...")
    engine_sql("postgres", SETUP["postgres"])
    run(["docker", "cp", str(LOAD_SCRIPT), f"{LOAD_CONTAINER}:/tmp/cmp_load.sh"])
    prepare_files()

    samples = {(load, mode): [] for load in ("oltp", *LOADS) for mode in MODES}
    actual_rows_seen = None
    try:
        for round_number in range(1, args.rounds + 1):
            for mode, pgoptions in MODES.items():
                result = sysbench(pgoptions, args.seconds)
                samples[("oltp", mode)].append(result)
                log(f"ronda {round_number} | oltp | {mode}: {result}")
                for load in LOADS:
                    result = psql_load(load, pgoptions, args.seconds)
                    samples[(load, mode)].append(result)
                    log(f"ronda {round_number} | {load} | {mode}: {result}")
                    if load == "scan" and mode == "analyze_sin_tiempo" and actual_rows_seen is None:
                        actual_rows_seen = logged_plan_has_actual_rows()
                        log(f"   el log trae Actual Rows: {actual_rows_seen}")
    finally:
        stop_load()
        engine_sql("postgres", TEARDOWN)

    lines = ["# Costo de auto_explain.log_analyze en Postgres", "",
             f"{args.rounds} rondas de {args.seconds} s por carga y modo, {WORKERS} sesiones, modos alternados.",
             f"El log trae `Actual Rows` con analyze_sin_tiempo: {actual_rows_seen}", "",
             "| Carga | Modo | Por segundo (mediana) | vs off | Latencia (mediana) |", "|---|---|---|---|---|"]
    summary = {}
    for load in ("oltp", *LOADS):
        base = statistics.median(s["por_segundo"] for s in samples[(load, "off")])
        for mode in MODES:
            rate = statistics.median(s["por_segundo"] for s in samples[(load, mode)])
            key = "p95_ms" if load == "oltp" else "media_ms"
            latency = statistics.median(s[key] for s in samples[(load, mode)])
            change = (rate - base) / base * 100 if base else 0.0
            summary[f"{load}/{mode}"] = {"por_segundo": rate, "cambio_pct": change, key: latency}
            unit = "transacciones" if load == "oltp" else "consultas"
            lines.append(f"| {load} | {mode} | {rate:,.1f} {unit} | {change:+.1f} % | "
                         f"{latency:,.2f} ms ({'p95' if load == 'oltp' else 'media'}) |")
    text = "\n".join(lines) + "\n"

    stamp = time.strftime("%Y%m%d_%H%M")
    (HERE / f"costo_log_analyze_{stamp}.json").write_text(
        json.dumps({"muestras": {f"{k[0]}/{k[1]}": v for k, v in samples.items()}, "resumen": summary},
                   indent=1, ensure_ascii=False), encoding="utf-8")
    (HERE / f"costo_log_analyze_{stamp}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
