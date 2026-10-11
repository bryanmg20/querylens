"""
QUERYLENS — A vs B de la confiabilidad de los EXPLAIN.

Mide si el EXPLAIN que obtiene el telemetry_pipeline alcanza para que un
detector acierte: por cada caso de un patron se inyecta una consulta, se toma
el snapshot con el pipeline indicado y se corre SOLO la regla de ese patron
(detection_engine de esta rama). El resultado se compara con lo esperado.

Las dos versiones que se comparan son dos telemetry_pipeline distintos:
  v1  feature/detection_engine          texto real desde active_queries
  v3  feature/detector-engine-api-rest  GENERIC_PLAN (PG) / QUERY_SAMPLE_TEXT (MySQL)
Se corre una vez con cada uno (--pipeline-dir) y despues se comparan.

FLUJO DE UN PATRON (por motor)
  1. setup_<motor>.sql del patron: tablas, datos, indices, ANALYZE.
  2. Por caso:
     a. reset de estadisticas del motor (pg_stat_statements / digest de MySQL),
        para que el caso no herede historial ni QUERY_SAMPLE_TEXT del anterior.
     b. carga continua con la consulta del caso (--workers sesiones), sin
        snapshots durante --warmup segundos: es el historial que necesita v3
        y la que mantiene la consulta corriendo para que v1 la vea activa.
     c. snapshot con el pipeline; si la consulta del caso no tiene EXPLAIN se
        reintenta hasta --attempts veces (v1 depende de verla en ejecucion).
     d. se detiene la carga y se evalua la regla sobre el snapshot.
  3. teardown.sql del patron.

CLASIFICACION DE CADA CASO
  VP / FN / FP / VN   con EXPLAIN de la consulta del caso
  detecta / no_detecta  con EXPLAIN, en casos sin esperado definido
                      (expected=None); no entran en las metricas
  sin_explain         la consulta fue candidata pero no tuvo EXPLAIN
  no_candidata        la consulta no entro en top_impact_queries
  no_encontrada       la consulta no aparece en statements

REQUISITOS
  - Sandbox levantado (ql_sandbox: docker compose up -d). No usa querylens_db.
  - venv en telemetry_pipeline/ y en detection_engine/ (de esta rama).

USO (desde la raiz del repo)
  python ql_sandbox/explain_ab/run_ab.py list
  python ql_sandbox/explain_ab/run_ab.py run ap02 --version v1 --engine both
  python ql_sandbox/explain_ab/run_ab.py run ap02 --version v3 --engine both \\
      --pipeline-dir ../querylens-v3/telemetry_pipeline
  python ql_sandbox/explain_ab/run_ab.py compare ap02 --engine both
"""

import argparse
import importlib.util
import json
import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PATTERNS_DIR = HERE / "patterns"
RESULTS_DIR = HERE / "results"

PG_CONTAINER = "ql_postgres"
MYSQL_CONTAINER = "ql_mysql"
# Las sesiones de carga se marcan para poder cortarlas todas al cerrar el caso:
# en PG por application_name; en MySQL (el cliente no permite marcar la sesion)
# con un usuario propio que solo usa este harness
LOAD_APP_NAME = "ql_ab_load"
LOAD_MYSQL_USER = "ql_ab_load"
LOAD_MYSQL_PASSWORD = "ql_ab_load"
# segundos maximos esperando que desaparezcan las sesiones de carga
LOAD_STOP_TIMEOUT_SECONDS = 60

OUTCOMES = ("VP", "FN", "FP", "VN", "detecta", "no_detecta", "sin_explain", "no_candidata", "no_encontrada")


# ------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(
    cmd: list[str],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> str:
    result = subprocess.run(
        cmd, input=input_text, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Fallo: {' '.join(cmd[:6])}...\n--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
        )
    return result.stdout


def venv_python(project_dir: Path) -> str | None:
    for candidate in (project_dir / "venv" / "Scripts" / "python.exe", project_dir / "venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return None


def exec_sql(engine: str, sql: str, *, root: bool = False) -> str:
    if engine == "postgres":
        # ql_user es el POSTGRES_USER del contenedor, o sea superusuario
        cmd = ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "ql_user", "-d", "ql_demo",
               "-v", "ON_ERROR_STOP=1", "-q", "-At", "-X"]
    elif root:
        cmd = ["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uroot", "-pql_root", "-N", "ql_demo"]
    else:
        cmd = ["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uapp_user", "-papp_pass", "-N", "ql_demo"]
    return run(cmd, input_text=sql)


def reset_stats(engine: str) -> None:
    if engine == "postgres":
        exec_sql(engine, "SELECT pg_stat_statements_reset();")
    else:
        exec_sql(engine, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;", root=True)


def git_info(path: Path) -> dict:
    def git(*args: str) -> str:
        return run(["git", "-C", str(path), *args], check=False).strip()
    return {
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": git("rev-parse", "--short", "HEAD"),
        # v1 corre con cambios sin commitear del pipeline: queda registrado
        "dirty": bool(git("status", "--porcelain", "--", ".")),
    }


# ------------------------------------------------------------------
# Patrones y casos
# ------------------------------------------------------------------

def pattern_dirs() -> list[Path]:
    return sorted(p for p in PATTERNS_DIR.iterdir() if p.is_dir() and (p / "cases.py").exists())


def find_pattern(name: str) -> Path:
    matches = [p for p in pattern_dirs() if p.name == name or p.name.startswith(name + "_")]
    if len(matches) != 1:
        raise SystemExit(f"Patron '{name}' no encontrado o ambiguo. Disponibles: {[p.name for p in pattern_dirs()]}")
    return matches[0]


def load_cases(pattern_dir: Path):
    spec = importlib.util.spec_from_file_location(f"cases_{pattern_dir.name}", pattern_dir / "cases.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RULE, module.CASES


def case_sql(case: dict, engine: str) -> str:
    sql = case["sql"]
    if isinstance(sql, dict):
        sql = sql[engine]
    return sql() if callable(sql) else sql


# ------------------------------------------------------------------
# Carga continua de la consulta de un caso
# ------------------------------------------------------------------

class Load:
    """--workers sesiones que ejecutan la consulta del caso sin pausa. Cada una es
    un cliente psql/mysql dentro del contenedor del motor, alimentado por stdin
    desde un hilo de este proceso (literal nuevo en cada ejecucion)."""

    def __init__(self, engine: str, case: dict, workers: int):
        self.engine = engine
        self.case = case
        self.workers = workers
        self.stop_event = threading.Event()
        self.procs: list[subprocess.Popen] = []
        self.threads: list[threading.Thread] = []

    def _cmd(self) -> list[str]:
        if self.engine == "postgres":
            return ["docker", "exec", "-i", "-e", f"PGAPPNAME={LOAD_APP_NAME}", PG_CONTAINER,
                    "psql", "-U", "ql_user", "-d", "ql_demo", "-q", "-X", "-o", "/dev/null"]
        return ["docker", "exec", "-i", MYSQL_CONTAINER,
                "mysql", f"-u{LOAD_MYSQL_USER}", f"-p{LOAD_MYSQL_PASSWORD}", "--force", "--skip-reconnect", "ql_demo"]

    def _feed(self, proc: subprocess.Popen) -> None:
        try:
            while not self.stop_event.is_set():
                proc.stdin.write((case_sql(self.case, self.engine) + ";\n").encode("utf-8"))
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def start(self) -> None:
        for _ in range(self.workers):
            proc = subprocess.Popen(
                self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            thread = threading.Thread(target=self._feed, args=(proc,), daemon=True)
            thread.start()
            self.procs.append(proc)
            self.threads.append(thread)

    def stop(self) -> None:
        self.stop_event.set()
        # el pipe puede tener cientos de consultas encoladas que se seguirian
        # ejecutando y caerian en el siguiente caso: se cortan las sesiones en el servidor
        terminate_load_sessions(self.engine)
        for proc in self.procs:
            try:
                proc.stdin.close()
            except OSError:
                pass
        for proc in self.procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        for thread in self.threads:
            thread.join(timeout=5)
        wait_no_load_sessions(self.engine)


def load_session_ids(engine: str) -> list[str]:
    if engine == "postgres":
        sql = f"SELECT pid FROM pg_stat_activity WHERE application_name = '{LOAD_APP_NAME}';"
        return exec_sql(engine, sql).split()
    sql = f"SELECT id FROM information_schema.processlist WHERE user = '{LOAD_MYSQL_USER}';"
    return exec_sql(engine, sql, root=True).split()


def terminate_load_sessions(engine: str) -> None:
    ids = [pid for pid in load_session_ids(engine) if pid.isdigit()]
    if not ids:
        return
    if engine == "postgres":
        exec_sql(engine, "".join(f"SELECT pg_terminate_backend({pid});" for pid in ids))
    else:
        # la sesion puede terminar sola entre el SELECT y el KILL: no es un error
        for pid in ids:
            run(["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uroot", "-pql_root", "-N"],
                input_text=f"KILL {pid};", check=False)


def wait_no_load_sessions(engine: str) -> None:
    # el siguiente caso no puede empezar con carga del anterior: contaminaria sus
    # estadisticas (y el caso podria terminar evaluando la consulta equivocada)
    deadline = time.monotonic() + LOAD_STOP_TIMEOUT_SECONDS
    while load_session_ids(engine):
        if time.monotonic() > deadline:
            raise RuntimeError(f"{engine}: siguen sesiones de carga tras {LOAD_STOP_TIMEOUT_SECONDS}s")
        terminate_load_sessions(engine)
        time.sleep(1)


def ensure_mysql_load_user() -> None:
    exec_sql("mysql", (
        f"CREATE USER IF NOT EXISTS '{LOAD_MYSQL_USER}'@'%' IDENTIFIED BY '{LOAD_MYSQL_PASSWORD}';"
        f"GRANT SELECT ON ql_demo.* TO '{LOAD_MYSQL_USER}'@'%';"
    ), root=True)


# ------------------------------------------------------------------
# Snapshot y evaluacion
# ------------------------------------------------------------------

# un snapshot normal tarda segundos; si se cuelga se cuenta como intento fallido
SNAPSHOT_TIMEOUT_SECONDS = 180


def take_snapshot(opts, engine: str, out_file: Path) -> dict:
    out_file.unlink(missing_ok=True)
    try:
        run(
            [opts.pipeline_python, str(HERE / "snapshot_once.py"), str(opts.pipeline_dir), engine, str(out_file)],
            timeout=SNAPSHOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log(f"    snapshot sin terminar tras {SNAPSHOT_TIMEOUT_SECONDS}s: se cuenta como intento fallido")
        return {}
    return json.loads(out_file.read_text(encoding="utf-8"))


def case_statements(payload: dict, case: dict) -> list[dict]:
    # tras el reset solo deberia correr la consulta del caso sobre su tabla
    table = case["table"].lower()
    return [
        statement for statement in payload.get("statements") or []
        if table in (statement.get("query_text") or "").replace("`", "").lower()
        and (statement.get("query_text") or "").lstrip().upper().startswith(("SELECT", "WITH"))
    ]


def find_case_query_id(payload: dict, case: dict) -> str | None:
    # si hubiera mas de una forma se toma la mas ejecutada; run_case lo deja anotado
    matches = case_statements(payload, case)
    if not matches:
        return None
    best = max(matches, key=lambda statement: statement.get("execution_count") or 0)
    return str(best.get("query_id"))


def explain_for(payload: dict, query_id: str) -> dict | None:
    for explain in payload.get("canonic_explains") or []:
        if str(explain.get("query_id")) == query_id:
            return explain
    return None


def is_candidate(payload: dict, query_id: str) -> bool:
    return any(str(c.get("query_id")) == query_id for c in payload.get("top_impact_queries") or [])


def scans_of(canonical_plan: dict | None) -> list[dict]:
    return [
        {key: op.get(key) for key in ("relation", "access_method", "index_name", "estimated_rows", "predicate")}
        for op in (canonical_plan or {}).get("physical_operations", [])
        if op.get("type") == "scan"
    ]


def evaluate(opts, rule: str, snapshot_file: Path) -> list[dict]:
    out = run([opts.engine_python, str(HERE / "evaluate.py"), str(opts.engine_dir), rule, str(snapshot_file)])
    return json.loads(out.strip().splitlines()[-1])


def classify(expected: bool | None, detected: bool) -> str:
    if expected is None:
        return "detecta" if detected else "no_detecta"
    if expected:
        return "VP" if detected else "FN"
    return "FP" if detected else "VN"


def expected_label(expected: bool | None) -> str:
    return "?" if expected is None else ("si" if expected else "no")


def avg_rows_per_call(payload: dict, query_id: str | None) -> float | None:
    for statement in payload.get("statements") or []:
        if str(statement.get("query_id")) == query_id and statement.get("avg_rows_per_call") is not None:
            return float(statement["avg_rows_per_call"])
    return None


def run_case(opts, rule: str, case: dict, engine: str, snapshots_dir: Path) -> dict:
    log(f"  caso {case['id']} (esperado: {expected_label(case['expected'])})")
    reset_stats(engine)
    # ventana del caso (UTC), para cruzar con logs del servidor (ej. auto_explain)
    case_started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    load = Load(engine, case, opts.workers)
    load.start()
    attempts = 0
    query_id = None
    payload: dict = {}
    snapshot_file = snapshots_dir / f"{engine}_{case['id']}.json"
    try:
        time.sleep(opts.warmup)
        for attempts in range(1, opts.attempts + 1):
            payload = take_snapshot(opts, engine, snapshot_file)
            query_id = find_case_query_id(payload, case)
            if query_id is not None and explain_for(payload, query_id) is not None:
                break
            time.sleep(2)
    finally:
        load.stop()
    case_ended = datetime.now(timezone.utc).isoformat(timespec="seconds")

    others = [
        statement.get("query_text") for statement in case_statements(payload, case)
        if str(statement.get("query_id")) != query_id
    ]
    if others:
        log(f"    AVISO: otras consultas sobre {case['table']} en el snapshot: {others}")

    result = {
        "id": case["id"],
        "expected": case["expected"],
        "nota": case.get("nota"),
        "attempts": attempts,
        "started_at": case_started,
        "ended_at": case_ended,
        "query_id": query_id,
        "snapshot": str(snapshot_file.relative_to(HERE)),
        # deberia quedar vacio: si no, el caso pudo evaluar con carga ajena
        "otras_consultas": others,
        # filas reales por ejecucion (pg_stat_statements / digest de MySQL)
        "avg_rows_per_call": avg_rows_per_call(payload, query_id),
        "findings": [],
        "plan_scans": [],
    }
    if query_id is None:
        result["outcome"] = "no_encontrada"
    elif not is_candidate(payload, query_id):
        result["outcome"] = "no_candidata"
    else:
        explain = explain_for(payload, query_id)
        if explain is None:
            result["outcome"] = "sin_explain"
        else:
            # lo que vio el detector, para explicar diferencias entre versiones
            result["plan_scans"] = scans_of(explain.get("canonical_plan"))
            result["plan_output_rows"] = ((explain.get("canonical_plan") or {}).get("estimates") or {}).get("output_rows")
            # v3: generic (plan generico de PG), custom (variante de pg_stats), sample (MySQL)
            result["explain_source"] = explain.get("explain_source")
            # v3 con pg_stats (feature/pg-stats-explains): planes extra con valores
            # reales de la columna. Los detectores de esta rama no los leen; se
            # guardan para ver que aportarian
            if explain.get("plan_stability") or explain.get("plan_variants"):
                result["plan_stability"] = explain.get("plan_stability")
                result["variant_scans"] = {
                    variant.get("label"): scans_of(variant.get("canonical_plan"))
                    for variant in explain.get("plan_variants") or []
                }
            findings = [f for f in evaluate(opts, rule, snapshot_file) if str(f.get("query_id")) == query_id]
            result["findings"] = findings
            result["outcome"] = classify(case["expected"], bool(findings))

    log(f"    -> {result['outcome']} (intentos de snapshot: {attempts})")
    return result


def run_pattern(opts, engine: str) -> None:
    pattern_dir = find_pattern(opts.pattern)
    rule, cases = load_cases(pattern_dir)
    if opts.case:
        cases = [c for c in cases if c["id"] in opts.case]
    out_dir = RESULTS_DIR / pattern_dir.name
    snapshots_dir = out_dir / "snapshots" / opts.version
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    log(f"===== {pattern_dir.name} | {engine} | {opts.version} | regla {rule} =====")
    log(f"pipeline: {opts.pipeline_dir}")
    started = datetime.now().isoformat(timespec="seconds")
    if engine == "mysql":
        ensure_mysql_load_user()
    # por si una corrida anterior se corto sin limpiar su carga
    wait_no_load_sessions(engine)
    exec_sql(engine, (pattern_dir / f"setup_{engine}.sql").read_text(encoding="utf-8"))
    # pasos opcionales despues de la preparacion (ej. histograms_mysql.sql)
    for extra in opts.extra or []:
        extra_file = pattern_dir / f"{extra}_{engine}.sql"
        if not extra_file.exists():
            raise SystemExit(f"No existe {extra_file.relative_to(HERE)}: '{extra}' no aplica a {engine}.")
        log(f"paso extra: {extra_file.name}")
        exec_sql(engine, extra_file.read_text(encoding="utf-8"))
    results = []
    try:
        for case in cases:
            results.append(run_case(opts, rule, case, engine, snapshots_dir))
    finally:
        if not opts.keep:
            exec_sql(engine, (pattern_dir / "teardown.sql").read_text(encoding="utf-8"))

    report = {
        "pattern": pattern_dir.name,
        "rule": rule,
        "engine": engine,
        "version": opts.version,
        "started_at": started,
        "pipeline_dir": str(opts.pipeline_dir),
        "pipeline_git": git_info(opts.pipeline_dir),
        "params": {"workers": opts.workers, "warmup": opts.warmup, "attempts": opts.attempts, "extra": opts.extra or []},
        "cases": results,
    }
    out_file = out_dir / f"{engine}_{opts.version}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print_summary(report)
    log(f"resultado: {out_file.relative_to(ROOT)}")


# ------------------------------------------------------------------
# Resumen y comparacion
# ------------------------------------------------------------------

def counts(cases: list[dict]) -> dict:
    total = {outcome: 0 for outcome in OUTCOMES}
    for case in cases:
        total[case["outcome"]] += 1
    return total


def metrics(c: dict) -> str:
    def ratio(num: int, den: int) -> str:
        return f"{num / den:.2f}" if den else "-"
    return f"precision={ratio(c['VP'], c['VP'] + c['FP'])} recall={ratio(c['VP'], c['VP'] + c['FN'])}"


def print_summary(report: dict) -> None:
    c = counts(report["cases"])
    print(f"\n{report['pattern']} | {report['engine']} | {report['version']} | {len(report['cases'])} casos")
    print("  " + " ".join(f"{k}={v}" for k, v in c.items()))
    print("  " + metrics(c) + "\n")


def compare(opts) -> None:
    pattern_dir = find_pattern(opts.pattern)
    engines = ["postgres", "mysql"] if opts.engine == "both" else [opts.engine]
    for engine in engines:
        reports = {}
        for version in opts.versions:
            path = RESULTS_DIR / pattern_dir.name / f"{engine}_{version}.json"
            if path.exists():
                reports[version] = json.loads(path.read_text(encoding="utf-8"))
        if not reports:
            print(f"{engine}: sin resultados en {RESULTS_DIR / pattern_dir.name}")
            continue

        print(f"\n===== {pattern_dir.name} | {engine} =====\n")
        header = f"{'version':<8} {'casos':>5} " + " ".join(f"{k:>13}" for k in OUTCOMES) + "  metricas"
        print(header)
        for version, report in reports.items():
            c = counts(report["cases"])
            print(f"{version:<8} {len(report['cases']):>5} " + " ".join(f"{c[k]:>13}" for k in OUTCOMES)
                  + f"  {metrics(c)}")

        by_case: dict[str, dict] = {}
        for version, report in reports.items():
            for case in report["cases"]:
                by_case.setdefault(case["id"], {"expected": case["expected"]})[version] = case
        width = max(20, *(len(case_id) for case_id in by_case))
        print(f"\n{'caso':<{width}} {'esperado':<9} " + " ".join(f"{v:<13}" for v in reports) + " coinciden")
        for case_id, row in by_case.items():
            outcomes = [row[v]["outcome"] if v in row else "-" for v in reports]
            same = "si" if len(set(outcomes)) == 1 else "NO"
            print(f"{case_id:<{width}} {expected_label(row['expected']):<9} "
                  + " ".join(f"{o:<13}" for o in outcomes) + f" {same}")
        # estimacion de filas de cada version donde el resultado difiere: suele ser la causa
        for case_id, row in by_case.items():
            outcomes = {v: row[v]["outcome"] for v in reports if v in row}
            if len(set(outcomes.values())) > 1 or opts.detail:
                print(f"\n  {case_id}:")
                for version in outcomes:
                    case = row[version]
                    scans = [
                        f"{s['relation']} {s['access_method']} est={s['estimated_rows']}"
                        for s in case.get("plan_scans") or []
                    ]
                    plan = f"plan={case['explain_source']} " if case.get("explain_source") else ""
                    rows = f"raiz_est={case.get('plan_output_rows')} real={case.get('avg_rows_per_call')}"
                    print(f"    {version}: {outcomes[version]} | {plan}{rows} | {'; '.join(scans) or 'sin plan'}")


# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="A vs B de la confiabilidad de los EXPLAIN.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="lista los patrones y sus casos")

    p_run = sub.add_parser("run", help="corre los casos de un patron con un pipeline")
    p_run.add_argument("pattern", help="carpeta del patron o su prefijo (ap02)")
    p_run.add_argument("--version", required=True, help="etiqueta del resultado (v1, v3)")
    p_run.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    p_run.add_argument("--pipeline-dir", type=Path, default=ROOT / "telemetry_pipeline")
    p_run.add_argument("--pipeline-python", help="python con las dependencias del pipeline "
                       "(por defecto el venv del pipeline, o el de telemetry_pipeline/ de esta rama)")
    p_run.add_argument("--workers", type=int, default=6, help="sesiones de carga por caso")
    p_run.add_argument("--warmup", type=int, default=15, help="segundos de carga antes del primer snapshot")
    p_run.add_argument("--attempts", type=int, default=6, help="snapshots maximos por caso hasta tener EXPLAIN")
    p_run.add_argument("--case", action="append", help="correr solo este caso (se puede repetir)")
    p_run.add_argument("--keep", action="store_true", help="no borrar las tablas al terminar")
    p_run.add_argument("--extra", action="append", help="paso opcional tras la preparacion: corre "
                       "<extra>_<motor>.sql del patron (se puede repetir; ej. histograms)")

    p_cmp = sub.add_parser("compare", help="compara los resultados de las versiones")
    p_cmp.add_argument("pattern")
    p_cmp.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    p_cmp.add_argument("--versions", nargs="+", default=["v1", "v3"])
    p_cmp.add_argument("--detail", action="store_true", help="detalle de todos los casos, no solo los que difieren")

    opts = parser.parse_args()

    if opts.command == "list":
        for pattern_dir in pattern_dirs():
            rule, cases = load_cases(pattern_dir)
            print(f"{pattern_dir.name} (regla {rule})")
            for case in cases:
                print(f"  {case['id']:<26} esperado={expected_label(case['expected'])}")
        return

    if opts.command == "compare":
        compare(opts)
        return

    opts.pipeline_dir = opts.pipeline_dir.resolve()
    opts.pipeline_python = (
        opts.pipeline_python
        or venv_python(opts.pipeline_dir)
        or venv_python(ROOT / "telemetry_pipeline")
    )
    opts.engine_dir = ROOT / "detection_engine"
    opts.engine_python = venv_python(opts.engine_dir)
    if not opts.pipeline_python or not opts.engine_python:
        raise SystemExit("Falta el venv de telemetry_pipeline/ o de detection_engine/.")

    random.seed()
    engines = ["postgres", "mysql"] if opts.engine == "both" else [opts.engine]
    for engine in engines:
        run_pattern(opts, engine)


if __name__ == "__main__":
    main()
