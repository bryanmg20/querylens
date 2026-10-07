"""Sobrecosto del pipeline bajo carga continua (PrimerInforme: < 5%).

Cronometra las queries REALES del load (continuous_load.sh) con y sin
pipeline y compara p95 de latencia. Sin datos => SIN_DATO, no CUMPLE.

Metodo por motor (aislado): load solo del motor -> ventana A;
load + run_engine cada EXTRACT_EVERY_S (1 disparo al medio si cadencia
>= ventana) -> ventana B. Sobrecosto = (p95_B - p95_A) / p95_A * 100.

Uso: python measure_overhead.py [MEASURE_S] [quick] [ROUNDS] [EXTRACT_EVERY_S]
Exit: 0=CUMPLE, 1=error/SIN_DATO, 2=NO CUMPLE
"""

import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

from sqlalchemy import text  # noqa: E402

from ci.regenerate_goldens import PIPELINE_FINGERPRINT  # noqa: E402
from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)

UMBRAL_SOBRECOSTO_PCT = 5.0
DEFAULT_MEASURE_S, DEFAULT_ROUNDS, DEFAULT_EXTRACT_EVERY_S = 15.0, 3, 10.0
WARMUP_S, STAT_INTERVAL = 8.0, 1.5
MIN_SAMPLES_WARN, P95_SD_WARN_PTS = 20, 5.0
CONTAINERS = ["ql_postgres", "ql_mysql"]
LABEL = {"ql_postgres": "postgres", "ql_mysql": "mysql"}
FOCUS = {"postgres": "ql_postgres", "mysql": "ql_mysql"}
MONITOR_PG_ROLE, BYTES_GB = "querylens_monitor", 1e9
LOAD_PID, LOAD_METRICS = "/tmp/ql_load.pid", "/tmp/ql_load_metrics.csv"
LOGS = PIPELINE / "logs"


# --- metricas puras ---------------------------------------------------------

def percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def latency_stats(rows, window_s):
    """p50/p95/QPS solo de queries OK (status=0); fallos aparte."""
    ok = [r["duration_ms"] for r in rows if r.get("status") == 0]
    fails = sum(1 for r in rows if r.get("status") != 0)
    if not ok:
        return {"n": 0, "failures": fails, "p50_ms": None, "p95_ms": None,
                "mean_ms": None, "qps": None}
    s = sorted(ok)
    return {"n": len(ok), "failures": fails, "p50_ms": percentile(s, 50),
            "p95_ms": percentile(s, 95), "mean_ms": statistics.mean(ok),
            "qps": (len(ok) / window_s) if window_s else None}


def relative_overhead_pct(base, con):
    if base is None or con is None or base <= 0:
        return None
    return (con - base) / base * 100.0


def qps_overhead_pct(qps_base, qps_con):
    if qps_base is None or qps_con is None or qps_base <= 0:
        return None
    return (qps_base - qps_con) / qps_base * 100.0


def cpu_overhead_pct(cpu_sola, cpu_con):
    if cpu_sola is None or cpu_con is None:
        return None
    return cpu_con - cpu_sola


def verdict(overhead_pct, umbral=UMBRAL_SOBRECOSTO_PCT):
    if overhead_pct is None:
        return "SIN_DATO"
    return "CUMPLE" if overhead_pct < umbral else "NO CUMPLE"


def overall_verdict(per_engine):
    states = [v.get("verdict") for v in per_engine.values() if v]
    if not states:
        return "SIN_DATO"
    if "NO CUMPLE" in states:
        return "NO CUMPLE"
    if "SIN_DATO" in states:
        return "SIN_DATO"
    return "CUMPLE"


def _nn(vals):
    return [v for v in vals if v is not None]


def mean_or_none(vals):
    vals = _nn(vals)
    return statistics.mean(vals) if vals else None


def stdev_or_none(vals):
    vals = _nn(vals)
    return statistics.stdev(vals) if len(vals) >= 2 else None


def min_or_none(vals):
    vals = _nn(vals)
    return min(vals) if vals else None


def max_or_none(vals):
    vals = _nn(vals)
    return max(vals) if vals else None


# --- docker stats (secundario) ----------------------------------------------

def _pct(s):
    return float(s.rstrip("%")) if "%" in s else 0.0


def _bytes(s):
    s = (s or "").strip()
    for suf, mult in (("GiB", 2**30), ("MiB", 2**20), ("KiB", 2**10), ("B", 1)):
        if s.endswith(suf):
            return float(s[: -len(suf)]) * mult
    return 0.0


def stats_now():
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return {}
    res = {}
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) >= 3:
            res[p[0]] = {"cpu": _pct(p[1]), "mem": _bytes(p[2].split()[0])}
    return res


class Sampler:
    def __init__(self):
        self.samples, self._stop = [], False

    def _loop(self):
        while not self._stop:
            self.samples.append((time.time(), stats_now()))
            time.sleep(STAT_INTERVAL)

    def __enter__(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop = True


def phase_cost(samples, containers=None):
    out = {}
    for name in containers or CONTAINERS:
        cpus = [st[name]["cpu"] for _, st in samples
                if name in st and st[name].get("cpu") is not None]
        mems = [st[name]["mem"] for _, st in samples
                if name in st and st[name].get("mem") is not None]
        out[name] = {
            "cpu_pct": statistics.mean(cpus) if cpus else None,
            "mem_gb": (statistics.mean(mems) / BYTES_GB) if mems else None,
            "cpu_peak_pct": max(cpus) if cpus else None,
        }
    return out


def phase_json(phase):
    return {LABEL[n]: row for n, row in phase.items()}


def _fmt(v, unit="%"):
    return "n/a" if v is None else f"{v:.1f}{unit}"


def _delta(b, a):
    """mem_a - mem_gb o None."""
    if b is None or a is None:
        return None
    return a - b


# --- load continuo + latencias -----------------------------------------------

def start_load(engine="both"):
    subprocess.run(
        ["docker", "exec", "-d", "ql_sysbench", "bash",
         "/scripts/continuous_load.sh", engine, "0"],
        check=True, capture_output=True,
    )
    time.sleep(1)
    ok = subprocess.run(
        ["docker", "exec", "ql_sysbench", "bash", "-c",
         f"test -f {LOAD_PID} && kill -0 $(cat {LOAD_PID})"],
        capture_output=True,
    )
    if ok.returncode != 0:
        raise RuntimeError("continuous_load.sh no arranco en ql_sysbench")
    print(f"  load continuo OK (engine={engine})")


def stop_load():
    subprocess.run(
        ["docker", "exec", "ql_sysbench", "bash", "-c",
         f"kill $(cat {LOAD_PID}) 2>/dev/null; rm -f {LOAD_PID}"],
        capture_output=True,
    )
    time.sleep(1)


def clear_load_metrics():
    r = subprocess.run(
        ["docker", "exec", "ql_sysbench", "bash", "-c",
         f"echo 'ts_ms,engine,duration_ms,status' > {LOAD_METRICS}"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError("no se pudo limpiar el CSV de latencias del load")


def read_load_metrics(engine):
    out = subprocess.run(
        ["docker", "exec", "ql_sysbench", "cat", LOAD_METRICS],
        capture_output=True, text=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        p = line.strip().split(",")
        if len(p) < 4 or p[1] != engine:
            continue
        try:
            rows.append({"ts_ms": int(p[0]), "duration_ms": float(p[2]),
                         "status": int(p[3])})
        except ValueError:
            continue
    return rows


def run_pipeline(engine):
    from main import run_engine
    from main_sandbox import ENGINES
    t0 = time.perf_counter()
    run_engine(engine, dict(ENGINES)[engine])
    return time.perf_counter() - t0


def measure_window(seconds):
    with Sampler() as s:
        time.sleep(seconds)
    return s.samples


def measure_with_pipeline(seconds, extract_every_s, engine):
    """Ventana con pipeline. Cadencia>=ventana => 1 disparo a la mitad."""
    t0 = time.perf_counter()
    deadline, mid = t0 + seconds, t0 + seconds / 2.0
    cadence = extract_every_s if extract_every_s and extract_every_s > 0 else None
    single = cadence is None or cadence >= seconds
    last, n = t0, 0
    with Sampler() as s:
        while True:
            now = time.perf_counter()
            if now >= deadline:
                if n == 0:
                    run_pipeline(engine)
                    n = 1
                    print(f"      extraccion #{n} @ t={now - t0:.1f}s (fallback fin)")
                break
            if single:
                if n == 0 and now >= mid:
                    run_pipeline(engine)
                    n, last = 1, time.perf_counter()
                    print(f"      extraccion #{n} @ t={now - t0:.1f}s (mitad)")
            elif now - last >= cadence:
                run_pipeline(engine)
                n += 1
                last = time.perf_counter()
                print(f"      extraccion #{n} @ t={now - t0:.1f}s")
            time.sleep(0.05)
    return s.samples, n


# --- monitor de servidor (fase B) -------------------------------------------

def pg_snapshot(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) FILTER (WHERE r.rolname = :role)::int, "
            "coalesce(sum(s.total_exec_time) FILTER (WHERE r.rolname = :role), 0), "
            "count(*)::int, coalesce(sum(s.total_exec_time), 0) "
            "FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid"
        ), {"role": MONITOR_PG_ROLE}).one()


def mysql_snapshot(engine):
    likes, params = [], {}
    for i, prefix in enumerate(PIPELINE_FINGERPRINT):
        likes.append(f"lower(digest_text) LIKE :p{i}")
        params[f"p{i}"] = prefix + "%"
    w = " OR ".join(likes)
    with engine.connect() as c:
        return c.execute(text(
            "SELECT (SELECT count(*) FROM performance_schema"
            ".events_statements_summary_by_digest WHERE " + w + "), "
            "(SELECT coalesce(sum(sum_timer_wait), 0) FROM performance_schema"
            ".events_statements_summary_by_digest WHERE " + w + "), "
            "count(*), coalesce(sum(sum_timer_wait), 0) "
            "FROM performance_schema.events_statements_summary_by_digest"
        ), params).one()


# --- utilidades ---------------------------------------------------------------

def preflight():
    errors = []
    for name, factory in (("postgres", get_connection_postgres),
                          ("mysql", get_connection_mysql),
                          ("pgmq", get_connection_querylens_db)):
        try:
            with factory().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
    if subprocess.run(
        ["docker", "exec", "ql_sysbench", "test", "-f", "/scripts/continuous_load.sh"],
        capture_output=True,
    ).returncode != 0:
        errors.append("falta /scripts/continuous_load.sh")
    if errors:
        print("Sandbox no disponible:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("docker compose -f ql_sandbox/docker-compose.yml up -d && docker compose up -d",
              file=sys.stderr)
        return False
    return True


def write_json(payload):
    LOGS.mkdir(exist_ok=True)
    path = LOGS / f"overhead_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    return path


def _warn_low_n(label, stats):
    if stats["n"] and stats["n"] < MIN_SAMPLES_WARN:
        print(f"    [aviso] {label}: solo {stats['n']} queries OK "
              f"(<{MIN_SAMPLES_WARN}); p95 poco confiable")


def _round_line(label, lat, cpu_pct, extra=""):
    print(f"    {label} -> p95={_fmt(lat['p95_ms'], 'ms')} "
          f"qps={_fmt(lat['qps'], '')} n={lat['n']} "
          f"fails={lat['failures']} cpu={_fmt(cpu_pct)}{extra}")


# --- medicion ----------------------------------------------------------------

def run(measure_s=DEFAULT_MEASURE_S, rounds=DEFAULT_ROUNDS,
        extract_every_s=DEFAULT_EXTRACT_EVERY_S):
    print("=" * 70)
    print("SOBRECOSTO — carga continua (latencia p95 de la carga observada)")
    print(f"ventana={measure_s:.0f}s rondas={rounds} "
          f"pipeline_cada={extract_every_s:.0f}s warmup={WARMUP_S:.0f}s")
    print(f"umbral: sobrecosto p95 < {UMBRAL_SOBRECOSTO_PCT}%")
    print("Sin datos => SIN_DATO (exit 1), no se asume CUMPLE.")
    print("=" * 70)
    if not preflight():
        return 1

    pg, my = get_connection_postgres(), get_connection_mysql()

    print(f"\n[A] ocioso {WARMUP_S:.0f}s")
    idle = phase_cost(measure_window(WARMUP_S))
    for n, row in idle.items():
        print(f"  {LABEL[n]:10} cpu={_fmt(row['cpu_pct'])} mem={_fmt(row['mem_gb'], 'GB')}")

    print("\n[B] 1 extraccion POR MOTOR (sin load, costo absoluto)")
    b_pg, b_my, abs_cost = pg_snapshot(pg), mysql_snapshot(my), {}
    for engine in ("postgres", "mysql"):
        focus = FOCUS[engine]
        with Sampler() as s_pipe:
            t_pipe = run_pipeline(engine)
        a_pg, a_my = pg_snapshot(pg), mysql_snapshot(my)
        cpu_abs = phase_cost(s_pipe.samples, containers=[focus])
        mon = {
            "wall_s": t_pipe,
            "postgres": {
                "monitor_statements": max(0, a_pg[0] - b_pg[0]),
                "monitor_server_s": max(0.0, float(a_pg[1] - b_pg[1])) / 1000.0,
            },
            "mysql": {
                "monitor_statements": max(0, a_my[0] - b_my[0]),
                "monitor_server_s": max(0.0, float(a_my[1] - b_my[1])) / 1e12,
                "digest_reset_detected": b_my[0] > a_my[0] or float(a_my[1] - b_my[1]) < 0,
            },
            "cpu": phase_json(cpu_abs),
        }
        abs_cost[engine] = mon
        print(f"  {engine:10} pipeline={t_pipe:.1f}s "
              f"pg_monitor={mon['postgres']['monitor_server_s']:.3f}s "
              f"mysql_monitor={mon['mysql']['monitor_server_s']:.3f}s "
              f"cpu={_fmt(cpu_abs[focus]['cpu_pct'])}")
        b_pg, b_my = a_pg, a_my

    engines = {}
    try:
        for engine in ("postgres", "mysql"):
            focus = FOCUS[engine]
            print(f"\n[C] {engine} — load solo de {engine}, "
                  f"{rounds} rondas, pipeline solo de {engine}")
            start_load(engine)
            print(f"  warmup load {WARMUP_S:.0f}s")
            time.sleep(WARMUP_S)

            rows = []
            for r in range(1, rounds + 1):
                print(f"  ronda {r}/{rounds}")
                clear_load_metrics()
                cpu_load = phase_cost(measure_window(measure_s))
                lat_load = latency_stats(read_load_metrics(engine), measure_s)
                _warn_low_n("carga sola", lat_load)
                _round_line(f"carga sola {measure_s:.0f}s", lat_load,
                            cpu_load[focus]["cpu_pct"])

                clear_load_metrics()
                con_s, n_ext = measure_with_pipeline(measure_s, extract_every_s, engine)
                cpu_con = phase_cost(con_s)
                lat_con = latency_stats(read_load_metrics(engine), measure_s)
                _warn_low_n("carga+pipeline", lat_con)

                p95_over = relative_overhead_pct(lat_load["p95_ms"], lat_con["p95_ms"])
                qps_over = qps_overhead_pct(lat_load["qps"], lat_con["qps"])
                cpu_over = cpu_overhead_pct(cpu_load[focus]["cpu_pct"],
                                            cpu_con[focus]["cpu_pct"])
                mem_o = _delta(cpu_load[focus]["mem_gb"], cpu_con[focus]["mem_gb"])
                _round_line("carga+pipeline", lat_con, cpu_con[focus]["cpu_pct"],
                            f" extracciones={n_ext}")
                print(f"    sobrecosto p95={_fmt(p95_over, '%')} "
                      f"qps={_fmt(qps_over, '%')} "
                      f"cpu={_fmt(cpu_over, ' pts%')} mem={_fmt(mem_o, 'GB')}")

                rows.append({
                    "round": r,
                    "latency_load": lat_load,
                    "latency_con": lat_con,
                    "p95_overhead_pct": p95_over,
                    "qps_overhead_pct": qps_over,
                    "cpu_load": phase_json(cpu_load),
                    "cpu_con": phase_json(cpu_con),
                    "cpu_overhead_pct": cpu_over,
                    "mem_overhead_gb": mem_o,
                    "extractions": n_ext,
                })

            ov = [r["p95_overhead_pct"] for r in rows]
            p95_mean = mean_or_none(ov)
            p95_sd, p95_min, p95_max = stdev_or_none(ov), min_or_none(ov), max_or_none(ov)
            n_load_min = min_or_none([r["latency_load"]["n"] for r in rows])
            n_con_min = min_or_none([r["latency_con"]["n"] for r in rows])
            p95_base = mean_or_none([r["latency_load"]["p95_ms"] for r in rows])
            p95_con = mean_or_none([r["latency_con"]["p95_ms"] for r in rows])
            qps_base = mean_or_none([r["latency_load"]["qps"] for r in rows])
            qps_con = mean_or_none([r["latency_con"]["qps"] for r in rows])
            qps_mean = mean_or_none([r["qps_overhead_pct"] for r in rows])
            cpu_b = mean_or_none([r["cpu_load"][engine]["cpu_pct"] for r in rows])
            cpu_a = mean_or_none([r["cpu_con"][engine]["cpu_pct"] for r in rows])
            mem_b = mean_or_none([r["cpu_load"][engine]["mem_gb"] for r in rows])
            mem_a = mean_or_none([r["cpu_con"][engine]["mem_gb"] for r in rows])
            cpu_over = cpu_overhead_pct(cpu_b, cpu_a)
            mem_d = _delta(mem_b, mem_a)
            v = verdict(p95_mean)

            sd_s = f" ± {_fmt(p95_sd, '%')}" if p95_sd is not None else ""
            rng_s = (f" (min={_fmt(p95_min, '%')} max={_fmt(p95_max, '%')})"
                     if p95_min is not None else "")
            print(f"  media: p95 {_fmt(p95_base, 'ms')} -> {_fmt(p95_con, 'ms')} "
                  f"sobrecosto={_fmt(p95_mean, '%')}{sd_s}{rng_s} | "
                  f"qps {_fmt(qps_base, '')} -> {_fmt(qps_con, '')} "
                  f"sobrecosto={_fmt(qps_mean, '%')} | "
                  f"cpu={_fmt(cpu_over, ' pts%')} -> {v}")
            if n_load_min is not None and n_load_min < MIN_SAMPLES_WARN:
                print(f"    [aviso] n_min ventana={n_load_min} "
                      f"(<{MIN_SAMPLES_WARN}); p95 poco confiable")
            if p95_sd is not None and abs(p95_sd) >= P95_SD_WARN_PTS:
                print(f"    [aviso] desvío entre rondas={_fmt(p95_sd, '%')}; "
                      f"replicar con más rondas/ventanas más largas antes de concluir")

            engines[engine] = {
                "metric": "latency_p95_pct",
                "umbral_pct": UMBRAL_SOBRECOSTO_PCT,
                "rounds": rounds,
                "measure_s": measure_s,
                "extract_every_s": extract_every_s,
                "p95_load_ms": p95_base,
                "p95_con_ms": p95_con,
                "p95_overhead_pct": p95_mean,
                "p95_overhead_pct_stdev": p95_sd,
                "p95_overhead_pct_min": p95_min,
                "p95_overhead_pct_max": p95_max,
                "n_load_min": n_load_min,
                "n_con_min": n_con_min,
                "qps_load": qps_base,
                "qps_con": qps_con,
                "qps_overhead_pct": qps_mean,
                "cpu_load_pct": cpu_b,
                "cpu_con_pct": cpu_a,
                "cpu_overhead_pct": cpu_over,
                "mem_load_gb": mem_b,
                "mem_con_gb": mem_a,
                "mem_overhead_gb": mem_d,
                "verdict": v,
                "round_details": rows,
            }
            stop_load()
            print("  load detenido")
    finally:
        stop_load()

    final = overall_verdict(engines)
    print("\n" + "=" * 70)
    print("RESUMEN (veredicto = sobrecosto p95 latencia de la carga)")
    print("-" * 70)
    for name, d in engines.items():
        sd, nmin = d.get("p95_overhead_pct_stdev"), d.get("n_load_min")
        sd_s = f" ± {_fmt(sd, '%')}" if sd is not None else ""
        n_s = f" n_min={nmin}" if nmin is not None else ""
        print(f"  {name:10} p95 {_fmt(d['p95_load_ms'], 'ms')} -> "
              f"{_fmt(d['p95_con_ms'], 'ms')} "
              f"sobrecosto={_fmt(d['p95_overhead_pct'], '%')}{sd_s} | "
              f"qps={_fmt(d['qps_overhead_pct'], '%')} | "
              f"cpu={_fmt(d['cpu_overhead_pct'], ' pts%')} "
              f"rondas={d['rounds']}{n_s} -> {d['verdict']}")
    for name, m in abs_cost.items():
        print(f"  fase B {name}: pipeline={m['wall_s']:.1f}s "
              f"pg_monitor={m['postgres']['monitor_server_s']:.3f}s "
              f"mysql_monitor={m['mysql']['monitor_server_s']:.3f}s")
    print(f"  umbral p95 < {UMBRAL_SOBRECOSTO_PCT}% | GLOBAL: {final}")

    path = write_json({
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "method": "continuous_load_per_engine_latency",
        "measure_s": measure_s,
        "rounds": rounds,
        "extract_every_s": extract_every_s,
        "metric": "latency_p95_pct",
        "umbral_pct": UMBRAL_SOBRECOSTO_PCT,
        "verdict": final,
        "idle": phase_json(idle),
        "extraction": abs_cost,
        "engines": engines,
    })
    print(f"JSON: {path}")
    return 0 if final == "CUMPLE" else (2 if final == "NO CUMPLE" else 1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "quick"]
    quick = "quick" in sys.argv[1:]
    measure_s = float(args[0]) if args else DEFAULT_MEASURE_S
    rounds = int(args[1]) if len(args) > 1 else DEFAULT_ROUNDS
    cadence = float(args[2]) if len(args) > 2 else DEFAULT_EXTRACT_EVERY_S
    if quick:
        if measure_s == DEFAULT_MEASURE_S:
            measure_s = 10.0
        if rounds == DEFAULT_ROUNDS:
            rounds = 2
    sys.exit(run(measure_s=measure_s, rounds=rounds, extract_every_s=cadence))
