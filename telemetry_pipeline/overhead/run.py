"""Sobrecosto del pipeline v2 (PrimerInforme: < 5 %). Ver OVERHEAD_V2.md.

    python -m overhead.run --profile quick                      # el arnes funciona (no es evidencia)
    python -m overhead.run --profile serious --engine both      # evidencia (~1 h)
    python -m overhead.run --profile quick --positive-control   # el arnes detecta un costo conocido

Por motor, con la carga (overhead.loadgen) corriendo todo el tiempo:
  A/A              pares carga sola vs carga sola: piso de ruido del host
  pipeline         carga sola vs carga + pipeline a la cadencia real -> VEREDICTO
  stress           igual con cadencia de 2 s (opcional, cota superior)
  positive_control igual con una query pesada en lugar del pipeline (opcional)
  instrumentation  instrumentacion apagada vs prendida, sin pipeline (aparte)
Cada par corre sus dos bloques en orden aleatorio (semilla en el JSON).

Exit: 0=CUMPLE, 1=SIN_DATO/error, 2=NO CUMPLE, 3=INCONCLUSO
"""
import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from config.connections import (
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)
from main import _health_db_id
from overhead import server_cost
from overhead import stats as st
from overhead.connect import CONNECT
from overhead.toggles import TOGGLES

PIPELINE = Path(__file__).resolve().parent.parent
LOGS = PIPELINE / "logs"

PROFILES = {
    "quick": {"block_s": 25.0, "aa_pairs": 2, "pipeline_pairs": 3, "instr_pairs": 0,
              "stress_pairs": 2, "burn_pairs": 3},
    "serious": {"block_s": 45.0, "aa_pairs": 3, "pipeline_pairs": 10, "instr_pairs": 6,
                "stress_pairs": 4, "burn_pairs": 4},
}
SETTLE_S = 5.0
WARMUP_S = 60.0
GAP_S = 1.0
STRESS_CADENCE_S = 2.0
# Servicio del compose de la raiz (sin container_name fijo: se busca por la
# etiqueta de compose, en cualquier proyecto).
EXTERNAL_PIPELINE_SERVICE = "pipeline"
MONITOR_FACTORIES = {"postgres": get_connection_postgres, "mysql": get_connection_mysql}
# Los consumers que exige el pipeline (health/preflight.MY_CONSUMERS).
MY_REQUIRED_CONSUMERS = ("global_instrumentation", "thread_instrumentation",
                         "statements_digest", "events_statements_current")


# --- preflight -------------------------------------------------------------------

def _compose_service_running(service):
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.service={service}",
             "--filter", "status=running", "-q"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return False
    return bool(out.strip())


def _check_pg(errors, degraded):
    conn = CONNECT["postgres"]("ADMIN")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sbtest1")
            if not cur.fetchone()[0]:
                errors.append("postgres: sbtest1 vacia")
            cur.execute("SELECT current_setting('shared_preload_libraries')")
            if "auto_explain" not in cur.fetchone()[0]:
                degraded.append("postgres: auto_explain no esta en shared_preload_libraries")
            else:
                cur.execute("SELECT current_setting('auto_explain.log_min_duration')")
                if cur.fetchone()[0] == "-1":
                    degraded.append("postgres: auto_explain.log_min_duration = -1 (apagado)")
            cur.execute("SELECT to_regprocedure('querylens.read_log(text,bigint,bigint)') IS NOT NULL"
                        " AND to_regprocedure('querylens.log_files()') IS NOT NULL")
            if not cur.fetchone()[0]:
                degraded.append("postgres: faltan querylens.log_files()/read_log() (guia 006, paso 4)")
    finally:
        conn.close()


def _check_mysql(errors, degraded):
    conn = CONNECT["mysql"]("ADMIN")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ql_demo.sbtest1")
            if not cur.fetchone()[0]:
                errors.append("mysql: sbtest1 vacia")
            cur.execute(
                "SELECT NAME FROM performance_schema.setup_consumers "
                "WHERE NAME IN %s AND ENABLED <> 'YES'", (MY_REQUIRED_CONSUMERS,)
            )
            off = [r[0] for r in cur.fetchall()]
            if off:
                degraded.append(f"mysql: consumers apagados {off}")
    finally:
        conn.close()


def preflight(engines, allow_degraded, allow_external):
    errors, degraded, warnings = [], [], []
    if _compose_service_running(EXTERNAL_PIPELINE_SERVICE):
        msg = (f"el servicio {EXTERNAL_PIPELINE_SERVICE} del compose esta extrayendo: ensucia los "
               f"bloques de carga sola (docker compose stop {EXTERNAL_PIPELINE_SERVICE})")
        if allow_external:
            warnings.append(msg)
        else:
            errors.append(f"{msg} (--allow-external-pipeline para medir igual)")
    checks = [("pgmq", get_connection_querylens_db)]
    checks += [(f"{e} monitor", MONITOR_FACTORIES[e]) for e in engines]
    for name, factory in checks:
        try:
            engine = factory()
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:150]}")
    for engine in engines:
        try:
            (_check_pg if engine == "postgres" else _check_mysql)(errors, degraded)
        except Exception as e:
            errors.append(f"{engine} load/admin: {type(e).__name__}: {str(e)[:150]}")
    if degraded and not allow_degraded:
        errors += [f"{d} (--allow-degraded para medir igual)" for d in degraded]
        degraded = []
    return errors, degraded + warnings


# --- subprocesos -------------------------------------------------------------------

class PipelineProc:
    """Cliente del protocolo de overhead.pipeline_proc."""

    def __init__(self, engine, cadence, mode, events_path, log_path):
        self.log = open(log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "overhead.pipeline_proc", "--engine", engine,
             "--cadence", str(cadence), "--mode", mode, "--events", str(events_path)],
            cwd=PIPELINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, bufsize=1,
        )
        self._expect("ready")

    def _expect(self, want):
        line = self.proc.stdout.readline().strip()
        if line != want:
            raise RuntimeError(f"pipeline_proc respondio {line!r}, se esperaba {want!r} "
                               f"(ver {self.log.name})")

    def send(self, cmd, want):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
        self._expect(want)

    def close(self):
        try:
            if self.proc.poll() is None:
                self.send("quit", "ack quit")
                self.proc.wait(timeout=60)
        except Exception:
            self.proc.kill()
        self.log.close()


def start_loadgen(engine, workers, out_path, log_path):
    log = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "overhead.loadgen", "--engine", engine,
         "--workers", str(workers), "--out", str(out_path)],
        cwd=PIPELINE, stdout=log, stderr=log,
    )
    return proc, log


def stop_loadgen(proc, log):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
    log.close()


# --- lectura de resultados ------------------------------------------------------------

def read_samples(path):
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split(",")
        if len(p) != 4:
            continue
        try:
            out.append((int(p[0]), int(p[1]), int(p[2]), p[3] == "1"))
        except ValueError:
            continue
    return out


def read_cycles(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def health_rows(engine):
    db_id = _health_db_id(engine, None)
    qe = get_connection_querylens_db()
    try:
        with qe.connect() as c:
            last = c.execute(text(
                "SELECT status, sections_failed, finished_at FROM pipeline_health.last_run "
                "WHERE db_id = :d"), {"d": db_id}).mappings().first()
            issues = c.execute(text(
                "SELECT code, severity, occurrences FROM pipeline_health.issues "
                "WHERE db_id = :d AND resolved_at IS NULL ORDER BY code"), {"d": db_id}).mappings().all()
        return {"db_id": db_id, "last_run": dict(last) if last else None,
                "open_issues": [dict(i) for i in issues]}
    except Exception as e:
        return {"db_id": db_id, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        qe.dispose()


# --- sesion por motor ----------------------------------------------------------------

def plan_arms(engine, cfg, args):
    """[(arm, n_pares, base, treat)]. El orden no importa: stats.interleave
    mezcla los pares de todos los brazos."""
    arms = [("aa", cfg["aa_pairs"], "A1", "A2"),
            ("pipeline", args.pairs or cfg["pipeline_pairs"], "off", "on")]
    if args.stress:
        arms.append(("stress", cfg["stress_pairs"], "off", "on"))
    if args.positive_control:
        arms.append(("positive_control", cfg["burn_pairs"], "off", "on"))
    if cfg["instr_pairs"] and not args.skip_instrumentation:
        arms.append(("instrumentation", cfg["instr_pairs"], "off", "on"))
    return arms


def run_engine_session(engine, cfg, args, run_dir, seed):
    block_s = args.block_s or cfg["block_s"]
    arms = plan_arms(engine, cfg, args)
    samples_path = run_dir / f"{engine}_load.csv"
    cycles_path = run_dir / f"{engine}_cycles.jsonl"
    procs, toggle, admin = {}, None, None
    blocks = []
    rng = random.Random(seed)
    load, load_log = None, None
    try:
        admin = CONNECT[engine]("ADMIN")
        if any(a[0] == "instrumentation" for a in arms):
            toggle = TOGGLES[engine]()
        procs["pipeline"] = PipelineProc(engine, args.cadence, "pipeline", cycles_path,
                                         run_dir / f"{engine}_pipeline.log")
        if args.stress:
            procs["stress"] = PipelineProc(engine, STRESS_CADENCE_S, "pipeline", cycles_path,
                                           run_dir / f"{engine}_stress.log")
        if args.positive_control:
            procs["positive_control"] = PipelineProc(engine, args.cadence, "burn", cycles_path,
                                                     run_dir / f"{engine}_burn.log")

        load, load_log = start_loadgen(engine, args.workers, samples_path,
                                       run_dir / f"{engine}_loadgen.log")
        print(f"  carga: {args.workers} workers; warmup {args.warmup_s:.0f}s")
        time.sleep(args.warmup_s)
        if load.poll() is not None:
            raise RuntimeError(f"loadgen termino (ver {load_log.name})")

        treats = {arm: treat for arm, _n, _base, treat in arms}
        units = st.interleave(arms, rng.randrange(1 << 30))
        print(f"  {len(units)} pares mezclados ({', '.join(f'{a}={n}' for a, n, _, _ in arms)})"
              f" x 2 bloques de {block_s:.0f}s")
        for i, (arm, pair, order) in enumerate(units, 1):
            for side in order:
                blocks.append(_run_block(engine, arm, pair, side, treats[arm], block_s,
                                         procs, toggle, admin))
            print(f"    {i}/{len(units)} {arm} par {pair + 1} {order[0]}/{order[1]}")
    finally:
        if load is not None:
            stop_loadgen(load, load_log)
        for p in procs.values():
            p.close()
        if toggle is not None:
            toggle.close()
        if admin is not None:
            admin.close()

    return analyze_engine(engine, arms, blocks, read_samples(samples_path),
                          read_cycles(cycles_path), seed, args, toggle)


def _run_block(engine, arm, pair, side, treat, block_s, procs, toggle, admin):
    proc = procs.get(arm) if side == treat else None
    if arm == "instrumentation":
        (toggle.on if side == treat else toggle.off)()
    before = server_cost.snapshot(engine, admin)
    snap_t0 = time.monotonic_ns()
    if proc is not None:
        # Primer ciclo al terminar el settle: entra entero en la ventana medida.
        proc.send(f"on {SETTLE_S}", "ack on")
    start = time.monotonic_ns()
    time.sleep(block_s)
    end = time.monotonic_ns()
    if proc is not None:
        proc.send("off", "ack off")
    snap_t1 = time.monotonic_ns()
    after = server_cost.snapshot(engine, admin)
    if arm == "instrumentation" and side != treat:
        # Los brazos estan mezclados: el resto corre con la configuracion desplegada.
        toggle.on()
    time.sleep(GAP_S)
    return {"arm": arm, "pair": pair, "side": side, "start_ns": start, "end_ns": end,
            # El delta de servidor cubre tambien la espera del "off" (un ciclo
            # en curso se termina): se divide por ese lapso, no por block_s.
            "server": server_cost.delta(before, after),
            "server_span_ns": snap_t1 - snap_t0,
            "server_error": before.get("error") or after.get("error")}


# --- analisis -----------------------------------------------------------------------------

def _pairs_of(arm_blocks, base, treat, samples, settle_ns):
    by_pair = {}
    for b in arm_blocks:
        by_pair.setdefault(b["pair"], {})[b["side"]] = b
    pairs = []
    for idx in sorted(by_pair):
        sides = by_pair[idx]
        if base not in sides or treat not in sides:
            continue
        row = {}
        for key, b in (("base", sides[base]), ("treat", sides[treat])):
            ok = st.samples_in_block(samples, b["start_ns"], b["end_ns"], settle_ns)
            row[key] = st.by_template(ok)
            row[f"{key}_n"] = len(ok)
            row[f"{key}_s"] = (b["end_ns"] - b["start_ns"] - settle_ns) / 1e9
            row[f"{key}_failures"] = st.failures_in_block(samples, b["start_ns"], b["end_ns"])
        pairs.append(row)
    return pairs


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _server_side(arm_blocks, side):
    rows = [b for b in arm_blocks if b["side"] == side]
    keys = sorted({k for b in rows for k in b["server"]})
    out = {}
    for k in keys:
        per_s = [b["server"][k] / (b["server_span_ns"] / 1e9)
                 for b in rows if k in b["server"]]
        out[f"{k}_per_s"] = _mean(per_s)
    if "cpu_usec_per_s" in out and out["cpu_usec_per_s"] is not None:
        # usec de CPU por segundo de reloj / 1e4 = % de un core.
        out["cpu_pct_of_core"] = out.pop("cpu_usec_per_s") / 1e4
    return out


def _cycles_in(cycles, blocks, mode):
    spans = [(b["start_ns"], b["end_ns"]) for b in blocks]
    return [c for c in cycles if c.get("mode") == mode
            and any(s <= c["t0_ns"] < e for s, e in spans)]


def _cycle_summary(cycles, treat_blocks, settle_ns):
    walls = [(c["t1_ns"] - c["t0_ns"]) / 1e9 for c in cycles]
    measured = sum(b["end_ns"] - b["start_ns"] - settle_ns for b in treat_blocks) / 1e9
    errors = sorted({c["error"] for c in cycles if c.get("error")})
    return {
        "cycles": len(cycles),
        "failed": sum(1 for c in cycles if not c.get("ok")),
        "errors": errors[:5],
        "wall_s_mean": _mean(walls),
        "wall_s_max": max(walls) if walls else None,
        "duty_cycle_pct": (sum(walls) / measured * 100.0) if measured else None,
        "candidates_mean": _mean([c.get("candidates") for c in cycles]),
        "auto_explain_plans_mean": _mean([c.get("auto_explain_plans") for c in cycles]),
        "log_bytes_read_mean": _mean([c.get("log_bytes_read") for c in cycles]),
    }


def _during_extraction(samples, treat_blocks, cycles, settle_ns):
    """Latencia media de las queries que se solapan con un ciclo vs las que no,
    dentro de los bloques con pipeline. Cota del peor caso: no es el veredicto."""
    spans = [(c["t0_ns"], c["t1_ns"]) for c in cycles]
    inside, outside = [], []
    for b in treat_blocks:
        for s in st.samples_in_block(samples, b["start_ns"], b["end_ns"], settle_ns):
            (inside if st.overlaps(s, spans) else outside).append(s)
    return {
        "n_overlapping": len(inside),
        "n_clear": len(outside),
        "mean_pct": st.geomean_ratio_pct(st.by_template(outside), st.by_template(inside),
                                         "mean", min_n=5),
        "p95_pct": st.geomean_ratio_pct(st.by_template(outside), st.by_template(inside),
                                        "p95", min_n=20),
    }


def analyze_engine(engine, arms, blocks, samples, cycles, seed, args, toggle):
    settle_ns = int(SETTLE_S * 1e9)
    out = {"samples_total": len(samples),
           "failures_total": sum(1 for s in samples if not s[3]),
           "arms": {}}
    mode_of = {"pipeline": "pipeline", "stress": "pipeline", "positive_control": "burn"}
    for arm, _n, base, treat in arms:
        arm_blocks = [b for b in blocks if b["arm"] == arm]
        pairs = _pairs_of(arm_blocks, base, treat, samples, settle_ns)
        res = st.analyze_arm(pairs, seed=seed, iters=args.bootstrap)
        res["failures"] = sum(p["base_failures"] + p["treat_failures"] for p in pairs)
        res["server_base"] = _server_side(arm_blocks, base)
        res["server_treat"] = _server_side(arm_blocks, treat)
        res["server_errors"] = sorted({b["server_error"] for b in arm_blocks if b["server_error"]})
        if arm in mode_of:
            treat_blocks = [b for b in arm_blocks if b["side"] == treat]
            arm_cycles = _cycles_in(cycles, treat_blocks, mode_of[arm])
            res["cycles"] = _cycle_summary(arm_cycles, treat_blocks, settle_ns)
            res["during_extraction"] = _during_extraction(samples, treat_blocks, arm_cycles,
                                                          settle_ns)
        if arm == "aa":
            res["noise_floor_ok"] = st.noise_ok(res["p95_ci"])
        elif arm in ("pipeline", "stress", "instrumentation"):
            res["verdict"] = st.verdict(res["p95_pct"], res["p95_ci"], res["enough_samples"])
        if arm == "instrumentation" and toggle is not None:
            res["toggled"] = toggle.describe()
        out["arms"][arm] = res

    aa = out["arms"].get("aa", {})
    pc = out["arms"].get("positive_control")
    if pc is not None:
        pc["detected"] = st.detected(pc.get("p95_ci"))
    out["verdict"] = out["arms"]["pipeline"].get("verdict", st.SIN_DATO)
    out["noise_floor_ok"] = aa.get("noise_floor_ok")
    out["health"] = health_rows(engine)
    out["warnings"] = _warnings(out)
    # Para re-analizar el run con los CSV sin volver a medir.
    out["blocks"] = blocks
    return out


def _warnings(res):
    w = []
    if res["noise_floor_ok"] is False:
        w.append("RUIDO_EXCESIVO: el A/A no cae dentro de +-5 %; el host no resuelve el umbral")
    if res["noise_floor_ok"] is None:
        w.append("sin A/A utilizable (menos de 2 pares o sin muestras)")
    for name, arm in res["arms"].items():
        cyc = arm.get("cycles")
        if cyc and cyc["failed"]:
            w.append(f"{name}: {cyc['failed']}/{cyc['cycles']} ciclos fallaron {cyc['errors']}")
        if cyc and not cyc["cycles"]:
            w.append(f"{name}: ningun ciclo cayo dentro de los bloques")
        if not arm.get("enough_samples"):
            w.append(f"{name}: menos de {st.MIN_SAMPLES_PER_TEMPLATE} muestras por plantilla")
        if arm.get("failures"):
            w.append(f"{name}: {arm['failures']} queries de la carga fallaron")
    pc = res["arms"].get("positive_control")
    if pc is not None and pc.get("detected") is False:
        w.append("CONTROL POSITIVO NO DETECTADO: el arnes no ve un costo conocido")
    health = res.get("health") or {}
    if (health.get("last_run") or {}).get("status") not in (None, "ok"):
        w.append(f"pipeline_health last_run = {health['last_run']['status']}")
    return w


# --- salida -------------------------------------------------------------------------------

def _f(v, unit="%", signed=True):
    if v is None:
        return "n/a"
    return f"{v:+.1f}{unit}" if signed else f"{v:.1f}{unit}"


def _ci(ci):
    lo, hi = ci or (None, None)
    return "n/a" if lo is None else f"[{lo:+.1f}, {hi:+.1f}]"


def print_summary(engines, final):
    print("\n" + "=" * 78)
    print("RESUMEN v2 (veredicto = p95 por plantilla, IC95 bootstrap por pares)")
    print("-" * 78)
    for engine, res in engines.items():
        print(f"{engine}: {res['verdict']}  (muestras={res['samples_total']})")
        for name, arm in res["arms"].items():
            line = (f"  {name:17} p95={_f(arm['p95_pct'])} IC={_ci(arm['p95_ci'])} "
                    f"media={_f(arm['mean_pct'])} thr={_f(arm['throughput_pct'])} "
                    f"n_min={min(arm['min_template_n_base'], arm['min_template_n_treat'])}")
            if "verdict" in arm:
                line += f" -> {arm['verdict']}"
            if "detected" in arm:
                line += f" -> detectado={arm['detected']}"
            if "noise_floor_ok" in arm:
                line += f" -> ruido_ok={arm['noise_floor_ok']}"
            print(line)
            cyc = arm.get("cycles")
            if cyc:
                dur = arm["during_extraction"]
                print(f"  {'':17} ciclos={cyc['cycles']} fallidos={cyc['failed']} "
                      f"wall={_f(cyc['wall_s_mean'], 's', False)} duty={_f(cyc['duty_cycle_pct'], '%', False)} "
                      f"durante_extraccion(media)={_f(dur['mean_pct'])}")
            cpu_b = arm["server_base"].get("cpu_pct_of_core")
            cpu_t = arm["server_treat"].get("cpu_pct_of_core")
            if cpu_b is not None and cpu_t is not None:
                print(f"  {'':17} cpu contenedor {cpu_b:.1f}% -> {cpu_t:.1f}% de un core")
        for w in res["warnings"]:
            print(f"  [aviso] {w}")
    print(f"umbral p95 < {st.UMBRAL_PCT}% (cota superior del IC) | GLOBAL: {final}")


def overall(engines):
    states = [r["verdict"] for r in engines.values()]
    for s in (st.NO_CUMPLE, st.SIN_DATO, st.INCONCLUSO):
        if s in states:
            return s
    return st.CUMPLE if states else st.SIN_DATO


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    parser.add_argument("--pairs", type=int, help="pares del brazo pipeline (pisa el perfil)")
    parser.add_argument("--block-s", type=float, help="segundos por bloque (pisa el perfil)")
    parser.add_argument("--cadence", type=float, default=10.0,
                        help="cadencia del pipeline (runner.DEFAULT_INTERVAL_S)")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--stress", action="store_true", help="brazo extra con cadencia de 2 s")
    parser.add_argument("--positive-control", action="store_true")
    parser.add_argument("--skip-instrumentation", action="store_true")
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--allow-external-pipeline", action="store_true")
    parser.add_argument("--warmup-s", type=float, default=WARMUP_S,
                        help="carga sola antes del primer bloque (subir tras levantar el sandbox)")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--bootstrap", type=int, default=st.BOOTSTRAP_ITERS)
    args = parser.parse_args(argv)
    # Corrida de ~1 h redirigida a un archivo: que el avance se vea en vivo.
    sys.stdout.reconfigure(line_buffering=True)

    cfg = PROFILES[args.profile]
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = LOGS / f"overhead_v2_{stamp}"

    print("=" * 78)
    print(f"SOBRECOSTO v2 | perfil={args.profile} motores={','.join(engines)} "
          f"cadencia={args.cadence:g}s workers={args.workers} semilla={seed}")
    if args.profile == "quick":
        print("perfil quick: prueba que el arnes funciona; NO es evidencia del informe")
    print("=" * 78)

    errors, degraded = preflight(engines, args.allow_degraded, args.allow_external_pipeline)
    for d in degraded:
        print(f"[aviso] {d}")
    if errors:
        print("Preflight fallo:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    run_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for i, engine in enumerate(engines):
        print(f"\n[{engine}]")
        results[engine] = run_engine_session(engine, cfg, args, run_dir, seed + i)

    final = overall(results)
    print_summary(results, final)
    payload = {
        "generated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "method": "paired_blocks_per_template_p95_bootstrap",
        "profile": args.profile,
        "config": {**cfg, "block_s": args.block_s or cfg["block_s"],
                   "pipeline_pairs": args.pairs or cfg["pipeline_pairs"],
                   "settle_s": SETTLE_S, "warmup_s": args.warmup_s, "cadence_s": args.cadence,
                   "stress_cadence_s": STRESS_CADENCE_S, "workers": args.workers,
                   "bootstrap": args.bootstrap, "seed": seed},
        "umbral_pct": st.UMBRAL_PCT,
        "preflight_warnings": degraded,
        "verdict": final,
        "engines": results,
    }
    path = run_dir / "result.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"JSON: {path}")
    return st.EXIT_CODES[final]


if __name__ == "__main__":
    sys.exit(main())
