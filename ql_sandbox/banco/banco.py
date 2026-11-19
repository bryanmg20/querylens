"""
QUERYLENS — banco de pruebas de un solo comando (SegundoInforme, objetivo 7).

Ejecuta, sin supervision, los 22 escenarios de inyeccion (10 repeticiones cada
uno) y 10 corridas de control de 30 min por motor, y calcula las metricas M1 a
M4 de la Tabla 7 contra los manifiestos (manifiestos.py, metricas.py).

Todo pasa por el flujo real, como en una base de un cliente: la base del
sandbox registrada con `querylens add`, el servicio pipeline recolectando cada
10 s, el servicio detection-engine y public.hallazgos. La carga base
(sysbench oltp_read_write, 4 hilos) corre todo el tiempo en el motor que se
esta midiendo: las inyecciones se hacen sobre ella y las corridas de control
son la carga base sola.

Por motor, en este orden:
  1. escenarios de antipatrones cortos (tabla y carga de run_scenario.py)
  2. bloque de contencion (los casos de measure_ct.py, fase "positivo")
  3. escenario de linea base (AP-01, unos 12 min por repeticion)
  4. corridas de control

REQUISITOS
  - Sandbox (ql_postgres, ql_mysql, ql_sysbench) y compose de la raiz
    levantados, con las imagenes al dia.
  - Solo libreria estandar: cualquier python 3.10+.

USO (desde la raiz del repo)
  python ql_sandbox/banco/banco.py
      la campana completa (unas 18 h): 22 escenarios x 10 repeticiones y
      10 controles de 30 min por motor
  python ql_sandbox/banco/banco.py --smoke
      1 repeticion y 1 control de 2 min: comprueba que el banco funciona
  python ql_sandbox/banco/banco.py --reps 3 --controles 2 --minutos-control 10 --etiqueta calibracion
  python ql_sandbox/banco/banco.py --engine mysql --solo missing_index,ct,control
  python ql_sandbox/banco/banco.py --reanudar banco-campana-20261116-101500
      sigue una corrida cortada: salta lo que ya quedo guardado
  python ql_sandbox/banco/metricas.py ql_sandbox/banco/resultados/<corrida>
      vuelve a calcular el reporte sin correr nada

SALIDA  ql_sandbox/banco/resultados/<corrida>/
  meta.json        parametros de la corrida, commit y versiones de los motores
  corridas.jsonl   una linea por corrida terminada (inyeccion, control o
                   bloque de contencion) con sus hallazgos
  metricas.json    M1 a M4 por combinacion patologia-motor
  reporte.md       lo mismo, para leer; se actualiza al terminar cada corrida
  errores.log      corridas que fallaron (se repiten con --reanudar)

Codigo de salida: 0 si las cuatro metricas cumplen en las 24 combinaciones,
2 si alguna no cumple, 1 si quedaron corridas sin terminar.
"""

import argparse
import json
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "ql_sandbox" / "scenarios"))
sys.path.insert(0, str(ROOT / "ql_sandbox" / "mediciones"))

import manifiestos as mf  # noqa: E402
import measure_ct as ct  # noqa: E402
import metricas  # noqa: E402
import run_scenario as rs  # noqa: E402

RESULTS_DIR = HERE / "resultados"
LOAD_CONTAINER = rs.LOAD_CONTAINER
SANDBOX_CONTAINERS = ("ql_postgres", "ql_mysql", LOAD_CONTAINER)
ROOT_SERVICES = ("postgres", "pipeline", "detection-engine")
BASE_LOAD_SCRIPT = "/scripts/base_load.sh"
SYSBENCH_DRIVER = {"postgres": "pgsql", "mysql": "mysql"}

# Observacion de una inyeccion: como minimo 3 ventanas, para que los
# colaterales y los falsos positivos tengan con que salir aunque el esperado
# aparezca en la primera; como maximo el limite de M4 mas un margen, para que
# una deteccion tardia se vea como VP con M4 incumplida y no como FN.
OBSERVE_MIN_S = 30
OBSERVE_MAX_S = 180
# AP-01: ciclos de carga antes de eliminar el indice. 60 ventanas de linea
# base; el primer ciclo solo crea la fila de la consulta, y en MySQL el digest
# sobrevive a la tabla recreada, asi que hacen falta 61 ventanas propias para
# que la linea base no mire las de la repeticion anterior. Uno mas de margen.
BASELINE_CYCLES = 63
CYCLE_TIMEOUT_S = 120
ATTEMPTS = 2
MAX_CONSECUTIVE_FAILURES = 3

# Cuenta (y con "kill" termina) los procesos de la carga base de un motor.
# ql_sysbench no trae pkill ni ps: se buscan en /proc.
BASE_LOAD_PROCS = r"""
count=0
for dir in /proc/[0-9]*; do
  cmd=$(tr '\0' ' ' < "$dir/cmdline" 2>/dev/null) || continue
  case "$cmd" in
    "bash /scripts/base_load.sh $1 "*|"sysbench oltp_read_write --db-driver=$2 "*"--time=0 "*)
      count=$((count + 1)); [ "$3" = kill ] && kill "${dir#/proc/}" 2>/dev/null ;;
  esac
done
echo "$count"
"""


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def lit(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ql_now() -> str:
    return rs.querylens_sql("SELECT now();")


def wait_for(predicate, timeout: float, step: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


# ------------------------------------------------------------------
# Carga base
# ------------------------------------------------------------------

def _base_load_procs(engine: str, action: str = "count") -> int:
    out = rs.run(["docker", "exec", LOAD_CONTAINER, "bash", "-c", BASE_LOAD_PROCS, "_", engine,
                  SYSBENCH_DRIVER[engine], action])
    return int(out.strip() or 0)


def start_base_load(engine: str, rate: int) -> None:
    if _base_load_procs(engine):
        return
    rs.run(["docker", "exec", "-d", LOAD_CONTAINER, "bash", BASE_LOAD_SCRIPT, engine, str(rate)])
    # la primera vez crea las tablas sbtest antes de empezar
    if not wait_for(lambda: _base_load_procs(engine) > 0, 30):
        raise RuntimeError(f"la carga base de {engine} no arranco ({BASE_LOAD_SCRIPT} en {LOAD_CONTAINER})")
    log(f"{engine} | carga base en marcha (sysbench oltp_read_write, 4 hilos, {rate} tx/s)")


def stop_base_load(engine: str) -> None:
    for _ in range(5):
        if _base_load_procs(engine, "kill") == 0:
            return
        time.sleep(1)
    log(f"{engine} | aviso: la carga base sigue corriendo en {LOAD_CONTAINER}")


# ------------------------------------------------------------------
# Lo que ve QueryLens
# ------------------------------------------------------------------

def wait_cycle(db_id: str, since: str | None = None) -> None:
    """Espera a que el servicio pipeline termine un ciclo de la base empezado
    despues de `since` (por defecto, ahora) y a que el detector lo procese.
    Lo que ese ciclo encontro queda guardado antes de volver: es el corte entre
    una corrida y la siguiente."""
    since = since or ql_now()
    seen = (f"SELECT count(*) FROM pipeline_health.last_run WHERE db_id = {lit(db_id)}"
            f" AND started_at > {lit(since)}::timestamptz;")
    if not wait_for(lambda: rs.querylens_sql(seen) != "0", CYCLE_TIMEOUT_S):
        raise RuntimeError(f"el servicio pipeline no completo ningun ciclo de {db_id} en {CYCLE_TIMEOUT_S} s")
    if not wait_for(lambda: rs.queue_length() == 0, CYCLE_TIMEOUT_S):
        raise RuntimeError("el servicio detection-engine no vacio la cola")


def wait_cycles(db_id: str, since: str, cycles: int) -> None:
    count = (f"SELECT count(*) FROM pipeline_health.cycle_samples WHERE db_id = {lit(db_id)}"
             f" AND taken_at > {lit(since)}::timestamptz;")
    # un ciclo dura 10 s; se da el doble por si alguno se atrasa
    if not wait_for(lambda: int(rs.querylens_sql(count) or 0) >= cycles, cycles * mf.WINDOW_S * 2, step=5):
        raise RuntimeError(f"no se juntaron {cycles} ciclos de {db_id}")


def fetch_findings(db_id: str, since: str, until: str | None = None) -> list[dict]:
    """Problemas detectados en la ventana, uno por problema: cuando se detecto
    por primera vez dentro de ella y cuantas veces. La evidencia es la de su
    ultima deteccion dentro de la ventana (la del problema si esa deteccion es
    anterior a la columna).

    Para M7 cuenta, entre las detecciones de la ventana, las que no guardaron
    su evidencia y las que no quedaron enlazadas con su instantanea: sin
    snapshot_msg_id, o con uno que no esta en pgmq.a_analyze_job con el mismo
    db_id."""
    upper = f" AND d.detectado_en <= {lit(until)}::timestamptz" if until else ""
    raw = rs.querylens_sql(
        "SELECT coalesce(json_agg(t ORDER BY t.primera_deteccion), '[]') FROM ("
        " SELECT p.id AS problema_id, p.antipatron, p.codigo, p.table_name, p.query_id, p.database_name,"
        "        p.finding_key, p.severidad, coalesce(d.ultima_evidencia, p.evidencia) AS evidencia,"
        "        coalesce(p.explicacion, '') <> '' AS con_explicacion,"
        "        coalesce(p.recomendacion, '') <> '' AS con_recomendacion,"
        "        coalesce(p.riesgo, '') <> '' AS con_riesgo,"
        "        d.primera_deteccion, d.detecciones, d.deteccion_ids,"
        "        d.detecciones_sin_evidencia, d.detecciones_sin_enlace"
        " FROM public.problemas p JOIN ("
        "   SELECT d.problema_id, min(d.detectado_en) AS primera_deteccion, count(*) AS detecciones,"
        "          array_agg(d.id ORDER BY d.id) AS deteccion_ids,"
        "          (array_agg(d.evidencia ORDER BY d.id DESC))[1] AS ultima_evidencia,"
        "          count(*) FILTER (WHERE d.evidencia IS NULL) AS detecciones_sin_evidencia,"
        "          count(*) FILTER (WHERE a.msg_id IS NULL) AS detecciones_sin_enlace"
        "   FROM public.detecciones d"
        "   JOIN public.problemas dp ON dp.id = d.problema_id"
        "   LEFT JOIN pgmq.a_analyze_job a"
        "     ON a.msg_id = d.snapshot_msg_id AND a.message ->> 'db_id' IS NOT DISTINCT FROM dp.db_id"
        f"   WHERE d.detectado_en >= {lit(since)}::timestamptz{upper}"
        "   GROUP BY d.problema_id) d ON d.problema_id = p.id"
        f" WHERE p.db_id = {lit(db_id)}) t;"
    )
    return json.loads(raw or "[]")


def stats_window_minutes(engine: str) -> float:
    """Minutos que cubren las estadisticas de uso de los indices: desde el
    arranque del motor o, en Postgres, desde el ultimo reset si fue despues."""
    if engine == "postgres":
        return float(ct.pg_sandbox(
            "SELECT extract(epoch FROM now() - greatest(pg_postmaster_start_time(), coalesce(stats_reset,"
            " pg_postmaster_start_time()))) / 60 FROM pg_stat_database WHERE datname = current_database();"))
    out = ct.mysql_root("SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Uptime';")
    return float(out.strip().splitlines()[-1]) / 60


def reset_statement_stats(engine: str) -> None:
    """Pone en cero las estadisticas de sentencias del motor (pg_stat_statements
    o el resumen por digest de MySQL).

    El pipeline elige las consultas a analizar (EXPLAIN y auto_explain) por el
    top 10 de tiempo total acumulado. Con horas de carga base acumuladas, la
    consulta nueva de un escenario tarda mas en entrar a ese top 10, y AP-05
    indice no utilizado o redundante y AP-08 error grave de estimacion de
    cardinalidad se atrasan (calibracion del 2026-11-18: 139 s con 230 min
    acumulados frente a 40 s con 31 min). Es una limitacion del pipeline, no
    del banco, y se declara en el informe. No toca las estadisticas de uso de
    los indices ni el arranque del motor, asi que la ventana de AP-05 sigue
    igual."""
    if engine == "postgres":
        ct.pg_sandbox("SELECT pg_stat_statements_reset();")
    else:
        ct.mysql_root("TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")
    log(f"{engine} | estadisticas de sentencias en cero")


# ------------------------------------------------------------------
# Corridas
# ------------------------------------------------------------------

def wait_stats_window(engine: str, name: str) -> None:
    """"No utilizado" (AP-05) solo se evalua con la ventana de estadisticas de
    los indices cerrada: si el motor lleva poco encendido, se espera."""
    missing = mf.AP05_STATS_WINDOW_MIN + 1 - stats_window_minutes(engine)
    if missing > 0:
        log(f"{engine} | {name}: el motor lleva poco encendido; se esperan {missing:.0f} min")
        time.sleep(missing * 60)


def run_injection(engine: str, db_id: str, name: str, rep: int) -> dict:
    scenario = rs.SCENARIOS[name]
    manifest = mf.AP_MANIFESTS[name]
    wanted = [e for e in manifest.expected if engine in e.engines]
    extra = {}

    if "AP-05" in manifest.codes():
        wait_stats_window(engine, name)
        extra["ventana_estadisticas_min"] = round(stats_window_minutes(engine), 1)

    try:
        rs.exec_sql(engine, rs.SETUP_SQL[engine])
        if scenario.extra_setup:
            rs.exec_sql(engine, scenario.extra_setup(engine))
        # despues de preparar la tabla: con temptable_use_mmap = OFF, el INSERT
        # de 1.000.000 de filas de MySQL falla con ERROR 1146 sobre su tabla
        # temporal (el error de TempTable del sandbox); solo lo necesita la carga
        if engine in scenario.server_setup:
            rs.exec_server_sql(engine, scenario.server_setup[engine])
        # corte: lo que el pipeline vea de la preparacion queda antes del inicio
        wait_cycle(db_id)

        workload_seconds = OBSERVE_MAX_S + 120
        if scenario.inject_after_cycle is not None:
            # AP-01: la carga corre sana hasta juntar la linea base y la
            # inyeccion es eliminar el indice
            workload_seconds += BASELINE_CYCLES * scenario.interval_seconds * 2
            load_start = ql_now()
            rs.start_workload(engine, scenario.workload, scenario.workers, workload_seconds)
            wait_cycles(db_id, load_start, BASELINE_CYCLES)
            extra["carga_desde"] = load_start
            start = ql_now()
            rs.exec_sql(engine, scenario.inject_sql[engine])
        else:
            start = ql_now()
            rs.start_workload(engine, scenario.workload, scenario.workers, workload_seconds)

        began = time.monotonic()
        while True:
            elapsed = time.monotonic() - began
            findings = fetch_findings(db_id, start)
            found = all(any(mf.matches(e.criterio, f) for f in findings) for e in wanted)
            if (found and elapsed >= OBSERVE_MIN_S) or elapsed >= OBSERVE_MAX_S:
                break
            time.sleep(2)
        end = ql_now()
        findings = fetch_findings(db_id, start, end)
    finally:
        rs.stop_workload()
        if engine in scenario.server_teardown:
            rs.exec_server_sql(engine, scenario.server_teardown[engine])
        rs.exec_sql(engine, rs.TEARDOWN_SQL)
        rs.forget_scenario_statements(engine)

    return {"tipo": "inyeccion", "engine": engine, "scenario": name, "rep": rep, "db_id": db_id,
            "inicio": start, "fin": end, "hallazgos": findings, **extra}


def run_control(engine: str, db_id: str, rep: int, minutes: float, rate: int) -> dict:
    wait_cycle(db_id)
    start = ql_now()
    deadline = time.monotonic() + minutes * 60
    restarts = 0
    while time.monotonic() < deadline:
        time.sleep(min(30.0, max(0.0, deadline - time.monotonic())))
        if not _base_load_procs(engine):
            restarts += 1
            start_base_load(engine, rate)
    end = ql_now()
    return {"tipo": "control", "engine": engine, "rep": rep, "db_id": db_id, "inicio": start, "fin": end,
            "hallazgos": fetch_findings(db_id, start, end), "reinicios_carga_base": restarts}


def run_contention(engine: str, db_id: str, reps: int) -> dict:
    wait_cycle(db_id)
    start = ql_now()
    cases = sorted(set(mf.CT_CASES[engine].values()))
    options = argparse.Namespace(
        smoke=False, reps=reps, cases=",".join(cases), phases="positivo",
        pg_version=ct.DEFAULT_VERSION["postgres"], mysql_version=ct.DEFAULT_VERSION["mysql"],
    )
    ct.RESULTS_DIR.mkdir(exist_ok=True)
    run_id, _ = ct.measure(engine, options)
    end = ql_now()
    results = json.loads((ct.RESULTS_DIR / run_id / "results.json").read_text(encoding="utf-8"))
    return {"tipo": "ct", "engine": engine, "db_id": db_id, "inicio": start, "fin": end,
            "ct_run_id": run_id,
            "ct": {"results": results["results"], "summary": results["summary"], "meta": results["meta"]},
            "hallazgos": fetch_findings(db_id, start, end)}


# ------------------------------------------------------------------
# Plan y estado de la campana
# ------------------------------------------------------------------

def plan(args) -> list[tuple]:
    """Corridas de la campana, en orden: (id, motor, tipo, escenario, repeticion)."""
    only = set(args.solo.split(",")) if args.solo else None
    engines = list(mf.ENGINES) if args.engine == "both" else [args.engine]

    def wanted(name: str) -> bool:
        return only is None or name in only

    units = []
    for engine in engines:
        for name in mf.AP_ORDER[:-1]:
            if wanted(name):
                units += [(f"iny|{engine}|{name}|{rep}", engine, "inyeccion", name, rep)
                          for rep in range(1, args.reps + 1)]
        if wanted("ct"):
            units.append((f"ct|{engine}", engine, "ct", None, None))
        name = mf.AP_ORDER[-1]
        if wanted(name):
            units += [(f"iny|{engine}|{name}|{rep}", engine, "inyeccion", name, rep)
                      for rep in range(1, args.reps + 1)]
        if wanted("control"):
            units += [(f"control|{engine}|{rep}", engine, "control", None, rep)
                      for rep in range(1, args.controles + 1)]
    return units


def git_commit() -> str:
    def git(*command):
        return subprocess.run(["git", *command], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    commit = git("rev-parse", "--short", "HEAD") or "?"
    return commit + ("+cambios" if git("status", "--porcelain") else "")


def preflight() -> None:
    result = subprocess.run(["docker", "inspect", "-f", "{{.Name}} {{.State.Running}}", *SANDBOX_CONTAINERS],
                            capture_output=True, text=True)
    stopped = [line.split()[0].lstrip("/") for line in result.stdout.splitlines() if line.endswith("false")]
    if result.returncode != 0 or stopped:
        sys.exit(f"Sandbox detenido o incompleto ({', '.join(stopped) or result.stderr.strip()}): "
                 f"docker start {' '.join(SANDBOX_CONTAINERS)}")
    for service in ROOT_SERVICES:
        try:
            rs.compose_container(service)
        except RuntimeError as e:
            sys.exit(f"{e}: docker compose up -d")


def run_units(units: list[tuple], args, out: Path) -> int:
    """Corre las corridas pendientes de un motor tras otro. Devuelve cuantas
    quedaron sin terminar."""
    done = {unit["id"] for unit in metricas.load_units(out)}
    pending = [unit for unit in units if unit[0] not in done]
    log(f"{len(units)} corridas en el plan, {len(units) - len(pending)} ya hechas, {len(pending)} pendientes")
    unfinished = 0
    for engine in dict.fromkeys(unit[1] for unit in pending):
        unfinished += run_engine(engine, [unit for unit in pending if unit[1] == engine], args, out)
    return unfinished


def run_engine(engine: str, units: list[tuple], args, out: Path) -> int:
    log(f"===== {engine} {ct.server_version(engine)} | {len(units)} corridas =====")
    db_id = ct.register(engine)
    was_enabled = ct.monitoring_enabled(db_id)
    if not was_enabled:
        ct.cli("resume", db_id)
    log(f"{engine} | base registrada {db_id}, monitoreo encendido")
    unfinished = consecutive = 0
    try:
        if args.reiniciar_sentencias:
            # AP-05 va primero para entrar al top 10 con las sentencias recien
            # en cero: si tuviera que esperar la ventana de los indices despues
            # del reinicio, la carga base acumularia esos 30 min. Se espera antes
            first_name = next((name for _, _, kind, name, _ in units if kind == "inyeccion"), None)
            if first_name and "AP-05" in mf.AP_MANIFESTS[first_name].codes():
                wait_stats_window(engine, first_name)
            reset_statement_stats(engine)
        start_base_load(engine, args.tasa)
        wait_cycle(db_id)
        for position, (unit_id, _, kind, name, rep) in enumerate(units, 1):
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                unfinished += 1
                continue
            for attempt in range(1, ATTEMPTS + 1):
                started = time.monotonic()
                try:
                    start_base_load(engine, args.tasa)
                    if kind == "inyeccion":
                        unit = run_injection(engine, db_id, name, rep)
                    elif kind == "control":
                        unit = run_control(engine, db_id, rep, args.minutos_control, args.tasa)
                    else:
                        unit = run_contention(engine, db_id, args.reps)
                except Exception:
                    error = traceback.format_exc()
                    # si un contenedor del compose se recreo (p. ej. `docker
                    # compose run cli` levanta de nuevo una dependencia), el
                    # id guardado ya no sirve: se vuelve a buscar
                    rs.compose_container.cache_clear()
                    ct.measure_lib.compose_container.cache_clear()
                    with (out / "errores.log").open("a", encoding="utf-8") as handle:
                        handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] {unit_id} "
                                     f"(intento {attempt})\n{error}\n")
                    log(f"{engine} | {unit_id} FALLO (intento {attempt}/{ATTEMPTS}): "
                        f"{error.strip().splitlines()[-1][:160]}")
                    continue
                unit = {"id": unit_id, **unit, "segundos": round(time.monotonic() - started, 1)}
                with (out / "corridas.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(unit, ensure_ascii=False, default=str) + "\n")
                metricas.write_report(out)
                log(f"{engine} | [{position}/{len(units)}] {unit_id} | {summary_line(unit)}")
                consecutive = 0
                break
            else:
                unfinished += 1
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    log(f"{engine} | {consecutive} corridas seguidas fallaron: se deja el resto del motor para "
                        "--reanudar (ver errores.log)")
    finally:
        rs.stop_workload()
        stop_base_load(engine)
        if not was_enabled:
            ct.cli("pause", db_id)
    return unfinished


def summary_line(unit: dict) -> str:
    engine = unit["engine"]
    if unit["tipo"] == "ct":
        rows = [r for r in unit["ct"]["results"] if r.get("phase") == "positivo"]
        return f"{sum(bool(r.get('correct')) for r in rows)}/{len(rows)} inyecciones con el hallazgo correcto"
    manifest = mf.AP_MANIFESTS.get(unit.get("scenario"))
    injected = unit["tipo"] != "control"
    kinds = [mf.classify(finding, engine, manifest, injected)[0] for finding in unit["hallazgos"]]
    text = f"{kinds.count('no_declarado')} no declarados, {kinds.count('colateral')} colaterales, " \
           f"{kinds.count('linea_base')} de linea base"
    if manifest is None:
        return text
    wanted = [e for e in manifest.expected if engine in e.engines]
    found = sum(any(mf.matches(e.criterio, f) for f in unit["hallazgos"]) for e in wanted)
    return f"esperados {found}/{len(wanted)}, {text}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Banco de pruebas de QueryLens: 22 escenarios, repeticiones y controles.")
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    parser.add_argument("--reps", type=int, default=10, help="repeticiones por escenario (10)")
    parser.add_argument("--controles", type=int, default=10, help="corridas de control por motor (10)")
    parser.add_argument("--minutos-control", type=float, default=30, help="duracion de cada corrida de control (30)")
    parser.add_argument("--tasa", type=int, default=50, help="transacciones por segundo de la carga base (50)")
    parser.add_argument("--solo", help="solo estas partes, separadas por coma: nombres de escenario, ct, control")
    parser.add_argument("--smoke", action="store_true", help="1 repeticion y 1 control de 2 min")
    parser.add_argument("--etiqueta", default=None, help="nombre de la corrida (campana, calibracion...)")
    parser.add_argument("--reanudar", metavar="CORRIDA", help="seguir una corrida cortada (carpeta en resultados/)")
    parser.add_argument("--reiniciar-sentencias", action="store_true",
                        help="poner en cero las estadisticas de sentencias al empezar cada motor (ver reset_statement_stats)")
    args = parser.parse_args()

    preflight()
    if args.reanudar:
        out = RESULTS_DIR / args.reanudar
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        # los parametros son los de la corrida original, no los de esta linea de comandos
        args.engine, args.reps, args.controles = meta["engine"], meta["reps"], meta["controles"]
        args.minutos_control, args.tasa, args.solo = meta["minutos_control"], meta["tasa_carga_base"], meta["solo"]
        args.reiniciar_sentencias = meta.get("reiniciar_sentencias", False)
    else:
        if args.smoke:
            args.reps, args.controles, args.minutos_control = 1, 1, 2
        label = args.etiqueta or ("smoke" if args.smoke else "campana")
        out = RESULTS_DIR / f"banco-{label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        out.mkdir(parents=True)
        meta = {
            "run_id": out.name, "inicio": ql_now(), "fin": None, "commit": git_commit(),
            "engine": args.engine, "reps": args.reps, "controles": args.controles,
            "minutos_control": args.minutos_control, "tasa_carga_base": args.tasa, "solo": args.solo,
            "reiniciar_sentencias": args.reiniciar_sentencias,
            "observacion_s": [OBSERVE_MIN_S, OBSERVE_MAX_S],
            "versiones": {engine: ct.server_version(engine) for engine in mf.ENGINES},
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"Corrida {out.name}")

    unfinished = run_units(plan(args), args, out)

    if not unfinished:
        meta["fin"] = ql_now()
        (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    metrics = metricas.write_report(out)
    print("\n================ RESUMEN ================")
    for name in ("M1", "M2", "M3", "M4"):
        item = metrics["resumen"][name]
        print(f"{name}: {item['cumplen']}/{item['de']} combinaciones | {item['veredicto']}")
    item = metrics["resumen"]["M7"]
    print(f"M7: {item['cumplen']}/{item['de']} problemas | {item['veredicto']}")
    print(f"Reporte: {out / 'reporte.md'}")
    if unfinished:
        print(f"{unfinished} corridas sin terminar: python ql_sandbox/banco/banco.py --reanudar {out.name}")
        return 1
    return 0 if all(metrics["resumen"][name]["veredicto"] == "CUMPLE" for name in ("M1", "M2", "M3", "M4")) else 2


if __name__ == "__main__":
    sys.exit(main())
