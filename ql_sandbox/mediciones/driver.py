"""Driver de la medicion: corre DENTRO de un contenedor con la imagen del
pipeline (trae psycopg2 y pymysql), lanzado por measure.py. Ver README.md.

  python driver.py setup     <med> <motor> <out_dir>
  python driver.py prepare   <med> <motor> <out_dir> [--limit N]   query_id de cada caso (+ calibracion en ap07) -> cases.json
  python driver.py run       <med> <motor> <out_dir> <segundos> [--rate R]
  python driver.py inject    <med> <motor> <out_dir>
  python driver.py teardown  <med> <motor> <out_dir>
"""

import argparse
import json
import random
import re
import signal
import sys
import threading
import time
from pathlib import Path

import psycopg2
import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cases import MEASUREMENTS  # noqa: E402

HOST = "host.docker.internal"
PG = {"host": HOST, "port": 5432, "user": "ql_user", "password": "ql_pass", "dbname": "ql_demo"}
MY = {"host": HOST, "port": 3307, "user": "app_user", "password": "app_pass", "database": "ql_demo"}
MY_ROOT = {**MY, "user": "root", "password": "ql_root"}

N_POSITIVE = 100
N_CONTROL = 40


def connect(engine, root=False):
    if engine == "postgres":
        conn = psycopg2.connect(**PG)
        conn.autocommit = True
        return conn
    return pymysql.connect(**(MY_ROOT if root else MY), autocommit=True)


def log(message):
    print(f"[driver {time.strftime('%H:%M:%S')}] {message}", flush=True)


def execute_all(engine, statements):
    conn = connect(engine)
    cur = conn.cursor()
    for sql in statements:
        cur.execute(sql)
        if cur.description:
            cur.fetchall()
    conn.close()


# ------------------------------------------------------------------ query_id

def query_id(cur, engine, sql):
    """El query_id con el que el motor va a agregar esta consulta."""
    if engine == "postgres":
        cur.execute("EXPLAIN (VERBOSE) " + sql)
        for (line,) in cur.fetchall():
            match = re.search(r"Query Identifier: (-?\d+)", line)
            if match:
                return match.group(1)
        raise RuntimeError(f"sin Query Identifier: {sql[:80]}")
    # el pipeline arma el query_id de MySQL como <schema>/<DIGEST> (collectors/mysql/queries.py)
    cur.execute("SELECT STATEMENT_DIGEST(%s)", (sql,))
    return f"{MY['database']}/{cur.fetchone()[0].lower()}"


# ------------------------------------------------------------------ calibracion AP-07

def spill_counters(cur, engine, qid):
    """(spill del indicador del detector, sort merge passes) acumulados de la consulta."""
    if engine == "postgres":
        cur.execute("SELECT COALESCE(SUM(temp_blks_written), 0) FROM pg_stat_statements WHERE queryid = %s", (int(qid),))
        return int(cur.fetchone()[0]), 0
    cur.execute(
        "SELECT COALESCE(SUM(SUM_CREATED_TMP_DISK_TABLES), 0), COALESCE(SUM(SUM_SORT_MERGE_PASSES), 0) "
        "FROM performance_schema.events_statements_summary_by_digest WHERE DIGEST = %s", (qid.split("/", 1)[-1],))
    a, b = cur.fetchone()
    return int(a), int(b)


def calibrate_m07(engine, pool):
    """Corre cada candidato una vez, solo, y mide si derramo. Positivos: los que
    derraman (en MySQL, por tablas temporales en disco: lo que mira AP-07).
    Extra: MySQL que solo derrama por ordenamiento (merge passes). Controles:
    los que no derraman."""
    conn = connect(engine)
    cur = conn.cursor()
    admin = connect(engine, root=True)
    acur = admin.cursor()
    rng = random.Random(11)
    out = []
    for case in pool:
        qid = query_id(cur, engine, case.queries["target"])
        before = spill_counters(acur, engine, qid)
        started = time.monotonic()
        for stmt in case.session:
            cur.execute(stmt)
        error = None
        try:
            case.run(cur, rng)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
        elapsed = time.monotonic() - started
        after = spill_counters(acur, engine, qid)
        spill, merges = after[0] - before[0], after[1] - before[1]
        out.append((case, qid, spill, merges, elapsed, error))
        log(f"calibra {case.cid:<10} {case.role:<8} spill={spill:<6} merge={merges:<4} {elapsed * 1000:7.0f} ms {error or ''}")
    conn.close()
    admin.close()
    return out


def _round_robin(items, key, limit):
    """Hasta `limit` elementos, alternando entre grupos (plantillas) para variar."""
    groups = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    picked = []
    while len(picked) < limit and any(groups.values()):
        for name in list(groups):
            if groups[name] and len(picked) < limit:
                picked.append(groups[name].pop(0))
    return picked


def select_m07(engine, calibrated):
    """Positivos: los que derraman por cualquiera de los dos indicadores que lee
    AP-07 (en MySQL, tablas temporales en disco o merge passes de ordenamiento)."""
    positives, controls, discarded = [], [], []
    for case, qid, spill, merges, elapsed, error in calibrated:
        record = (case, qid, {"calibracion_spill": spill, "calibracion_merge_passes": merges,
                              "calibracion_ms": round(elapsed * 1000), "calibracion_error": error})
        if error:
            discarded.append((record, "error"))
        elif case.role == "positivo" and (spill > 0 or merges > 0):
            positives.append(record)
        elif case.role == "control" and spill == 0 and merges == 0:
            controls.append(record)
        else:
            discarded.append((record, "no derramo" if case.role == "positivo" else "control que derramo"))
    key = lambda record: record[0].meta["template"]  # noqa: E731
    chosen_p = _round_robin(positives, key, N_POSITIVE)
    chosen_c = _round_robin(controls, key, N_CONTROL)
    for record in positives:
        if record not in chosen_p:
            discarded.append((record, "positivo sobrante"))
    for record in controls:
        if record not in chosen_c:
            discarded.append((record, "control sobrante"))
    return chosen_p + chosen_c, discarded


# ------------------------------------------------------------------ casos

def load_cases(med, engine, out_dir):
    """Los casos de cases.json (los que eligio prepare), con su funcion run."""
    spec = MEASUREMENTS[med]
    every = spec["pool"](engine) if "pool" in spec else spec["cases"](engine)
    by_id = {case.cid: case for case in every}
    data = json.loads((out_dir / "cases.json").read_text(encoding="utf-8"))
    chosen = []
    for row in data["cases"]:
        case = by_id[row["cid"]]
        case.role = row["role"]
        case.category = row["category"]
        chosen.append(case)
    return chosen


def prepare(med, engine, out_dir, limit=None):
    spec = MEASUREMENTS[med]
    discarded = []
    if med == "ap07":
        pool = spec["pool"](engine)
        if limit:
            # modo de prueba: solo las dos tablas mas chicas
            pool = [case for case in pool if case.meta["table"] in ("ql_m07_t0", "ql_m07_t1")]
        records, discarded = select_m07(engine, calibrate_m07(engine, pool))
        rows = []
        conn = connect(engine)
        cur = conn.cursor()
        for case, qid, calibration in records:
            rows.append({"cid": case.cid, "role": case.role, "category": case.category,
                         "query_ids": {"target": qid}, "queries": case.queries, "meta": {**case.meta, **calibration}})
        conn.close()
    else:
        cases = spec["cases"](engine)
        conn = connect(engine)
        cur = conn.cursor()
        rows = []
        for case in cases:
            ids = {name: query_id(cur, engine, sql) for name, sql in case.queries.items()}
            rows.append({"cid": case.cid, "role": case.role, "category": case.category,
                         "query_ids": ids, "queries": case.queries, "meta": case.meta})
        conn.close()
    if limit:
        # modo de prueba: pocos casos de cada rol
        kept, seen = [], {}
        for row in rows:
            cap = limit if row["role"] == "positivo" else max(2, limit // 2)
            if seen.get(row["role"], 0) < cap:
                kept.append(row)
                seen[row["role"]] = seen.get(row["role"], 0) + 1
        rows = kept

    # cada consulta "target" tiene que tener su propio query_id
    owners = {}
    for row in rows:
        for name, qid in row["query_ids"].items():
            owners.setdefault(qid, []).append(f"{row['cid']}:{name}")
    collisions = {qid: who for qid, who in owners.items() if len(who) > 1}
    payload = {
        "measurement": med, "engine": engine, "cases": rows,
        "collisions": collisions,
        "discarded": [{"cid": record[0].cid, "role": record[0].role, "reason": reason,
                       "query": record[0].queries["target"], **record[2]} for record, reason in discarded],
    }
    (out_dir / "cases.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = {}
    for row in rows:
        counts[row["role"]] = counts.get(row["role"], 0) + 1
    log(f"casos: {counts} | colisiones de query_id: {len(collisions)}")
    if collisions:
        for qid, who in collisions.items():
            log(f"  colision {qid}: {who}")
        sys.exit(2)


# ------------------------------------------------------------------ carga

STOP = threading.Event()


def worker(engine, cases, stats, lock, seed, mode, rate, round_seconds):
    rng = random.Random(seed)
    conn = None
    while not STOP.is_set():
        try:
            if conn is None:
                conn = connect(engine)
                cur = conn.cursor()
            round_started = time.monotonic()
            for case in cases:
                if STOP.is_set():
                    break
                for _ in range(case.per_round if mode == "rounds" else 1):
                    try:
                        for stmt in case.session:
                            cur.execute(stmt)
                        case.run(cur, rng)
                        ok = True
                    except (psycopg2.OperationalError, pymysql.err.OperationalError, pymysql.err.InterfaceError):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        with lock:
                            stats[case.cid]["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    with lock:
                        stats[case.cid]["runs" if ok else "errors"] += 1
            if mode in ("paced", "rounds"):
                period = 1.0 / rate if mode == "paced" else round_seconds
                STOP.wait(max(0.0, period - (time.monotonic() - round_started)))
        except Exception as exc:  # noqa: BLE001  conexion caida: reconectar
            log(f"worker reconecta tras {type(exc).__name__}: {str(exc)[:120]}")
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
            STOP.wait(1)
    if conn is not None:
        conn.close()


def run_load(med, engine, out_dir, seconds, rate):
    cases = load_cases(med, engine, out_dir)
    # AP-07: lo mas rapido posible (basta una ejecucion por ventana);
    # AP-01: `rate` vueltas por segundo por caso; AP-06: rondas de 5 s
    mode, threads, round_seconds = {"ap07": ("free", 6, 0), "ap01": ("paced", 10, 0), "ap06": ("rounds", 8, 5.0)}[med]
    rng = random.Random(5)
    rng.shuffle(cases)
    buckets = [cases[i::threads] for i in range(threads)]
    stats = {case.cid: {"runs": 0, "errors": 0, "last_error": None} for case in cases}
    lock = threading.Lock()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    pool = [threading.Thread(target=worker, args=(engine, bucket, stats, lock, 100 + n, mode, rate, round_seconds),
                             daemon=True) for n, bucket in enumerate(buckets) if bucket]
    for thread in pool:
        thread.start()
    log(f"carga {med}/{engine}: {len(cases)} casos en {len(pool)} hilos ({mode}), hasta {seconds}s")
    started = time.monotonic()
    last_report = started
    while not STOP.is_set() and time.monotonic() - started < seconds:
        STOP.wait(1)
        if time.monotonic() - last_report >= 30:
            last_report = time.monotonic()
            with lock:
                runs = sum(s["runs"] for s in stats.values())
                errors = sum(s["errors"] for s in stats.values())
            log(f"  {int(last_report - started)}s: {runs} vueltas, {errors} errores")
            (out_dir / "driver_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    STOP.set()
    for thread in pool:
        thread.join(timeout=30)
    (out_dir / "driver_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    log("carga detenida")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["setup", "prepare", "run", "inject", "teardown"])
    parser.add_argument("med", choices=sorted(MEASUREMENTS))
    parser.add_argument("engine", choices=["postgres", "mysql"])
    parser.add_argument("out_dir")
    parser.add_argument("seconds", nargs="?", type=int, default=600)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = MEASUREMENTS[args.med]

    if args.command == "setup":
        started = time.monotonic()
        execute_all(args.engine, spec["setup"](args.engine))
        log(f"setup {args.med}/{args.engine} en {time.monotonic() - started:.0f}s")
    elif args.command == "prepare":
        prepare(args.med, args.engine, out_dir, args.limit)
    elif args.command == "run":
        run_load(args.med, args.engine, out_dir, args.seconds, args.rate)
    elif args.command == "inject":
        started = time.monotonic()
        statements = spec["inject"](args.engine)
        execute_all(args.engine, statements)
        log(f"inyeccion: {len(statements)} sentencias en {time.monotonic() - started:.1f}s")
    elif args.command == "teardown":
        execute_all(args.engine, spec["teardown"](args.engine))
        log(f"teardown {args.med}/{args.engine}")


if __name__ == "__main__":
    main()
