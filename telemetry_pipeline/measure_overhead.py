"""Mide el sobrecosto del pipeline sobre las DBs del sandbox.

Cumple PrimerInforme.md: sobrecosto inferior al 5 % sobre la metrica de
rendimiento de la carga observada. La metrica es la duracion de la bateria
(workload controlado) con y sin el pipeline corriendo en paralelo.

Fases:
  A. linea base ociosa      -> CPU/RAM de los contenedores sin actividad
  B. bateria de carga       -> stats pobladas + referencia de duracion
  C. una extraccion         -> wall del pipeline + tiempo de servidor del
                               monitor (delta filtrado por rol/fingerprint)
  D. impacto por motor      -> bateria solo vs con pipeline concurrente
                               (postgres y mysql por separado)

USO (sandbox arriba):
  python measure_overhead.py [REPS] [quick]
  python measure_overhead.py 10 quick

Exit codes:
  0 = CUMPLE (< 5 % en ambos motores)
  1 = error o SIN_DATO (sandbox caido, stats no confiables)
  2 = NO CUMPLE (>= 5 % en al menos un motor)
"""

import json
import os
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
CONTAINERS = ["ql_postgres", "ql_mysql"]
STAT_INTERVAL = 1.5
LOGS_DIR = PIPELINE / "logs"
MONITOR_PG_ROLE = "querylens_monitor"


def impact_pct(t_sola, t_con):
    """Impacto relativo de la duracion de la carga: (con - sola) / sola."""
    if t_sola is None or t_con is None or t_sola <= 0:
        return None
    return (t_con - t_sola) / t_sola * 100.0


def verdict(impact, umbral=UMBRAL_SOBRECOSTO_PCT):
    """PrimerInforme: sobrecosto *inferior* al 5 %."""
    if impact is None:
        return "SIN_DATO"
    return "CUMPLE" if impact < umbral else "NO CUMPLE"


def overall_verdict(per_engine):
    states = [v.get("verdict") for v in per_engine.values() if v]
    if not states:
        return "SIN_DATO"
    if "NO CUMPLE" in states:
        return "NO CUMPLE"
    if "SIN_DATO" in states:
        return "SIN_DATO"
    return "CUMPLE"


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
    cpus, nets, mems = [], [], []
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


def _battery(reps, engine="both"):
    t0 = time.perf_counter()
    subprocess.run(
        ["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", str(reps), "0", engine],
        check=True,
    )
    return time.perf_counter() - t0


def _pipeline():
    """Una extraccion completa del agente (collect + validate + enqueue)."""
    from main import main as run_main

    t0 = time.perf_counter()
    run_main()
    return time.perf_counter() - t0


def _pg_snapshot(engine):
    """Delta disponible del monitor vs total en pg_stat_statements.

    El rol monitor aparece en la vista: el collector lo excluye del snapshot
    con userid != session_user, pero para medir su costo propio se filtra
    explicitamente por rol.
    """
    with engine.connect() as c:
        return c.execute(text(
            "SELECT "
            "  count(*) FILTER (WHERE r.rolname = :role)::int AS mon_n, "
            "  coalesce(sum(s.total_exec_time) FILTER (WHERE r.rolname = :role), 0) AS mon_ms, "
            "  count(*)::int AS total_n, "
            "  coalesce(sum(s.total_exec_time), 0) AS total_ms "
            "FROM pg_stat_statements s "
            "JOIN pg_roles r ON r.oid = s.userid"
        ), {"role": MONITOR_PG_ROLE}).one()


def _mysql_snapshot(engine):
    """Delta del monitor (fingerprint del pipeline) vs total en digest."""
    likes = []
    params = {}
    for i, prefix in enumerate(PIPELINE_FINGERPRINT):
        key = f"p{i}"
        likes.append(f"lower(digest_text) LIKE :{key}")
        params[key] = prefix + "%"
    monitor_where = " OR ".join(likes)
    with engine.connect() as c:
        return c.execute(text(
            "SELECT "
            "  (SELECT count(*) FROM performance_schema.events_statements_summary_by_digest "
            f"   WHERE {monitor_where}) AS mon_n, "
            "  (SELECT coalesce(sum(sum_timer_wait), 0) "
            f"   FROM performance_schema.events_statements_summary_by_digest "
            f"   WHERE {monitor_where}) AS mon_wait_ps, "
            "  count(*) AS total_n, "
            "  coalesce(sum(sum_timer_wait), 0) AS total_wait_ps "
            "FROM performance_schema.events_statements_summary_by_digest"
        ), params).one()


def _print(k, v, unit=""):
    print(f"  {k:<36} {v}{unit}")


def _preflight():
    errors = []
    checks = (
        ("postgres", get_connection_postgres),
        ("mysql", get_connection_mysql),
        ("querylens_db (pgmq)", get_connection_querylens_db),
    )
    for name, factory in checks:
        try:
            engine = factory()
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:140]}")

    try:
        probe = subprocess.run(
            ["docker", "exec", "ql_sysbench", "test", "-f", "/scripts/battery.sh"],
            capture_output=True, text=True, timeout=15,
        )
        if probe.returncode != 0:
            errors.append("ql_sysbench: no responde o falta /scripts/battery.sh")
    except Exception as exc:
        errors.append(f"ql_sysbench: {type(exc).__name__}: {str(exc)[:140]}")

    if errors:
        print("Sandbox no disponible:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nLevanta el banco de pruebas:\n"
            "  docker compose -f ql_sandbox/docker-compose.yml up -d\n"
            "  docker compose up -d\n"
            "y exporta las credenciales del .env del repo si hace falta.",
            file=sys.stderr,
        )
        return False
    return True


def _write_json(payload):
    LOGS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"overhead_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def run(reps=30):
    print("=" * 72)
    print(f"MEDICION DE SOBRECOSTO (bateria con {reps} reps por query)")
    print(f"Umbral PrimerInforme: sobrecosto < {UMBRAL_SOBRECOSTO_PCT}%")
    print("=" * 72)

    if not _preflight():
        return 1

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
    _print("cpu_mean pg/mysql", f"{idle_cpu.get('ql_postgres', 0):.1f}% / {idle_cpu.get('ql_mysql', 0):.1f}%")
    _print("mem_mean", f"{(idle_agg['mem_mean'] or 0) / 1e9:.2f} GB")

    # B. bateria de carga (stats + referencia) -----------------------------
    print(f"\n[B] Bateria de carga ({reps} reps, ambos motores)")
    with Sampler() as s:
        t_batt_ref = _battery(reps, "both")
    agg_b = _agg_cpu(s.samples, CONTAINERS, idle_cpu)
    _print("duracion_bateria", f"{t_batt_ref:.1f}", " s")
    _print("cpu(db) neta", f"{(agg_b['cpu_mean_net'] or 0):.1f}%")

    # C. una extraccion: costo absoluto del pipeline -----------------------
    print("\n[C] Una extraccion del pipeline tras la carga")
    b_pg = _pg_snapshot(pg)
    b_my = _mysql_snapshot(my)
    with Sampler() as s:
        t_pipe = _pipeline()
    a_pg = _pg_snapshot(pg)
    a_my = _mysql_snapshot(my)
    agg_c = _agg_cpu(s.samples, CONTAINERS, idle_cpu)

    mon_n_pg = max(0, a_pg.mon_n - b_pg.mon_n)
    mon_ms_pg = max(0.0, float(a_pg.mon_ms - b_pg.mon_ms))
    tot_ms_pg = max(0.0, float(a_pg.total_ms - b_pg.total_ms))
    mon_n_my = max(0, a_my.mon_n - b_my.mon_n)
    mon_wait_my = float(a_my.mon_wait_ps - b_my.mon_wait_ps)
    tot_wait_my = float(a_my.total_wait_ps - b_my.total_wait_ps)
    reset_my = b_my.mon_n > a_my.mon_n or mon_wait_my < 0
    mon_s_my = max(0.0, mon_wait_my) / 1e12 if not reset_my else None
    tot_s_my = max(0.0, tot_wait_my) / 1e12 if tot_wait_my >= 0 else None

    try:
        with get_connection_querylens_db().connect() as c:
            enqueued = c.execute(text(
                "SELECT count(*) FROM pgmq.q_analyze_job "
                "WHERE enqueued_at > now() - interval '5 minutes'"
            )).scalar()
    except Exception:
        enqueued = None

    _print("duracion_pipeline", f"{t_pipe:.1f}", " s")
    _print("cpu(db) neta", f"{(agg_c['cpu_mean_net'] or 0):.1f}%")
    _print("pg: statements del monitor", f"{mon_n_pg}")
    _print("pg: tiempo_db monitor", f"{mon_ms_pg / 1000.0:.3f}", " s")
    _print("pg: tiempo_db ventana total", f"{tot_ms_pg / 1000.0:.3f}", " s")
    if mon_s_my is None:
        _print("mysql: tiempo_db monitor", "n/a", " (digest reseteado; delta no confiable)")
    else:
        _print("mysql: statements del monitor", f"{mon_n_my}")
        _print("mysql: tiempo_db monitor", f"{mon_s_my:.3f}", " s")
    if tot_s_my is not None:
        _print("mysql: tiempo_db ventana total", f"{tot_s_my:.3f}", " s")
    if enqueued is not None:
        _print("mensajes encolados (5 min)", f"{enqueued}")

    extraction = {
        "wall_s": t_pipe,
        "postgres": {
            "monitor_statements": mon_n_pg,
            "monitor_server_s": mon_ms_pg / 1000.0,
            "window_total_server_s": tot_ms_pg / 1000.0,
        },
        "mysql": {
            "monitor_statements": mon_n_my,
            "monitor_server_s": mon_s_my,
            "window_total_server_s": tot_s_my,
            "digest_reset_detected": reset_my,
        },
        "cpu_pipeline_pct": agg_c["cpu_mean_net"],
        "cpu_pipeline_peak_pct": agg_c["cpu_max"],
        "enqueued_last_5min": enqueued,
    }

    # D. impacto por motor (bateria solo vs con pipeline) ------------------
    engines_impact = {}
    for engine_name in ("postgres", "mysql"):
        print(f"\n[D] Impacto en {engine_name}: bateria solo vs con pipeline")
        t_sola = _battery(reps, engine_name)
        with Sampler() as s:
            t0 = time.perf_counter()
            proc = subprocess.Popen(
                ["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh",
                 str(reps), "0", engine_name]
            )
            time.sleep(max(0.5, t_sola * 0.1))
            t_pipe_conc = _pipeline()
            proc.wait()
            t_con = time.perf_counter() - t0
        agg_d = _agg_cpu(s.samples, CONTAINERS, idle_cpu)
        impact = impact_pct(t_sola, t_con)
        v = verdict(impact)
        _print("duracion_sola", f"{t_sola:.1f}", " s")
        _print("duracion_con_pipeline", f"{t_con:.1f}", " s")
        _print("overhead_latencia", f"{impact:+.1f}%" if impact is not None else "n/a")
        _print("veredicto_motor", f"{v} (umbral < {UMBRAL_SOBRECOSTO_PCT}%)")
        _print("cpu(db) neta", f"{(agg_d['cpu_mean_net'] or 0):.1f}%")
        engines_impact[engine_name] = {
            "t_sola_s": t_sola,
            "t_con_s": t_con,
            "pipeline_concurrent_wall_s": t_pipe_conc,
            "impact_pct": impact,
            "verdict": v,
            "cpu_mean_net_pct": agg_d["cpu_mean_net"],
            "cpu_peak_pct": agg_d["cpu_max"],
        }

    final = overall_verdict(engines_impact)

    print("\n" + "=" * 72)
    print("RESUMEN POR MOTOR")
    print("-" * 72)
    for name, data in engines_impact.items():
        imp = data["impact_pct"]
        imp_s = f"{imp:+.1f}%" if imp is not None else "n/a"
        print(f"  {name:<10} impacto={imp_s:>8}  -> {data['verdict']}")
    print(f"\n  Extraccion: {t_pipe:.1f}s de wall; "
          f"pg monitor={mon_ms_pg / 1000.0:.3f}s; "
          f"mysql monitor={mon_s_my if mon_s_my is None else f'{mon_s_my:.3f}s'}")
    print(f"  Umbral: < {UMBRAL_SOBRECOSTO_PCT}% (PrimerInforme)")
    print(f"  VEREDICTO GLOBAL: {final}")

    payload = {
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "reps": reps,
        "umbral_pct": UMBRAL_SOBRECOSTO_PCT,
        "verdict": final,
        "idle": {
            "cpu_pg_pct": idle_cpu.get("ql_postgres"),
            "cpu_mysql_pct": idle_cpu.get("ql_mysql"),
            "mem_mean_gb": (idle_agg["mem_mean"] or 0) / 1e9,
        },
        "battery_ref_s": t_batt_ref,
        "extraction": extraction,
        "engines": engines_impact,
    }
    path = _write_json(payload)
    print(f"\nJSON: {path}")

    if final == "CUMPLE":
        return 0
    if final == "NO CUMPLE":
        return 2
    return 1


if __name__ == "__main__":
    quick = len(sys.argv) > 2 and sys.argv[2] == "quick"
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    if quick and reps == 30:
        reps = 10
    sys.exit(run(reps=reps))
