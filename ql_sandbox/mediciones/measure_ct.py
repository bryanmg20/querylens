"""
QUERYLENS — medicion de deteccion de contencion: CT-01 a CT-05.

Corre cada escenario de ql_sandbox/scripts/<motor>/simulate_ct0*.sh varias
veces contra el stack real: la base del sandbox registrada con el CLI
(`querylens add`), el servicio pipeline recolectando cada 10 s, el
detection-engine y public.hallazgos. Cada corrida guarda la verdad (si hubo
abrazo, los pid, la victima y el contador del motor antes y despues) y se
compara con lo que QueryLens vio: public.deadlock_events, public.hallazgos,
pipeline_health.issues y, por fase, el reporte del CLI (calidad_deadlocks).

USO (desde la raiz del repo; solo libreria estandar)
  python ql_sandbox/mediciones/measure_ct.py --engine postgres
  python ql_sandbox/mediciones/measure_ct.py --engine mysql --smoke          # 2 repeticiones
  python ql_sandbox/mediciones/measure_ct.py --engine mysql --cases ct01,rafaga
  python ql_sandbox/mediciones/measure_ct.py --engine postgres --pg-version 15   # profile pg15 arriba
  python ql_sandbox/mediciones/measure_ct.py --reevaluar ct-mysql-20261026-001500

Ver README.md (seccion CT): casos, criterios y lectura del reporte.
"""

import argparse
import json
import re
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta

import measure as measure_lib  # measure() de este modulo tapa el nombre
from measure import (
    RESULTS_DIR,
    ROOT,
    log,
    mysql_root,
    parse_ts,
    pct,
    querylens_sql,
    run,
    wilson,
)

SYSBENCH = "ql_sysbench"
PG_CONTAINER = "ql_postgres"
CONNECTION = {
    # nombre de la conexion, motor del auth-service, puerto publicado en el host
    "postgres": ("sandbox-pg", "postgresql", "5432"),
    "mysql": ("sandbox-mysql", "mysql", "3307"),
}
# Version del motor (--pg-version, --mysql-version): servicio de
# ql_sandbox/docker-compose.yml y puerto publicado. 17 y 8.0 son postgres y
# mysql; las demas se levantan con su profile (pg15, pg16, pg18, pg19, mysql84).
VERSIONS = {
    "postgres": {"17": ("postgres", "5432"), "15": ("postgres15", "5415"), "16": ("postgres16", "5416"),
                 "18": ("postgres18", "5418"), "19": ("postgres19", "5419")},
    "mysql": {"8.0": ("mysql", "3307"), "8.4": ("mysql84", "3384")},
}
DEFAULT_VERSION = {"postgres": "17", "mysql": "8.0"}
# host de la version en ql_network: los escenarios lo leen de PG_HOST / MYSQL_HOST
SCENARIO_ENV = {}

CASCADE = "long_transaction_cascade"
UNCLASSIFIED = "unclassified_deadlock"
PATTERN = {
    "inconsistent_lock_order_deadlock": "CT-01",
    "lock_mode_conversion_deadlock": "CT-02",
    "range_contention_deadlock": "CT-03",
    "lock_amplification_deadlock": "CT-04",
    CASCADE: "CT-05",
    UNCLASSIFIED: "sin clasificar",
}

# Metrica M4 (Tabla 3): CT-01 a CT-04 <= 15 s desde el abrazo; CT-05 <= 45 s
# desde que la raiz cumple 30 s. Hasta que el hallazgo esta guardado
# (detectado_en), que es cuando el usuario lo ve, no hasta el snapshot.
M4_DEADLOCK_S = 15
M4_CASCADE_S = 45
CASCADE_MIN_AGE_S = 30

# Espera tras cada abrazo: el evento suele estar en 1 o 2 ciclos (10 s). Entre
# dos abrazos siempre pasa al menos SPACING_S: INNODB STATUS guarda solo el
# ultimo, y dos en el mismo ciclo no son el caso que se mide.
EVENT_TIMEOUT_S = 40
SPACING_S = 14
# Antes de cerrar una fase: que el ultimo ciclo y el detector la alcancen.
PHASE_TAIL_S = 30
# Pausa del monitoreo: mas que LOOKBACK_S de DeadlocksStage (120 s).
PAUSE_S = 135
# Desborde del log de Postgres: LOG_WINDOW_BYTES es 2 MB por ciclo (10 s), es
# decir unos 205 KB/s. Se escriben ~400 KB/s durante FLOOD_S.
FLOOD_S = 30
FLOOD_LINES_PER_S = 400

CT01_PG = "postgres/simulate_ct01_deadlock_order_postgres.sh"
CT02_PG = "postgres/simulate_ct02_deadlock_conversion_postgres.sh"
CT05_PG = "postgres/simulate_ct05_long_transaction_postgres.sh"
CT01_MY = "mysql/simulate_ct01_deadlock_order_mysql.sh"
CT02_MY = "mysql/simulate_ct02_deadlock_conversion_mysql.sh"
CT03_MY = "mysql/simulate_ct03_deadlock_gap_mysql.sh"
CT04_MY = "mysql/simulate_ct04_deadlock_no_index_mysql.sh"
CT05_MY = "mysql/simulate_ct05_long_transaction_mysql.sh"

CT01 = "inconsistent_lock_order_deadlock"
CT02 = "lock_mode_conversion_deadlock"
CT03 = "range_contention_deadlock"
CT04 = "lock_amplification_deadlock"

CT05_ENV = {"HOLD_SECONDS": "60", "WAITERS": "5"}


def case(name, phase, kind, script, expect, env=None, captured=True, **extra):
    """expect: antipatron esperado, None si no debe haber hallazgo, "?" si es
    informativo (se reporta lo que paso, sin acierto ni fallo). captured: si el
    abrazo debe quedar en deadlock_events (None: informativo)."""
    return {"name": name, "phase": phase, "kind": kind, "script": script, "expect": expect,
            "env": env or {}, "captured": captured, **extra}


CASES = {
    "postgres": [
        case("ct01", "positivo", "deadlock", CT01_PG, CT01),
        case("ct02", "positivo", "deadlock", CT02_PG, CT02),
        # con los valores aparte, la fila de cada espera sale de su ctid: hace
        # falta que la otra sesion espere mas que deadlock_timeout (GAP)
        case("ct01_bind", "positivo", "deadlock", CT01_PG, CT01, {"MODE": "bind", "GAP": "1.5"},
             note="valores aparte ($1) y esperas a 1,5 s: la fila sale del ctid"),
        case("ct02_bind", "positivo", "deadlock", CT02_PG, CT02, {"MODE": "bind", "GAP": "1.5"},
             note="valores aparte ($1) y esperas a 1,5 s: la fila sale del ctid"),
        case("ct05", "positivo", "ct05", CT05_PG, CASCADE, CT05_ENV),
        case("ct05_raiz_corta", "control", "ct05", CT05_PG, None, {"HOLD_SECONDS": "25", "WAITERS": "5"},
             note="la raiz no llega a 30 s"),
        case("ct05_una_espera", "control", "ct05", CT05_PG, None, {"HOLD_SECONDS": "60", "WAITERS": "1"},
             note="una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo"),
        case("ct01_bind_rapido", "limite", "deadlock", CT01_PG, UNCLASSIFIED, {"MODE": "bind"},
             note="valores aparte ($1) y esperas a 0,5 s: solo el ctid de la victima"),
        case("ct02_bind_rapido", "limite", "deadlock", CT02_PG, UNCLASSIFIED, {"MODE": "bind"},
             note="valores aparte ($1) y esperas a 0,5 s: solo el ctid de la victima"),
        case("ct01_tres_bind", "limite", "deadlock", CT01_PG, CT01, {"MODE": "bind", "GAP": "1.5", "SESSIONS": "3"},
             note="ciclo de 3 con valores aparte y esperas a 1,5 s"),
        case("ct01_tres_bind_rapido", "limite", "deadlock", CT01_PG, UNCLASSIFIED, {"MODE": "bind", "SESSIONS": "3"},
             note="ciclo de 3 con valores aparte y esperas a 0,5 s"),
        case("sin_unica", "limite", "deadlock", CT01_PG, CT01, {"VARIANT": "sin_unica", "GAP": "1.5"},
             note="WHERE sin indice unico, esperas a 1,5 s: los valores no alcanzan, el ctid si"),
        case("sin_unica_rapido", "limite", "deadlock", CT01_PG, UNCLASSIFIED, {"VARIANT": "sin_unica"},
             note="WHERE sin indice unico, esperas a 0,5 s"),
        case("dos_tablas", "limite", "deadlock", CT01_PG, CT01, {"VARIANT": "dos_tablas", "MODE": "bind", "GAP": "1.5"},
             note="la fila id=1 de dos tablas: mismo ctid, otra relacion"),
        case("dos_schemas", "limite", "deadlock", CT01_PG, CT01, {"VARIANT": "dos_schemas", "MODE": "bind", "GAP": "1.5"},
             note="la misma tabla en dos schemas: mismo nombre y ctid, el schema sale de la sentencia"),
        case("particionada", "limite", "deadlock", CT01_PG, CT01, {"VARIANT": "particionada", "MODE": "bind", "GAP": "1.5"},
             note="filas en particiones distintas: mismo ctid"),
        case("ct01_tres", "limite", "deadlock", CT01_PG, CT01, {"SESSIONS": "3"}, note="ciclo de 3 transacciones"),
        case("rafaga", "limite", "burst", None, "?", parts=[(CT01_PG, {}, CT01), (CT02_PG, {}, CT02)],
             note="dos abrazos a la vez, en el mismo ciclo"),
        case("otra_base", "limite", "deadlock", CT01_PG, None, {"DBNAME": "ql_otra"}, captured=False,
             note="abrazo en otra base del mismo servidor: no es de la base monitoreada"),
        case("desborde_log", "limite", "flood", CT01_PG, "?", captured=None, issue="LOG_WINDOW_OVERRUN",
             note=f"~{FLOOD_LINES_PER_S} KB/s de log durante {FLOOD_S} s (> 2 MB por ciclo)"),
        case("terse", "limite", "deadlock", CT01_PG, "?", captured=None, issue="DEADLOCK_REPORTS_PARTIAL",
             setup="pg_terse", note="log_error_verbosity = terse"),
        case("pausa", "limite", "pause", CT01_PG, None, captured=False,
             note=f"monitoreo pausado {PAUSE_S} s (> 120 s de ventana)"),
        case("ct01", "carga", "deadlock", CT01_PG, CT01),
        case("ct02", "carga", "deadlock", CT02_PG, CT02),
        case("ct05", "carga", "ct05", CT05_PG, CASCADE, CT05_ENV),
    ],
    "mysql": [
        case("ct01", "positivo", "deadlock", CT01_MY, CT01),
        case("ct02", "positivo", "deadlock", CT02_MY, CT02),
        case("ct01_prepared", "positivo", "deadlock", CT01_MY, CT01, {"MODE": "prepared"},
             note="PREPARE/EXECUTE: la fila sale del registro del informe, no de la sentencia"),
        case("ct02_prepared", "positivo", "deadlock", CT02_MY, CT02, {"MODE": "prepared"},
             note="PREPARE/EXECUTE: la fila sale del registro del informe, no de la sentencia"),
        case("ct03", "positivo", "deadlock", CT03_MY, CT03),
        case("ct03_end", "positivo", "deadlock", CT03_MY, CT03, {"GAP": "end"}, note="hueco al final del indice"),
        case("ct04", "positivo", "deadlock", CT04_MY, CT04),
        case("ct05", "positivo", "ct05", CT05_MY, CASCADE, CT05_ENV),
        case("ct04_indice", "control", "deadlock", CT04_MY, None, {"VARIANT": "index"}, captured=None,
             no_deadlock=True, note="con indice sobre cliente no hay abrazo"),
        case("ct04_for_update", "control", "deadlock", CT04_MY, None, {"VARIANT": "for_update"},
             note="amplia un SELECT ... FOR UPDATE: abrazo sin patron (issue #48)"),
        case("ct05_raiz_corta", "control", "ct05", CT05_MY, None, {"HOLD_SECONDS": "25", "WAITERS": "5"},
             note="la raiz no llega a 30 s"),
        case("ct05_una_espera", "control", "ct05", CT05_MY, None, {"HOLD_SECONDS": "60", "WAITERS": "1"},
             note="una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo"),
        case("ct01_tres", "limite", "deadlock", CT01_MY, CT01, {"SESSIONS": "3"}, note="ciclo de 3 transacciones"),
        case("rafaga", "limite", "burst", None, "?", parts=[(CT01_MY, {}, CT01), (CT02_MY, {}, CT02),
                                                            (CT03_MY, {}, CT03)],
             note="tres abrazos a la vez: INNODB STATUS guarda solo el ultimo"),
        case("rafaga_sin_print_all", "limite", "burst", None, "?", setup="mysql_no_print_all",
             issue="DEADLOCK_REPORTS_PARTIAL",
             parts=[(CT01_MY, {}, CT01), (CT02_MY, {}, CT02), (CT03_MY, {}, CT03)],
             note="innodb_print_all_deadlocks = OFF: error_log no recibe los encabezados"),
        case("otro_schema", "limite", "deadlock", CT01_MY, "?", {"DBNAME": "ql_otra"}, captured=None,
             note="abrazo en otro schema: InnoDB no separa por schema"),
        case("pausa", "limite", "pause", CT01_MY, None, captured=False,
             note=f"monitoreo pausado {PAUSE_S} s (> 120 s de ventana)"),
        case("ct01", "carga", "deadlock", CT01_MY, CT01),
        case("ct02", "carga", "deadlock", CT02_MY, CT02),
        case("ct03", "carga", "deadlock", CT03_MY, CT03),
        case("ct04", "carga", "deadlock", CT04_MY, CT04),
        case("ct05", "carga", "ct05", CT05_MY, CASCADE, CT05_ENV),
    ],
}
PHASES = ["positivo", "control", "limite", "carga"]
PHASE_NAME = {"positivo": "Positivos", "control": "Controles (sin hallazgo esperado)",
              "limite": "Casos límite", "carga": "Positivos con carga de fondo (continuous_load.sh)"}


# ------------------------------------------------------------------ utilidades

def ql_now():
    return querylens_sql("SELECT now();")


def ql_json(sql):
    return json.loads(querylens_sql(sql) or "null")


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def pg_sandbox(sql, database="ql_demo"):
    return run(["docker", "exec", "-i", "-e", "PGOPTIONS=-c client_min_messages=error", PG_CONTAINER, "psql",
                "-U", "ql_user", "-d", database, "-At", "-v", "ON_ERROR_STOP=1"], input_text=sql).strip()


def engine_counter(engine):
    """Abrazos que conto el motor: lo mismo que lee EngineCountersStage."""
    if engine == "postgres":
        return int(pg_sandbox("SELECT deadlocks FROM pg_stat_database WHERE datname = 'ql_demo';"))
    out = mysql_root("SELECT `COUNT` FROM information_schema.INNODB_METRICS WHERE NAME = 'lock_deadlocks';")
    return int(out.strip().splitlines()[-1])


def use_version(engine, version):
    """Apunta la medicion a otra version del sandbox: el contenedor donde se
    lee el contador, el host de los escenarios y la conexion que se registra
    (otro puerto, otra base en QueryLens). Devuelve el sufijo del run_id."""
    global PG_CONTAINER
    service, port = VERSIONS[engine][version]
    if version == DEFAULT_VERSION[engine]:
        return ""
    name, auth_engine, _ = CONNECTION[engine]
    CONNECTION[engine] = (f"{name}-{version}", auth_engine, port)
    if engine == "postgres":
        PG_CONTAINER = f"ql_{service}"
        SCENARIO_ENV["PG_HOST"] = service
    else:
        measure_lib.MYSQL_CONTAINER = f"ql_{service}"
        SCENARIO_ENV["MYSQL_HOST"] = service
    return version.replace(".", "")


def server_version(engine):
    if engine == "postgres":
        return pg_sandbox("SHOW server_version;")
    return mysql_root("SELECT VERSION();").strip().splitlines()[-1]


def cli(*args, input_text=None):
    return run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "run", "--rm", "-T", "cli", *args],
               input_text=input_text)


def register(engine):
    """Registra la base del sandbox por el CLI, como lo haria un usuario. El
    auth-service reconoce la misma conexion por su fingerprint y devuelve el id
    que ya tenia, asi que repetirlo no crea otra fila."""
    name, auth_engine, port = CONNECTION[engine]
    answers = [name, auth_engine, "host.docker.internal", port, "querylens_monitor", "monitor_pass", "ql_demo", "", ""]
    out = cli("add", input_text="\n".join(answers) + "\n")
    match = re.search(r"\(id (db_[0-9a-f]+)\)", out)
    if not match:
        raise RuntimeError(f"el CLI no devolvio el id de la base:\n{out[-1500:]}")
    return match.group(1)


def monitoring_enabled(db_id):
    return querylens_sql(
        f"SELECT monitoring_enabled FROM registered_databases WHERE database_identifier = {lit(db_id)};") == "t"


def scenario(script, env):
    """Corre un script del sandbox y devuelve (codigo de salida, stdout+stderr)."""
    cmd = ["docker", "exec"]
    for key, value in {**SCENARIO_ENV, **env}.items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [SYSBENCH, "bash", f"/scripts/{script}"]
    started = time.monotonic()
    # sin check: un control puede terminar sin abrazo, y el script sale con 1
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode, result.stdout + result.stderr, round(time.monotonic() - started, 1)


def parse_output(output):
    sessions = {int(n): int(pid) for n, pid in re.findall(r"sesion (\d+) (?:pid|id)=(\d+)", output)}
    victim = re.search(r"victima: sesion (\d+)", output)
    root = re.search(r"raiz pid=(\d+)", output)
    victim_pid = sessions.get(int(victim.group(1))) if victim else None
    return {"pids": [sessions[n] for n in sorted(sessions)], "victim_pid": victim_pid,
            "root_pid": int(root.group(1)) if root else None, "deadlock": victim is not None}


def wait_for(predicate, timeout, step=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def event_for_victim(db_id, victim_pid, since):
    return querylens_sql(
        f"SELECT count(*) FROM public.deadlock_events WHERE db_id = {lit(db_id)}"
        f" AND report_key LIKE {lit('%|' + str(victim_pid))} AND detected_at >= {lit(since)}::timestamptz"
        " - interval '5 seconds';") != "0"


def cascade_for_root(db_id, root_pid, since):
    return querylens_sql(
        f"SELECT count(*) FROM public.hallazgos WHERE db_id = {lit(db_id)} AND antipatron = '{CASCADE}'"
        f" AND evidencia->'root'->>'process_id' = {lit(root_pid)} AND detectado_en >= {lit(since)};") != "0"


# ------------------------------------------------------------------ ajustes del servidor por caso

def setup_server(name, engine):
    if name == "pg_terse":
        pg_sandbox("ALTER SYSTEM SET log_error_verbosity = 'terse'; SELECT pg_reload_conf();")
        log(f"{engine} | servidor: log_error_verbosity = terse")
    elif name == "mysql_no_print_all":
        mysql_root("SET GLOBAL innodb_print_all_deadlocks = OFF;")
        log(f"{engine} | servidor: innodb_print_all_deadlocks = OFF")
    time.sleep(3)


def teardown_server(name, engine):
    if name == "pg_terse":
        pg_sandbox("ALTER SYSTEM RESET log_error_verbosity; SELECT pg_reload_conf();")
        value = pg_sandbox("SHOW log_error_verbosity;")
        log(f"{engine} | servidor restaurado: log_error_verbosity = {value}")
    elif name == "mysql_no_print_all":
        mysql_root("SET GLOBAL innodb_print_all_deadlocks = ON;")
        value = mysql_root("SELECT @@innodb_print_all_deadlocks;").strip().splitlines()[-1]
        log(f"{engine} | servidor restaurado: innodb_print_all_deadlocks = {value}")


def prepare_other_database(engine):
    """ql_otra: la base (PG) o el schema (MySQL) de los casos otra_base y otro_schema."""
    if engine == "postgres":
        if pg_sandbox("SELECT count(*) FROM pg_database WHERE datname = 'ql_otra';") == "0":
            pg_sandbox("CREATE DATABASE ql_otra;")
    else:
        mysql_root("CREATE DATABASE IF NOT EXISTS ql_otra; GRANT ALL PRIVILEGES ON ql_otra.* TO 'app_user'@'%';")


def drop_other_database(engine):
    if engine == "postgres":
        pg_sandbox("DROP DATABASE IF EXISTS ql_otra;", database="postgres")
    else:
        mysql_root("DROP DATABASE IF EXISTS ql_otra;")


def start_load(engine):
    host = [arg for key, value in SCENARIO_ENV.items() for arg in ("-e", f"{key}={value}")]
    run(["docker", "exec", "-d", *host, SYSBENCH, "bash", "/scripts/continuous_load.sh", engine, "0"])
    log(f"{engine} | carga de fondo iniciada (continuous_load.sh {engine})")


# ql_sysbench no trae pkill ni ps: se buscan los procesos en /proc. Mata el
# lazo y sus subshells, y devuelve cuantos quedan.
KILL_LOAD = r"""
pattern="continuous_load.sh $1 "
for attempt in 1 2 3; do
  left=0
  for dir in /proc/[0-9]*; do
    cmd=$(tr '\0' ' ' < "$dir/cmdline" 2>/dev/null) || continue
    case "$cmd" in "bash /scripts/$pattern"*) left=$((left + 1)); kill "${dir#/proc/}" 2>/dev/null ;; esac
  done
  [ "$left" -eq 0 ] && break
  sleep 1
done
echo "$left"
"""


def stop_load(engine):
    left = run(["docker", "exec", SYSBENCH, "bash", "-c", KILL_LOAD, "_", engine]).strip()
    if left != "0":
        raise RuntimeError(f"continuous_load.sh {engine} sigue corriendo en {SYSBENCH} ({left} procesos)")
    log(f"{engine} | carga de fondo detenida")


def flood_pg_log(seconds):
    """Escribe ~FLOOD_LINES_PER_S KB/s en el log del servidor (RAISE WARNING
    llega con log_min_messages = warning)."""
    sql = (f"DO $$ BEGIN FOR s IN 1..{seconds} LOOP FOR i IN 1..{FLOOD_LINES_PER_S} LOOP "
           "RAISE WARNING 'ql-medicion-desborde %', repeat('x', 1000); END LOOP; "
           "PERFORM pg_sleep(1); END LOOP; END $$;")
    pg_sandbox(sql)


# ------------------------------------------------------------------ una repeticion

def run_deadlock(engine, db_id, script, env):
    before = engine_counter(engine)
    started = ql_now()
    rc, output, seconds = scenario(script, env)
    done = time.monotonic()
    finished = ql_now()
    truth = {"started": started, "finished": finished, "rc": rc, "seconds": seconds, "output": output,
             "script": script, "env": env, **parse_output(output)}
    if truth["deadlock"]:
        wait_for(lambda: event_for_victim(db_id, truth["victim_pid"], started), EVENT_TIMEOUT_S)
        # el hallazgo se guarda despues que el evento, en el mismo job
        time.sleep(2)
    time.sleep(max(0.0, SPACING_S - (time.monotonic() - done)))
    truth["counter_before"], truth["counter_after"] = before, engine_counter(engine)
    return truth


def run_cascade(engine, db_id, script, env):
    before = engine_counter(engine)
    started = ql_now()
    rc, output, seconds = scenario(script, env)
    finished = ql_now()
    truth = {"started": started, "finished": finished, "rc": rc, "seconds": seconds, "output": output,
             "script": script, "env": env, **parse_output(output)}
    # el hallazgo sale mientras la raiz sigue abierta; tras el ROLLBACK, un ciclo mas
    time.sleep(12)
    truth["counter_before"], truth["counter_after"] = before, engine_counter(engine)
    return truth


def run_burst(engine, db_id, parts):
    before = engine_counter(engine)
    started = ql_now()
    results = [None] * len(parts)

    def worker(index, script, env):
        results[index] = scenario(script, env)

    threads = [threading.Thread(target=worker, args=(i, script, env)) for i, (script, env, _) in enumerate(parts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    finished = ql_now()
    members = []
    for (script, env, expect), (rc, output, seconds) in zip(parts, results, strict=True):
        members.append({"script": script, "env": env, "expect": expect, "rc": rc, "seconds": seconds,
                        "output": output, **parse_output(output)})
    time.sleep(EVENT_TIMEOUT_S)
    return {"started": started, "finished": finished, "members": members,
            "counter_before": before, "counter_after": engine_counter(engine)}


def run_pause(engine, db_id, script, env):
    before = engine_counter(engine)
    paused_at = ql_now()
    cli("pause", db_id)
    t0 = time.monotonic()
    # que el ciclo en curso termine antes del abrazo
    time.sleep(12)
    truth = run_deadlock_only(engine, script, env)
    time.sleep(max(0.0, PAUSE_S - (time.monotonic() - t0)))
    cli("resume", db_id)
    resumed_at = ql_now()
    time.sleep(EVENT_TIMEOUT_S)
    truth.update({"paused_at": paused_at, "resumed_at": resumed_at,
                  "counter_before": before, "counter_after": engine_counter(engine)})
    return truth


def run_deadlock_only(engine, script, env):
    started = ql_now()
    rc, output, seconds = scenario(script, env)
    finished = ql_now()
    return {"started": started, "finished": finished, "rc": rc, "seconds": seconds, "output": output,
            "script": script, "env": env, **parse_output(output)}


def run_flood(engine, db_id, script, env):
    before = engine_counter(engine)
    flood = threading.Thread(target=flood_pg_log, args=(FLOOD_S,))
    flood_started = ql_now()
    flood.start()
    # que ya haya un ciclo desbordado antes del abrazo
    time.sleep(12)
    truth = run_deadlock_only(engine, script, env)
    flood.join()
    time.sleep(EVENT_TIMEOUT_S)
    truth.update({"flood_started": flood_started, "flood_finished": ql_now(),
                  "counter_before": before, "counter_after": engine_counter(engine)})
    return truth


RUNNERS = {"deadlock": run_deadlock, "ct05": run_cascade, "flood": run_flood, "pause": run_pause}


# ------------------------------------------------------------------ una corrida

def measure(engine, args):
    version = args.pg_version if engine == "postgres" else args.mysql_version
    suffix = use_version(engine, version)
    run_id = f"ct-{engine}{suffix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{'-smoke' if args.smoke else ''}"
    out = RESULTS_DIR / run_id
    (out / "cli").mkdir(parents=True, exist_ok=True)
    reps = 2 if args.smoke else args.reps
    wanted = set(args.cases.split(",")) if args.cases else None
    phases = args.phases.split(",") if args.phases else PHASES
    cases = [c for c in CASES[engine] if c["phase"] in phases and (wanted is None or c["name"] in wanted)]
    version = server_version(engine)
    log(f"===== CT | {engine} {version} | {run_id} | {len(cases)} casos x {reps} repeticiones =====")

    db_id = register(engine)
    was_enabled = monitoring_enabled(db_id)
    if not was_enabled:
        cli("resume", db_id)
    log(f"{engine} | base registrada por el CLI: {db_id}")
    meta = {"run_id": run_id, "engine": engine, "version": version, "db_id": db_id, "reps": reps,
            "started": ql_now(),
            "phases": [], "spacing_s": SPACING_S, "event_timeout_s": EVENT_TIMEOUT_S}
    truth = {"meta": meta, "runs": []}

    def save():
        # en cada repeticion: si la corrida se corta, --reevaluar parte de aca
        (out / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")

    prepare_other_database(engine)
    # el primer ciclo tras registrar no tiene muestra anterior con que comparar
    time.sleep(25)
    try:
        for phase in phases:
            phase_cases = [c for c in cases if c["phase"] == phase]
            if not phase_cases:
                continue
            phase_info = {"phase": phase, "started": ql_now(), "counter_before": engine_counter(engine)}
            log(f"{engine} | --- fase {phase} ---")
            if phase == "carga":
                start_load(engine)
                time.sleep(20)
            try:
                for spec in phase_cases:
                    if spec.get("setup"):
                        setup_server(spec["setup"], engine)
                    try:
                        for rep in range(1, reps + 1):
                            if spec["kind"] == "burst":
                                result = run_burst(engine, db_id, spec["parts"])
                            else:
                                result = RUNNERS[spec["kind"]](engine, db_id, spec["script"], spec["env"])
                            result.update({"case": spec["name"], "phase": phase, "rep": rep})
                            truth["runs"].append(result)
                            save()
                            log(f"{engine} | {phase}/{spec['name']} {rep}/{reps} | {short(result)}")
                    finally:
                        if spec.get("setup"):
                            teardown_server(spec["setup"], engine)
            finally:
                if phase == "carga":
                    stop_load(engine)
            time.sleep(PHASE_TAIL_S)
            phase_info.update({"finished": ql_now(), "counter_after": engine_counter(engine)})
            phase_info["cli_report"] = cli_report(db_id, phase_info["started"], phase_info["finished"],
                                                  out / "cli" / f"{phase}.json")
            meta["phases"].append(phase_info)
            save()
    finally:
        drop_other_database(engine)
        if not was_enabled:
            cli("pause", db_id)
    meta["finished"] = ql_now()
    (out / "cli" / "health.txt").write_text(cli("health", db_id), encoding="utf-8")
    save()
    return evaluate(out)


def short(result):
    if "members" in result:
        return f"{sum(m['deadlock'] for m in result['members'])}/{len(result['members'])} abrazos"
    if result.get("root_pid"):
        return f"raiz {result['root_pid']}"
    return f"abrazo={'si' if result['deadlock'] else 'no'} victima={result.get('victim_pid')}"


def cli_report(db_id, since, until, path):
    out = cli("report", db_id, "--from", since, "--until", until, "--format", "json")
    match = re.search(r"Reporte: (\S+)", out)
    if not match:
        return {"error": out[-1000:]}
    data = json.loads((ROOT / "reports" / match.group(1)).read_text(encoding="utf-8"))
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data.get("calidad_deadlocks")


# ------------------------------------------------------------------ evaluacion

def fetch_events(db_id, since, until):
    return ql_json(
        "SELECT coalesce(json_agg(e ORDER BY e.detected_at), '[]') FROM ("
        " SELECT report_key, detected_at, source, complete, antipatron, recorded_at"
        f" FROM public.deadlock_events WHERE db_id = {lit(db_id)}"
        f" AND detected_at >= {lit(since)}::timestamptz AND detected_at < {lit(until)}::timestamptz) e;")


def fetch_findings(db_id, since, until):
    return ql_json(
        "SELECT coalesce(json_agg(h ORDER BY h.detectado_en), '[]') FROM ("
        " SELECT id, antipatron, severidad, table_name, evidencia, recomendacion, detectado_en"
        f" FROM public.hallazgos WHERE db_id = {lit(db_id)}"
        f" AND antipatron = ANY(ARRAY[{', '.join(lit(a) for a in PATTERN)}])"
        f" AND detectado_en >= {lit(since)}::timestamptz AND detectado_en < {lit(until)}::timestamptz) h;")


def fetch_issues(db_id, since):
    return ql_json(
        "SELECT coalesce(json_agg(i), '[]') FROM ("
        " SELECT code, section, first_seen, last_seen, resolved_at, occurrences"
        f" FROM pipeline_health.issues WHERE db_id = {lit(db_id)}"
        f" AND last_seen >= {lit(since)}::timestamptz) i;")


def utc(value):
    """detected_at de la evidencia: UTC sin zona."""
    moment = parse_ts(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def finding_pids(finding):
    return sorted(int(t["process_id"]) for t in finding["evidencia"].get("transactions", [])
                  if t.get("process_id") is not None)


def match_deadlock(occurrence, events, findings, used_events, used_findings):
    """Evento y hallazgo de un abrazo de la verdad: el evento por la victima
    (detected_at|pid), o, si llego sin detalle, el primero libre de la ventana;
    el hallazgo por el conjunto de pid del ciclo."""
    start = parse_ts(occurrence["started"]) - timedelta(seconds=5)
    end = parse_ts(occurrence["finished"]) + timedelta(seconds=5)
    event = next((e for e in events if e["report_key"] not in used_events
                  and e["report_key"].endswith(f"|{occurrence['victim_pid']}")
                  and start <= parse_ts(e["detected_at"]) <= end), None)
    if event is None:
        event = next((e for e in events if e["report_key"] not in used_events and not e["complete"]
                      and start <= parse_ts(e["detected_at"]) <= end), None)
    if event is not None:
        used_events.add(event["report_key"])
    pids = sorted(occurrence["pids"])
    finding = next((f for f in findings if f["id"] not in used_findings and f["antipatron"] != CASCADE
                    and finding_pids(f) == pids and parse_ts(f["detectado_en"]) >= start), None)
    if finding is not None:
        used_findings.add(finding["id"])
    return event, finding


# Tablas que tiene que nombrar el hallazgo de cada VARIANT del escenario CT-01
# de Postgres (en particionada, la particion o la tabla padre).
CT01_TABLES = {
    "sin_unica": ["ql_ct01_sin_unica"],
    "dos_tablas": ["ql_ct01_a", "ql_ct01_b"],
    "particionada": ["ql_ct01_part"],
}


def evidence_ok(expect, finding, occurrence):
    """Lo que el informe dice que el hallazgo tiene que traer, por patron."""
    if finding is None or finding["antipatron"] != expect:
        return None
    ev, env = finding["evidencia"], occurrence.get("env", {})
    tables = " ".join(ev.get("tables") or [])
    if expect == CT01:
        expected = CT01_TABLES.get(env.get("VARIANT", "cuentas"), ["ql_ct01_cuentas"])
        return ev.get("cycle_size") == int(env.get("SESSIONS", 2)) and all(table in tables for table in expected)
    if expect == CT02:
        return "ql_ct02_cuentas" in tables
    if expect == CT03:
        position = "end_of_index" if env.get("GAP") == "end" else "before_record"
        return any(g.get("position") == position for g in ev.get("gaps") or [])
    if expect == CT04:
        return (ev.get("related_antipattern") == "AP-04" and "CREATE INDEX" in finding["recomendacion"]
                and "cliente" in finding["recomendacion"])
    return True


def evaluate_deadlock(spec, occurrence, events, findings, used_events, used_findings):
    event, finding = match_deadlock(occurrence, events, findings, used_events, used_findings)
    expect = spec["expect"] if spec else occurrence.get("expect")
    delay = finding["evidencia"].get("detection_delay_s") if finding else None
    saved = None
    if finding and finding["evidencia"].get("detected_at"):
        saved = round((parse_ts(finding["detectado_en"]) - utc(finding["evidencia"]["detected_at"])).total_seconds(), 1)
    got = finding["antipatron"] if finding else None
    return {
        "deadlock": occurrence["deadlock"],
        "captured": event is not None,
        "complete": bool(event and event["complete"]),
        "event_antipatron": event["antipatron"] if event else None,
        "event_source": event["source"] if event else None,
        "finding": got,
        "correct": None if expect == "?" else got == expect,
        "delay_s": delay,
        "saved_delay_s": saved,
        "m4_ok": None if saved is None else saved <= M4_DEADLOCK_S,
        "evidence_ok": evidence_ok(expect, finding, occurrence),
        "severity": finding["severidad"] if finding else None,
    }


def evaluate_cascade(spec, run_, findings):
    root = run_.get("root_pid")
    start = parse_ts(run_["started"])
    hits = [f for f in findings if f["antipatron"] == CASCADE and f["evidencia"].get("root", {}).get("process_id") is not None
            and int(f["evidencia"]["root"]["process_id"]) == root and parse_ts(f["detectado_en"]) >= start]
    finding = hits[0] if hits else None
    ev = finding["evidencia"] if finding else {}
    age = ev.get("root", {}).get("transaction_age_s")
    delay = round(age - CASCADE_MIN_AGE_S, 1) if age is not None else None
    saved = None
    if finding and ev.get("root", {}).get("transaction_start_time"):
        ready = utc(ev["root"]["transaction_start_time"]) + timedelta(seconds=CASCADE_MIN_AGE_S)
        saved = round((parse_ts(finding["detectado_en"]) - ready).total_seconds(), 1)
    waiters = int(run_["env"].get("WAITERS", 5))
    return {
        "deadlock": False,
        "root_pid": root,
        "finding": CASCADE if finding else None,
        "findings_for_root": len(hits),
        "correct": (finding is not None) == (spec["expect"] == CASCADE),
        "delay_s": delay,
        "saved_delay_s": saved,
        "m4_ok": None if saved is None else saved <= M4_CASCADE_S,
        "evidence_ok": (ev.get("blocked_count") == waiters) if finding else None,
        "blocked_count": ev.get("blocked_count"),
        "severity": finding["severidad"] if finding else None,
    }


def evaluate(out):
    data = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    meta, runs = data["meta"], data["runs"]
    engine, db_id = meta["engine"], meta["db_id"]
    specs = {(c["phase"], c["name"]): c for c in CASES[engine]}
    until = (parse_ts(meta.get("finished") or ql_now()) + timedelta(minutes=5)).isoformat()
    events = fetch_events(db_id, meta["started"], until)
    findings = fetch_findings(db_id, meta["started"], until)
    issues = fetch_issues(db_id, meta["started"])
    used_events, used_findings = set(), set()

    results = []
    for run_ in runs:
        spec = specs[(run_["phase"], run_["case"])]
        base = {"case": spec["name"], "phase": spec["phase"], "rep": run_["rep"], "kind": spec["kind"],
                "engine_delta": run_["counter_after"] - run_["counter_before"]}
        if spec["kind"] == "burst":
            # los miembros corren en paralelo: la ventana es la de la rafaga
            window = {"started": run_["started"], "finished": run_["finished"]}
            members = [evaluate_deadlock(None, m | window, events, findings, used_events, used_findings)
                       | {"expect": m["expect"]} for m in run_["members"]]
            results.append(base | {"members": members})
        elif spec["kind"] == "ct05":
            results.append(base | evaluate_cascade(spec, run_, findings))
        else:
            results.append(base | evaluate_deadlock(spec, run_, events, findings, used_events, used_findings))
    # hallazgos de contencion que no son de ninguna corrida: falsos positivos
    matched_cascades = {f["id"] for f in findings if f["antipatron"] == CASCADE and any(
        r.get("root_pid") is not None and f["evidencia"].get("root", {}).get("process_id") is not None
        and int(f["evidencia"]["root"]["process_id"]) == r["root_pid"] for r in results)}
    stray = [{"id": f["id"], "antipatron": f["antipatron"], "detectado_en": f["detectado_en"],
              "pids": finding_pids(f) if f["antipatron"] != CASCADE else f["evidencia"].get("root", {}).get("process_id")}
             for f in findings if f["id"] not in used_findings and f["id"] not in matched_cascades]

    summary = summarize(engine, results, meta, issues, runs)
    summary["hallazgos_sin_corrida"] = stray
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results,
                                                  "issues": issues}, indent=1, ensure_ascii=False, default=str),
                                      encoding="utf-8")
    (out / "reporte.md").write_text(markdown(engine, meta, summary, results), encoding="utf-8")
    log(f"RESULTADO CT {engine}: -> {out / 'reporte.md'}")
    return meta["run_id"], summary


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"n": len(values), "mediana": round(statistics.median(values), 1), "max": round(max(values), 1),
            "min": round(min(values), 1)}


def summarize(engine, results, meta, issues, runs):
    by_case = {}
    for r in results:
        by_case.setdefault((r["phase"], r["case"]), []).append(r)
    specs = {(c["phase"], c["name"]): c for c in CASES[engine]}
    cases = []
    for key, rows in by_case.items():
        spec = specs[key]
        entry = {"phase": key[0], "case": key[1], "kind": spec["kind"], "expect": spec["expect"],
                 "note": spec.get("note"), "no_deadlock": spec.get("no_deadlock"), "reps": len(rows),
                 "engine_delta": sum(r["engine_delta"] for r in rows)}
        if spec["kind"] == "burst":
            members = [m for r in rows for m in r["members"]]
            entry.update({
                "abrazos": sum(m["deadlock"] for m in members),
                "capturados": sum(m["captured"] for m in members if m["deadlock"]),
                "con_detalle": sum(m["complete"] for m in members if m["deadlock"]),
                "con_hallazgo_correcto": sum(m["finding"] == m["expect"] for m in members if m["deadlock"]),
                "mal_clasificados": sum(m["finding"] not in (None, m["expect"]) for m in members if m["deadlock"]),
                "por_repeticion": [f"{sum(m['captured'] for m in r['members'])}/{sum(m['deadlock'] for m in r['members'])}"
                                   f" capt., {sum(m['complete'] for m in r['members'])} con detalle,"
                                   f" {sum(m['finding'] is not None for m in r['members'])} hallazgos" for r in rows],
                "latencia_s": stats([m["delay_s"] for m in members]),
            })
        elif spec["kind"] == "ct05":
            hit = sum(r["finding"] == CASCADE for r in rows)
            # en los controles lo correcto es no tener hallazgo
            correct = sum(r["correct"] for r in rows)
            entry.update({
                "con_hallazgo": hit, "correctos": correct,
                "tasa": round(correct / len(rows), 4), "ic95": wilson(correct, len(rows)),
                "latencia_s": stats([r["delay_s"] for r in rows]),
                "guardado_s": stats([r["saved_delay_s"] for r in rows]),
                "m4_ok": sum(bool(r["m4_ok"]) for r in rows),
                "evidencia_ok": sum(bool(r["evidence_ok"]) for r in rows),
                "evidencia_n": sum(r["evidence_ok"] is not None for r in rows),
                "duplicados": sum(max(0, r["findings_for_root"] - 1) for r in rows),
            })
        else:
            occurred = [r for r in rows if r["deadlock"]]
            correct = sum(bool(r["correct"]) for r in occurred) if spec["expect"] != "?" else None
            base_n = len(occurred) if not spec.get("no_deadlock") else len(rows)
            if spec.get("no_deadlock"):
                correct = sum(not r["deadlock"] and r["finding"] is None for r in rows)
            entry.update({
                "abrazos": len(occurred),
                "capturados": sum(r["captured"] for r in occurred),
                "con_detalle": sum(r["complete"] for r in occurred),
                "correctos": correct,
                "tasa": round(correct / base_n, 4) if correct is not None and base_n else None,
                "ic95": wilson(correct, base_n) if correct is not None else None,
                "hallazgos": {a: sum(r["finding"] == a for r in occurred)
                              for a in sorted({r["finding"] or "ninguno" for r in occurred} - {"ninguno"})},
                "sin_hallazgo": sum(r["finding"] is None for r in occurred),
                "patron_evento": {a or "sin_patron": sum(r["event_antipatron"] == a for r in occurred if r["captured"])
                                  for a in {r["event_antipatron"] for r in occurred if r["captured"]}},
                "latencia_s": stats([r["delay_s"] for r in occurred]),
                "guardado_s": stats([r["saved_delay_s"] for r in occurred]),
                "m4_ok": sum(bool(r["m4_ok"]) for r in occurred),
                "evidencia_ok": sum(bool(r["evidence_ok"]) for r in occurred),
                # sin patron esperado (informativos) no hay evidencia que revisar
                "evidencia_n": sum(r["evidence_ok"] is not None for r in occurred),
            })
        if spec.get("issue"):
            case_runs = [r for r in runs if (r["phase"], r["case"]) == key]
            since = parse_ts(case_runs[0]["started"])
            entry["issue"] = spec["issue"]
            entry["issue_visto"] = any(i["code"] == spec["issue"] and parse_ts(i["last_seen"]) >= since for i in issues)
        cases.append(entry)

    phases = []
    for info in meta["phases"]:
        phase_rows = [r for r in results if r["phase"] == info["phase"]]
        captured = sum(m["captured"] for r in phase_rows for m in (r.get("members") or [r]) if m.get("deadlock"))
        cq = info.get("cli_report") or {}
        phases.append({
            "phase": info["phase"],
            "motor_conto": info["counter_after"] - info["counter_before"],
            "capturados_verdad": captured,
            "cli_hubo": cq.get("hubo"), "cli_capturados": cq.get("capturados"), "cli_perdidos": cq.get("perdidos"),
            "cli_capturados_de_mas": cq.get("capturados_de_mas"), "cli_sin_detalle": cq.get("sin_detalle"),
            "cli_ciclos": cq.get("ciclos"), "cli_ciclos_con_desborde": cq.get("ciclos_con_desborde"),
            "cli_huecos_largos": cq.get("huecos_largos"), "cli_hueco_max_s": cq.get("hueco_max_s"),
            "cli_log_kb_s_max": cq.get("log_kb_s_max"), "cli_alcance": cq.get("alcance"),
            "cli_hubo_coincide": cq.get("hubo") == info["counter_after"] - info["counter_before"],
        })
    return {"casos": cases, "fases": phases}


# ------------------------------------------------------------------ reporte

def fmt_stats(s):
    return f"{s['mediana']} / {s['max']}" if s else "—"


def fmt_ratio(ok, n):
    return f"{ok}/{n}" if n else "—"


def fmt_rate(entry):
    if entry.get("tasa") is None:
        return "informativo"
    n = entry["abrazos"] if entry["kind"] == "deadlock" and not entry.get("no_deadlock") else entry["reps"]
    ic = entry.get("ic95")
    return f"**{entry['correctos']}/{n}** ({pct(entry['correctos'], n)})" + (
        f" · IC 95 % {ic[0] * 100:.0f}–{ic[1] * 100:.0f} %" if ic else "")


def markdown(engine, meta, summary, results):
    lines = [f"# Contención CT-01 a CT-05 — {engine} {meta.get('version', '')}".rstrip(), "",
             f"Corrida `{meta['run_id']}` · base `{meta['db_id']}` (registrada con `querylens add`) · "
             f"{meta['reps']} repeticiones por caso · inicio {meta['started']} · fin {meta.get('finished')}", "",
             "Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad "
             "de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la "
             "cola y el detector. M4 se cuenta con *Guardado*. Valores mediana / máximo en s.", ""]
    for phase in PHASES:
        entries = [e for e in summary["casos"] if e["phase"] == phase]
        if not entries:
            continue
        lines += [f"## {PHASE_NAME[phase]}", "",
                  "| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for e in entries:
            expect = PATTERN.get(e["expect"], "ninguno" if e["expect"] is None else "informativo")
            if e["kind"] == "burst":
                lines.append(f"| {e['case']} | {expect} | {e['abrazos']} ({e['engine_delta']}) | "
                             f"{e['capturados']} ({e['con_detalle']}) | {e['con_hallazgo_correcto']} con hallazgo "
                             f"correcto, {e['mal_clasificados']} mal | {fmt_stats(e['latencia_s'])} | — | — | — |")
            elif e["kind"] == "ct05":
                lines.append(f"| {e['case']} | {expect} | — ({e['engine_delta']}) | — | {fmt_rate(e)} | "
                             f"{fmt_stats(e['latencia_s'])} | {fmt_stats(e['guardado_s'])} | "
                             f"{fmt_ratio(e['m4_ok'], e['con_hallazgo'])} | {fmt_ratio(e['evidencia_ok'], e['evidencia_n'])} |")
            else:
                found = sum(e["hallazgos"].values())
                lines.append(f"| {e['case']} | {expect} | {e['abrazos']} ({e['engine_delta']}) | "
                             f"{e['capturados']} ({e['con_detalle']}) | {fmt_rate(e)} | {fmt_stats(e['latencia_s'])} | "
                             f"{fmt_stats(e['guardado_s'])} | {fmt_ratio(e['m4_ok'], found)} | "
                             f"{fmt_ratio(e['evidencia_ok'], e['evidencia_n'])} |")
        lines.append("")
        notes = []
        for e in entries:
            parts = []
            if e.get("note"):
                parts.append(e["note"])
            if e["kind"] == "deadlock" and e["hallazgos"]:
                parts.append("hallazgos: " + ", ".join(f"{PATTERN.get(a, a)} {n}" for a, n in e["hallazgos"].items()))
            if e["kind"] == "deadlock" and e["abrazos"]:
                parts.append(f"sin hallazgo: {e['sin_hallazgo']}")
                if e["patron_evento"]:
                    parts.append("evento: " + ", ".join(f"{PATTERN.get(a, a)} {n}" for a, n in e["patron_evento"].items()))
            if e["kind"] == "burst":
                parts.append("por repetición: " + "; ".join(e["por_repeticion"]))
            if e["kind"] == "ct05" and e["duplicados"]:
                parts.append(f"hallazgos repetidos por raíz: {e['duplicados']}")
            if e.get("issue"):
                parts.append(f"`{e['issue']}` en pipeline_health.issues: {'sí' if e['issue_visto'] else '**no**'}")
            if parts:
                notes.append(f"- `{e['case']}`: " + "; ".join(parts))
        lines += notes + [""]

    lines += ["## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)", "",
              "Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el "
              "reporte del CLI para la misma ventana.", "",
              "| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | "
              "Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in summary["fases"]:
        lines.append(f"| {p['phase']} | {p['motor_conto']} | {p['capturados_verdad']} | {p['cli_hubo']}"
                     f"{'' if p['cli_hubo_coincide'] else ' ⚠'} | {p['cli_capturados']} | {p['cli_perdidos']} | "
                     f"{p['cli_capturados_de_mas']} | {p['cli_sin_detalle']} | {p['cli_ciclos']} | "
                     f"{p['cli_ciclos_con_desborde']} | {p['cli_huecos_largos']} | {p['cli_hueco_max_s']} | "
                     f"{p['cli_log_kb_s_max']} |")
    lines.append("")
    if not all(p["cli_hubo_coincide"] for p in summary["fases"]):
        lines += ["⚠: el `hubo` del CLI no coincide con el contador leído directamente.", ""]

    misses = [r for r in results if r["kind"] == "deadlock" and r["deadlock"] and r["correct"] is False]
    misses += [r for r in results if r["kind"] == "ct05" and not r["correct"]]
    if misses:
        lines += ["## Corridas con resultado distinto del esperado", ""]
        for r in misses:
            if r["kind"] == "ct05":
                lines.append(f"- `{r['phase']}/{r['case']}` #{r['rep']}: raíz {r['root_pid']}, "
                             f"hallazgo {'sí' if r['finding'] else 'no'}")
            else:
                lines.append(f"- `{r['phase']}/{r['case']}` #{r['rep']}: capturado {'sí' if r['captured'] else 'no'}"
                             f" (detalle {'sí' if r['complete'] else 'no'}), hallazgo `{r['finding']}`, "
                             f"evento `{r['event_antipatron']}`")
        lines.append("")
    if summary.get("hallazgos_sin_corrida"):
        lines += ["## Hallazgos de contención que no son de ninguna corrida", ""]
        for f in summary["hallazgos_sin_corrida"]:
            lines.append(f"- {f['detectado_en']}: `{f['antipatron']}` (pid {f['pids']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Medicion de deteccion de contencion (CT-01 a CT-05).")
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="postgres")
    parser.add_argument("--reps", type=int, default=10, help="repeticiones por caso")
    parser.add_argument("--smoke", action="store_true", help="prueba corta: 2 repeticiones")
    parser.add_argument("--pg-version", choices=sorted(VERSIONS["postgres"]), default=DEFAULT_VERSION["postgres"],
                        help="version de Postgres del sandbox (otra que 17: levantar antes su profile)")
    parser.add_argument("--mysql-version", choices=sorted(VERSIONS["mysql"]), default=DEFAULT_VERSION["mysql"],
                        help="version de MySQL del sandbox (8.4: profile mysql84)")
    parser.add_argument("--cases", help="solo estos casos, separados por coma (p. ej. ct01,rafaga)")
    parser.add_argument("--phases", help=f"solo estas fases, separadas por coma ({','.join(PHASES)})")
    parser.add_argument("--reevaluar", metavar="CORRIDA",
                        help="recalcular el reporte de una corrida ya hecha (carpeta en resultados/)")
    args = parser.parse_args()
    if args.reevaluar:
        evaluate(RESULTS_DIR / args.reevaluar)
        return 0
    RESULTS_DIR.mkdir(exist_ok=True)
    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]
    for engine in engines:
        measure(engine, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
