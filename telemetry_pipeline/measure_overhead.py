"""Mide el sobrecosto del pipeline sobre las DBs del sandbox.

Fases por motor (postgres + mysql):
  A. linea base ociosa    -> CPU/RAM de los contenedores sin actividad
  B. bateria sola         -> duracion + CPU/RAM del DB bajo el workload real
  C. pipeline post-carga  -> declaraciones que el piipeline genera en la DB
                            (pg_stat_statements / digest por ventana) + CPU/RAM
  D. pipeline durante trafico -> impacto en latencia del workload (bateria
                            con y sin pipeline concurrente)

USO:
  python measure_overhead.py [REPS]   # bateria con REPS repeticiones (default 30)
  python measure_overhead.py 10 quick # perfil rapido
"""

import os
import statistics
import subprocess
import sys
import threading
import time

from sqlalchemy import create_engine, text

PIPELINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE)
from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)

PY = os.path.join(PIPELINE, "venv", "Scripts", "python.exe")
MAIN = os.path.join(PIPELINE, "main.py")
CONTAINERS = ["ql_postgres", "ql_mysql"]
STAT_INTERVAL = 1.5

QUICK = len(sys.argv) > 2 and sys.argv[2] == "quick"
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
if QUICK and REPS == 30:
    REPS = 10


def _pct(s):
    return float(s.rstrip("%")) if "%" in s else 0.0


def _bytes(s):
    s = (s or "").strip()
    if s.endswith("GiB"):
        return float(s[:-3]) * 2**30
    if s.endswith("MiB"):
        return float(s[:-3]) * 2**20
    if s.endswith("KiB"):
        return float(s[:-3]) * 2**10
    if s.endswith("B"):
        return float(s[:-1])
    return 0.0


def _stats_now():
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return {}
    res = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            res[parts[0]] = {"cpu": _pct(parts[1]), "mem": _bytes(parts[2].split()[0])}
    return res


class Sampler:
    def __init__(self):
        self.samples = []
        self._stop = False

    def _loop(self):
        while not self._stop:
            self.samples.append((time.time(), _stats_now()))
            time.sleep(STAT_INTERVAL)

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        self._t.join(timeout=8)


def _agg_cpu(samples, names, idle_cpu=None):
    cpus = []
    nets = []
    mems = []
    for _, s in samples:
        for n in names:
            st = s.get(n)
            if not st:
                continue
            c = st.get("cpu")
            m = st.get("mem")
            if c is not None:
                cpus.append(c)
                base = (idle_cpu or {}).get(n, 0.0)
                nets.append(max(0.0, c - base))
            if m is not None:
                mems.append(m)
    return {
        "cpu_mean": statistics.mean(cpus) if cpus else None,
        "cpu_mean_net": statistics.mean(nets) if nets else None,
        "cpu_max": max(cpus) if cpus else None,
        "mem_mean": statistics.mean(mems) if mems else None,
        "mem_max": max(mems) if mems else None,
    }


def _battery(reps):
    t0 = time.time()
    subprocess.run(["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", str(reps)], check=True)
    return time.time() - t0


def _pipeline():
    t0 = time.time()
    subprocess.run([PY, MAIN], check=True)
    return time.time() - t0


def _pg_snapshot(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT count(*)::int AS n, coalesce(sum(total_exec_time),0) AS tt_ms "
            "FROM pg_stat_statements"
        )).one()


def _mysql_snapshot(engine):
    with engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) AS n, coalesce(sum(sum_timer_wait),0) AS wait_ps "
            "FROM performance_schema.events_statements_summary_by_digest"
        )).one()


def _print(k, v, unit=""):
    print(f"  {k:<34} {v}{unit}")


def run(quick=False, reps=REPS):
    print("=" * 72)
    print(f"MEDICION DE SOBRECOSTO (bateria con {reps} reps por query)")
    print("=" * 72)

    pg = get_connection_postgres()
    my = get_connection_mysql()

    # A. linea base ociosa -------------------------------------------------
    print("\n[A] Base ociosa (DBs sin actividad) - 6s")
    with Sampler() as s:
        time.sleep(6)
    idle_cpu = {}
    for n in CONTAINERS:
        vals = [x[n]["cpu"] for _, x in s.samples if n in x]
        idle_cpu[n] = statistics.mean(vals) if vals else 0.0
    idle_agg = _agg_cpu(s.samples, CONTAINERS)
    _print("cpu_mean", f"{idle_cpu.get('ql_postgres', 0):.1f}% / {idle_cpu.get('ql_mysql', 0):.1f}%", " (pg / mysql)")
    _print("mem_mean", f"{idle_agg['mem_mean'] / 1e9:.2f} GB", "")

    # B. bateria sola ------------------------------------------------------
    print(f"\n[B] Bateria sin pipeline ({reps} reps)")
    with Sampler() as s:
        t_battery_only = _battery(reps)
    agg_b = _agg_cpu(s.samples, CONTAINERS, idle_cpu)
    _print("duracion_bateria", f"{t_battery_only:.1f}", " s")
    _print("cpu(db) neta", f"{agg_b['cpu_mean_net']:.1f}%", " (promedio sobre el intervalo, pg+mysql)")
    _print("cpu(db) pico", f"{agg_b['cpu_max']:.1f}%", "")
    _print("mem(db)", f"{agg_b['mem_mean'] / 1e9:.2f}", " GB")

    # C. pipeline post-carga ------------------------------------------------
    print(f"\n[C] Pipeline tras la bateria ({reps} reps de carga, luego pipeline)")
    _battery(reps)
    b_pg = _pg_snapshot(pg)
    b_my = _mysql_snapshot(my)
    with Sampler() as s:
        t_pipe = _pipeline()
    a_pg = _pg_snapshot(pg)
    a_my = _mysql_snapshot(my)
    agg_c = _agg_cpu(s.samples, CONTAINERS, idle_cpu)
    stm_pg = a_pg.n - b_pg.n
    tt_pg = (a_pg.tt_ms - b_pg.tt_ms) / 1000.0
    stm_my = max(0, a_my.n - b_my.n)
    tt_my_raw = float(a_my.wait_ps - b_my.wait_ps)
    tt_my = max(0.0, tt_my_raw) / 1e12 if tt_my_raw > 0 else None
    reset_note = " (contador de digests reiniciado; delta no confiable)" \
        if (b_my.n > a_my.n or tt_my_raw < 0) else ""
    with get_connection_querylens_db().connect() as c:
        explains = c.execute(text(
            "SELECT count(*) FROM pgmq.q_analyze_job "
            "WHERE enqueued_at > now() - interval '5 minutes'"
        )).scalar()
    _print("duracion_pipeline", f"{t_pipe:.1f}", " s")
    _print("cpu(db) neta", f"{agg_c['cpu_mean_net']:.1f}%", " (durante el pipeline)")
    _print("cpu(db) pico", f"{agg_c['cpu_max']:.1f}%", "")
    _print("mem(db) durante", f"{agg_c['mem_mean'] / 1e9:.2f}", " GB")
    _print("pg: statements pipeline", f"{stm_pg}", "")
    _print("pg: tiempo_db acumulado", f"{tt_pg:.3f}", " s")
    _print("mysql: statements pipeline", f"{stm_my}", "")
    if tt_my is None:
        _print("mysql: tiempo_db acumulado", "n/a", reset_note)
    else:
        _print("mysql: tiempo_db acumulado", f"{tt_my:.3f}", f" s{reset_note}")

    # D. impacto en latencia del workload ----------------------------------
    print(f"\n[D] Pipeline concurrente con trafico (impacto en latencia)")
    with Sampler() as s:
        t0 = time.time()
        proc = subprocess.Popen(["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", str(reps)])
        time.sleep(max(0.5, t_battery_only * 0.1))
        t_pipe_conc = _pipeline()
        proc.wait()
        t_batt_conc = time.time() - t0
    agg_d = _agg_cpu(s.samples, CONTAINERS, idle_cpu)
    impact = (t_batt_conc - t_battery_only) / t_battery_only * 100
    _print("duracion_bateria con pipeline", f"{t_batt_conc:.1f}", " s")
    _print("overhead latencia workload", f"{impact:+.1f}%", "")
    _print("cpu(db) neta", f"{agg_d['cpu_mean_net']:.1f}%", "")
    _print("cpu(db) pico", f"{agg_d['cpu_max']:.1f}%", "")

    print("\n" + "=" * 72)
    print("RESUMEN")
    print("-" * 72)
    if tt_my is None:
        print(f"  Pipeline: {t_pipe:.1f}s de ejecucion, {stm_pg} statements postgres + "
              f"{stm_my} mysql en la DB, {tt_pg:.3f}s de tiempo de servidor")
    else:
        print(f"  Pipeline: {t_pipe:.1f}s de ejecucion, {stm_pg} statements postgres + "
              f"{stm_my} mysql en la DB, {tt_pg:.3f}s+{tt_my:.3f}s de tiempo de servidor")
    print(f"  Impacto en latencia de trafico concurrente: {impact:+.1f}%")
    print(f"  CPU pico DB bajo pipeline: {agg_c['cpu_max']:.1f}% (basal {idle_cpu.get('ql_postgres', 0):.1f}% / "
          f"{idle_cpu.get('ql_mysql', 0):.1f}%)")
    print(f"  Memoria DB: ocio {idle_agg['mem_mean'] / 1e9:.2f} GB -> pipeline "
          f"{agg_c['mem_mean'] / 1e9:.2f} GB")
    return {
        "reps": reps,
        "pipeline_s": t_pipe,
        "db_statements": {"postgres": stm_pg, "mysql": stm_my},
        "db_server_time_s": {"postgres": tt_pg, "mysql": tt_my},
        "latency_impact_pct": impact,
        "cpu_pipeline_pct": agg_c["cpu_mean_net"],
        "cpu_pipeline_peak_pct": agg_c["cpu_max"],
        "mem_idle_gb": idle_agg["mem_mean"] / 1e9,
        "mem_pipeline_gb": agg_c["mem_mean"] / 1e9,
    }


if __name__ == "__main__":
    run(quick=QUICK, reps=REPS)