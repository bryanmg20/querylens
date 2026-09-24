import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy import text

PIPELINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE)
from config.connections import get_connection_querylens_db
from config.logs import LOG_SOURCES
from stages.log_reader import fast_signature, parse_mysql_slow_log, parse_pg_csvlog
from compare_real_query_mechanics import RESULTS_DIR, engine_of

PY = os.path.join(PIPELINE, "venv", "Scripts", "python.exe")

PARSERS = {"postgres": parse_pg_csvlog, "mysql": parse_mysql_slow_log}
PATHS = {k: v["path"] for k, v in LOG_SOURCES.items()}

# (lineas fisicas de cola del log que lee el pipeline, queries de ruido posterior)
# Cartografia del umbral de la ventana de produccion (50k por defecto). Cada query
# de ruido genera 1 linea fisica en el csvlog de PostgreSQL (heuristico) y ~5 en
# el slow log de MySQL, por eso el cruce del umbral de formateado aparece primero
# en MySQL. Para cruzar cantidades grandes sin gastar horas de bateria, las celdas
# con noise > BULK_NOISE_THRESHOLD anexan el relleno directamente al log con el
# mismo formato real (ver write_bulk_filler).
BULK_NOISE_THRESHOLD = 2000

GRID = [
    (50000, 0),        # control: ventana de produccion (por defecto), sin relleno
    (50000, 900),      # relleno normal via bateria -> deberia seguir 100 %
    (50000, 10000),    # masivo: ya entierra MySQL (~5 lineas/query x 10k = ~50k fisicas); pg (1 linea/query) no
    (50000, 20000),    # masivo: MySQL enterrada con margen; PostgreSQL todavia 100 %
    (50000, 48000),    # masivo: justo antes del umbral de PostgreSQL -> sigue 100 %
    (50000, 49990),    # masivo: en el borde del umbral de PostgreSQL -> ya cae a 0 % (corte abrupto)
    (50000, 51000),    # masivo: apenas por encima del umbral de PostgreSQL
    (50000, 60000),    # masivo: por encima del umbral con margen
]

_FILLER_TS = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000 UTC")


def write_bulk_filler(noise):
    """Simula ruido posterior masivo escribiendo las lineas directamente en los
    logs con el mismo formato que producen los motores. Mismo efecto sobre la
    ventana: el bloque de queries pesadas queda 'enterrado' solo si el total de
    lo escrito despues las supera desde el final."""
    pg_row = ",".join([
        _FILLER_TS, "ql_sysbench", "ql_user", "ql_demo", "0", "", "app",
        "ql_user", "0", "0", "0", "0", "0",
        "duration: 1.234 ms statement: SELECT c FROM sbtest1 WHERE id = {i}",
        "Parameters: $1 = '{i}'", "", "", "", "", "SELECT c FROM sbtest1 WHERE id = $1",
    ])
    with open(PATHS["postgres"], "a", encoding="utf-8") as f:
        for start in range(0, noise, 1000):
            f.write("".join(pg_row.format(i=i) + "\n" for i in range(start, min(start + 1000, noise))))
    _mysql_filler(PATHS["mysql"], noise)


def _mysql_filler(path, noise):
    lines = []
    for i in range(noise):
        lines.append("# Time: " + _FILLER_TS.replace(" UTC", "Z").replace(" ", "T") + "\n")
        lines.append("# User@Host: app_user[app_user] @  [172.17.0.2]\n")
        lines.append("# Query_time: 0.001234  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 1\n")
        lines.append("SET timestamp=1750000000;\n")
        lines.append(f"SELECT c FROM sbtest1 WHERE id = {i};\n")
    with open(path, "a", encoding="utf-8") as fp:
        fp.write("".join(lines))


def _mysql_filler(path, noise):
    lines = []
    for i in range(noise):
        lines.append("# Time: " + _FILLER_TS.replace(" UTC", "Z").replace(" ", "T") + "\n")
        lines.append("# User@Host: app_user[app_user] @  [172.17.0.2]\n")
        lines.append("# Query_time: 0.001234  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 1\n")
        lines.append("SET timestamp=1750000000;\n")
        lines.append(f"SELECT c FROM sbtest1 WHERE id = {i};\n")
    with open(path, "a", encoding="utf-8") as fp:
        fp.write("".join(lines))

_REPORT_HEAD = "=" * 72


def new_cell():
    return {
        "samples": [],
        "by_op": {},
    }


def analyze_payload(payload, engine, tail_lines, since):
    """Mide para un snapshot: cuantas top-impact recupero el pipeline (v2) y
    cuantas tenian su firma dentro de la ventana de log que SI pudo leer
    (cobertura de ventana), separando el fracaso por ventana del resto."""
    entries = PARSERS[engine](PATHS[engine], tail_lines=tail_lines, since=since)
    window_sigs = {e.canonical_text for e in entries}

    total = v2 = en_ventana = fuera = 0
    for cand in payload.get("top_impact_queries") or []:
        total += 1
        sig = fast_signature(cand.get("query_text") or "")
        matched = sig in window_sigs
        v = bool(cand.get("real_query_found"))
        if v:
            v2 += 1
        elif matched:
            en_ventana += 1  # estaba en la ventana pero el pipeline no la marco
        else:
            fuera += 1       # firma fuera de la ventana leida (enterrada) o distinta
    return {
        "total": total,
        "v2": v2,
        "en_ventana_no_v2": en_ventana,
        "fuera": fuera,
        "n_log_entries": len(entries),
    }


def run_cell(tail_lines, noise, reps, samples):
    cell = {}
    bulk = noise > BULK_NOISE_THRESHOLD
    battery_noise = 0 if bulk else noise
    for s in range(1, samples + 1):
        since = datetime.now(timezone.utc).isoformat(timespec="seconds")
        os.environ["QL_LOG_TAIL_LINES"] = str(tail_lines)
        os.environ["QL_LOG_SINCE"] = since
        subprocess.run(
            ["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", str(reps), str(battery_noise)],
            check=True,
        )
        if bulk:
            write_bulk_filler(noise)
        subprocess.run([PY, os.path.join(PIPELINE, "main.py")], check=True)

        with get_connection_querylens_db().connect() as conn:
            rows = conn.execute(
                text("SELECT msg_id, message FROM pgmq.q_analyze_job ORDER BY msg_id DESC LIMIT 2")
            ).mappings().all()

        for r in rows:
            payload = r["message"]
            if isinstance(payload, (str, bytes, bytearray)):
                payload = json.loads(payload)
            e = engine_of(payload)
            if e == "unknown" or e not in PARSERS:
                continue
            d = analyze_payload(payload, e, tail_lines, since)
            holder = cell.setdefault(e, new_cell())
            holder["samples"].append(d)
            print(
                f"  [tail={tail_lines} noise={noise}] {e} muestra={s} "
                f"v2={d['v2']}/{d['total']} en_ventana_sin_v2={d['en_ventana_no_v2']} "
                f"fuera={d['fuera']} entradas_in_window={d['n_log_entries']}",
                flush=True,
            )
    return cell


def _rate(ds, key, denom_key="total"):
    vals = [100 * d[key] / max(1, d[denom_key]) for d in ds]
    return statistics.mean(vals) if vals else 0.0


def build_report(agg):
    lines = [ _REPORT_HEAD ]
    lines.append("ABLACION DE LA RECUPERACION POR LOGS  (ventana de cola x ruido)")
    lines.append("v2  = recuperadas por el pipeline (real_query_found)")
    lines.append("ventana = firma de la top-impact presente en la ventana leida")
    lines.append("fuera = top-impact cuya firma quedo enterrada fuera de la ventana")
    lines.append(_REPORT_HEAD)

    for (tail_lines, noise), cell in agg.items():
        lines.append(f"\n:: ventana={tail_lines} lineas | ruido posterior={noise} | muestras={len(next(iter(cell.values()), {}).get('samples', [])) if cell else 0}")
        for engine, holder in cell.items():
            ds = holder["samples"]
            n = len(ds)
            total = sum(d["total"] for d in ds)
            v2 = sum(d["v2"] for d in ds)
            vent = sum(d["en_ventana_no_v2"] for d in ds)
            fuera = sum(d["fuera"] for d in ds)
            rates_v2 = [100 * d["v2"] / max(1, d["total"]) for d in ds]
            rates_vent = [100 * (d["v2"] + d["en_ventana_no_v2"]) / max(1, d["total"]) for d in ds]
            mean_entries = statistics.mean([d["n_log_entries"] for d in ds]) if ds else 0
            lines.append(f"  {engine:<9} v2={v2}/{total} ({statistics.mean(rates_v2):.0f}%; "
                         f"min/max {min(rates_v2) if rates_v2 else 0:.0f}/{max(rates_v2) if rates_v2 else 0:.0f}) | "
                         f"cobertura de ventana {statistics.mean(rates_vent):.0f}% | "
                         f"en_ventana_sin_v2={vent} fuera={fuera} | "
                         f"entradas promedio en ventana={mean_entries:.0f}")
    lines.append("\n" + _REPORT_HEAD)
    return "\n".join(lines)


def save_results(agg, args, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "meta": {
            "script": os.path.basename(__file__),
            "muestras_por_celda": args.samples,
            "reps_bateria": args.reps,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "grid": [{"tail_lines": t, "noise": n} for t, n in GRID],
            "mecanica": "v2 = LogsBackfillStage actual (colas del log via QL_LOG_TAIL_LINES, default 50000); "
                        "noise > " + str(BULK_NOISE_THRESHOLD) + " se anexa masivamente al log en lugar de bateria real",
        },
        "por_celda": {},
    }
    for key, cell in agg.items():
        payload["por_celda"][f"tail={key[0]}_noise={key[1]}"] = {
            e: {"muestras": h["samples"], "totales": {
                "top_impact": sum(d["total"] for d in h["samples"]),
                "v2": sum(d["v2"] for d in h["samples"]),
                "en_ventana_sin_v2": sum(d["en_ventana_no_v2"] for d in h["samples"]),
                "fuera": sum(d["fuera"] for d in h["samples"]),
                "n_log_entries_mean": statistics.mean(
                    [d["n_log_entries"] for d in h["samples"]]) if h["samples"] else 0,
            }} for e, h in cell.items()
        }

    json_path = os.path.join(out_dir, "ablate_recovery_window.json")
    txt_path = os.path.join(out_dir, "ablate_recovery_window.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(build_report(agg) + "\n")
    return json_path, txt_path


def main():
    parser = argparse.ArgumentParser(description=(
        "Ablacion de la recuperacion por logs: varia la ventana de cola "
        "(QL_LOG_TAIL_LINES) y el ruido posterior para ver cuando v2 pierde."))
    parser.add_argument("samples", nargs="?", type=int, default=2, help="muestras por celda")
    parser.add_argument("reps", nargs="?", type=int, default=3, help="reps de la bateria")
    parser.add_argument("--out", default=RESULTS_DIR)
    args = parser.parse_args()
    assert args.samples >= 1 and args.reps >= 1

    agg = {}
    for tail_lines, noise in GRID:
        print(f"\n>>> CELDA ventana={tail_lines} ruido={noise}")
        try:
            cell = run_cell(tail_lines, noise, args.reps, args.samples)
        except Exception as ex:
            print(f"  ERROR en celda tail={tail_lines} noise={noise}: {ex}")
            continue
        if cell:
            agg[(tail_lines, noise)] = cell

    report = build_report(agg)
    print("\n" + report)
    json_path, txt_path = save_results(agg, args, args.out)
    print(f"Resultados guardados:\n  {json_path}\n  {txt_path}")


if __name__ == "__main__":
    main()