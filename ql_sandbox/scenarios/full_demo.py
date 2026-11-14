"""
QUERYLENS — demo completa: llena un stack de QueryLens con hallazgos reales
de los 13 patrones posibles en el sandbox, en Postgres y en MySQL.

A diferencia de run_scenario.py (un escenario a la vez, con el pipeline y el
detector manejados desde aqui), esto usa el stack levantado tal como lo usa un
cliente: registra las bases por la API, enciende el monitoreo y deja que el
scheduler, el pipeline y el detector hagan su trabajo. Sirve para ver el front,
la API y el reporte de la CLI con datos de todo tipo.

REQUISITOS
  - Stack de la raiz levantado (por defecto en http://127.0.0.1:8080).
  - Sandbox encendido: docker start ql_postgres ql_mysql ql_sysbench
  - Solo libreria estandar: cualquier python 3.10+.

USO (desde la raiz del repo)
  python ql_sandbox/scenarios/full_demo.py todo
      registrar + cargar + abrazos + detener + limpiar (unos 7 minutos)

  python ql_sandbox/scenarios/full_demo.py registrar   registra las dos bases y enciende el monitoreo (sesion nueva)
  python ql_sandbox/scenarios/full_demo.py cargar      crea las tablas de prueba y corre la carga de antipatrones
  python ql_sandbox/scenarios/full_demo.py abrazos     abrazos mortales y cascadas (CT-01 a CT-05)
  python ql_sandbox/scenarios/full_demo.py detener     apaga el monitoreo de las dos bases
  python ql_sandbox/scenarios/full_demo.py limpiar     borra las tablas de prueba y ql_otra, y restaura MySQL

  --url URL        stack contra el que se registra (por defecto http://127.0.0.1:8080)
  --segundos N     duracion de la carga de antipatrones (por defecto 180)

QUE SE GENERA
  Carga (workload.sh en ql_sysbench, varios procesos por motor):
    full_scan     WHERE c = ? sin indice en c        AP-02 escaneo completo evitable, AP-04 indice ausente
    non_sargable  WHERE k + 0 = ?                    AP-03 predicado no sargable, AP-05 indice no utilizado o redundante
    correlated    a = ? AND b = ? AND c = ?          AP-08 error grave de estimacion de cardinalidad
    n_plus_one    un padre y una hija por fila       AP-06 patron N+1
    disk_spill    GROUP BY/ORDER BY con poca memoria AP-07 vertido a disco
  Abrazos (scripts/postgres y scripts/mysql, simulate_ct*):
    Postgres  CT-01, CT-02, CT-05
    MySQL     CT-01 a CT-05, y CT-01 otra vez en el schema ql_otra (la base del hallazgo)
  AP-01 degradacion respecto a la linea base no sale: necesita 10 minutos de
  linea base por consulta (ver run_scenario.py baseline_degradation).
  AP-05 indice no utilizado o redundante solo sale si el sandbox lleva 30
  minutos encendido: las estadisticas de uso de los indices empiezan de cero al
  arrancar el motor y antes de eso "no utilizado" no se evalua. El script avisa.

NOTAS
  - Registrar una base que ya estaba registrada (mismo host, puerto y base)
    reutiliza su registro: los hallazgos nuevos se suman a los que tenia.
  - detener va antes de limpiar: con el monitoreo encendido, las consultas de
    las tablas borradas siguen en las estadisticas del motor y el pipeline
    reporta EXPLAIN_FAILED en cada ciclo.
  - No se guardan tokens: cada accion vuelve a registrar la base para
    obtener uno (registrar de nuevo conserva el estado del monitoreo).
"""

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_scenario as rs  # noqa: E402

# Credenciales del rol de monitoreo del sandbox (ql_sandbox/init): no son secretas.
CONNECTIONS = {
    "postgres": {
        "connection_name": "sandbox-pg", "engine": "postgresql", "host": "host.docker.internal",
        "port": 5432, "username": "querylens_monitor", "password": "monitor_pass",
        "database_name": "ql_demo",
    },
    "mysql": {
        "connection_name": "sandbox-mysql", "engine": "mysql", "host": "host.docker.internal",
        "port": 3307, "username": "querylens_monitor", "password": "monitor_pass",
        "database_name": "",
    },
}

SANDBOX_CONTAINERS = ("ql_postgres", "ql_mysql", rs.LOAD_CONTAINER)

# Ventana minima de estadisticas de indices para AP-05 "no utilizado"
# (detection_engine, VENTANA_ESTADISTICAS_CORTA)
STATS_WINDOW_MIN = 30

# (tipo de carga de workload.sh, procesos) por motor
LOADS = [("full_scan", 2), ("non_sargable", 2), ("correlated", 2), ("n_plus_one", 1), ("disk_spill", 1)]

DEADLOCKS = {
    "postgres": [
        ("CT-01", "postgres/simulate_ct01_deadlock_order_postgres.sh", {}),
        ("CT-02", "postgres/simulate_ct02_deadlock_conversion_postgres.sh", {}),
        ("CT-05", "postgres/simulate_ct05_long_transaction_postgres.sh", {"HOLD_SECONDS": "60", "WAITERS": "5"}),
    ],
    "mysql": [
        ("CT-01", "mysql/simulate_ct01_deadlock_order_mysql.sh", {}),
        ("CT-01 en ql_otra", "mysql/simulate_ct01_deadlock_order_mysql.sh", {"DBNAME": "ql_otra"}),
        ("CT-02", "mysql/simulate_ct02_deadlock_conversion_mysql.sh", {}),
        ("CT-03", "mysql/simulate_ct03_deadlock_gap_mysql.sh", {}),
        ("CT-04", "mysql/simulate_ct04_deadlock_no_index_mysql.sh", {}),
        ("CT-05", "mysql/simulate_ct05_long_transaction_mysql.sh", {"HOLD_SECONDS": "60", "WAITERS": "5"}),
    ],
}

# Espera entre escenarios de abrazos: una ventana del pipeline, para que cada
# abrazo caiga en su propio ciclo.
CYCLE_S = 12


def _http(url: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"No se pudo llegar a {url} ({e.reason}): ¿esta levantado el stack?")


def _register(url: str, engine: str) -> dict:
    return _http(url, "POST", "/auth/databases", CONNECTIONS[engine])


def _set_monitoring(url: str, enabled: bool) -> None:
    for engine in CONNECTIONS:
        registered = _register(url, engine)
        body = {"enabled": enabled, "new_session": True} if enabled else {"enabled": False}
        state = _http(url, "PATCH", "/auth/databases/monitoring", body, registered["access_token"])
        print(f"{engine}: {registered['database_identifier']} monitoreo={state['monitoring_enabled']}")


def _check_sandbox() -> None:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.Name}} {{.State.Running}} {{.State.StartedAt}}", *SANDBOX_CONTAINERS],
        capture_output=True, text=True,
    )
    rows = [line.split() for line in result.stdout.splitlines()]
    stopped = [row[0].lstrip("/") for row in rows if row[1] == "false"]
    if result.returncode != 0 or stopped:
        sys.exit(f"Sandbox detenido o incompleto ({', '.join(stopped) or result.stderr.strip()}): "
                 f"docker start {' '.join(SANDBOX_CONTAINERS)}")
    # AP-05 "no utilizado" se evalua cuando las estadisticas de los indices
    # cubren STATS_WINDOW_MIN; al arrancar el motor empiezan de cero.
    for name, _, started in rows:
        if name.lstrip("/") not in ("ql_postgres", "ql_mysql"):
            continue
        started_at = datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00")
        minutes = (datetime.now(timezone.utc) - started_at).total_seconds() / 60
        if minutes < STATS_WINDOW_MIN:
            print(f"aviso: {name.lstrip('/')} lleva {minutes:.0f} min encendido; AP-05 indice no utilizado "
                  f"o redundante no sale hasta que lleve {STATS_WINDOW_MIN} min (faltan "
                  f"{STATS_WINDOW_MIN - minutes:.0f})")


def registrar(args) -> None:
    _set_monitoring(args.url, True)


def detener(args) -> None:
    _set_monitoring(args.url, False)


def cargar(args) -> None:
    _check_sandbox()
    for engine in CONNECTIONS:
        rs.exec_sql(engine, rs.SETUP_SQL[engine])
        rs.exec_sql(engine, rs.CORRELATED_SQL[engine])
        rs.exec_sql(engine, rs.N_PLUS_ONE_SQL[engine])
        print(f"{engine}: tablas de prueba listas")
    # MySQL: sin mmap el GROUP BY de disk_spill desborda a disco (AP-07)
    rs.exec_server_sql("mysql", "SET GLOBAL temptable_use_mmap = OFF;")
    rs.run(["docker", "exec", rs.LOAD_CONTAINER, "rm", "-f", rs.STOP_FILE])
    for engine in CONNECTIONS:
        for kind, workers in LOADS:
            for _ in range(workers):
                rs.run(["docker", "exec", "-d", rs.LOAD_CONTAINER, "bash", rs.WORKLOAD_SCRIPT, engine, kind,
                        str(args.segundos + 30)])
    print(f"carga en marcha {args.segundos} s")
    try:
        time.sleep(args.segundos)
    finally:
        rs.stop_workload()
        rs.exec_server_sql("mysql", "SET GLOBAL temptable_use_mmap = ON;")
    print("carga detenida")


def _deadlock_scenarios(engine: str, results: list) -> None:
    for name, script, env in DEADLOCKS[engine]:
        cmd = ["docker", "exec"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [rs.LOAD_CONTAINER, "bash", f"/scripts/{script}"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        results.append((engine, name, "ok" if result.returncode == 0 else f"fallo ({result.returncode})"))
        time.sleep(CYCLE_S)


def abrazos(args) -> None:
    _check_sandbox()
    rs.exec_server_sql("mysql", "CREATE DATABASE IF NOT EXISTS ql_otra; "
                                "GRANT ALL PRIVILEGES ON ql_otra.* TO 'app_user'@'%';")
    results: list = []
    threads = [threading.Thread(target=_deadlock_scenarios, args=(engine, results)) for engine in DEADLOCKS]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for engine, name, status in sorted(results):
        print(f"{engine}: {name} {status}")


def limpiar(args) -> None:
    _check_sandbox()
    rs.stop_workload()
    for engine in CONNECTIONS:
        rs.exec_sql(engine, rs.TEARDOWN_SQL)
    rs.exec_server_sql("mysql", "SET GLOBAL temptable_use_mmap = ON; DROP DATABASE IF EXISTS ql_otra;")
    print("sandbox limpio")


def todo(args) -> None:
    _check_sandbox()
    registrar(args)
    # que el scheduler tome las bases antes de empezar la carga
    time.sleep(25)
    cargar(args)
    abrazos(args)
    # el ultimo ciclo procesa lo que quedo de los abrazos
    time.sleep(30)
    detener(args)
    time.sleep(15)
    limpiar(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Llena un stack de QueryLens con hallazgos reales del sandbox.")
    parser.add_argument("accion", choices=["todo", "registrar", "cargar", "abrazos", "detener", "limpiar"])
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="stack de QueryLens (nginx)")
    parser.add_argument("--segundos", type=int, default=180, help="duracion de la carga de antipatrones")
    args = parser.parse_args()
    args.url = args.url.rstrip("/")
    globals()[args.accion](args)


if __name__ == "__main__":
    main()
