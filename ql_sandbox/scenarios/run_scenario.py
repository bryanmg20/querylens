"""
QUERYLENS — escenarios de prueba de los antipatrones del detection_engine.

Cada escenario arma su propia tabla (ql_scn, 1.000.000 de filas, como dice el
SegundoInforme), inyecta la patologia con carga continua desde ql_sysbench,
corre telemetry_pipeline + detection_engine en ciclos y revisa en
public.hallazgos si aparecio el hallazgo esperado. Al terminar detiene la
carga y elimina la tabla.

REQUISITOS
  - Sandbox levantado (ql_sandbox: docker compose up -d) y el compose de la raiz.
  - venv creado en telemetry_pipeline/ y en detection_engine/.
  - Solo libreria estandar: se corre con cualquier python 3.10+.

USO (desde la raiz del repo)
  python ql_sandbox/scenarios/run_scenario.py --list
  python ql_sandbox/scenarios/run_scenario.py disk_spill
  python ql_sandbox/scenarios/run_scenario.py avoidable_full_scan --engine mysql
  python ql_sandbox/scenarios/run_scenario.py all --engine both
  python ql_sandbox/scenarios/run_scenario.py n_plus_one --engine both --docker

  --docker: el pipeline corre dentro del servicio pipeline del compose y los
  snapshots los procesa el servicio detection-engine (docker
  compose de la raiz levantado, con la imagen al dia). Sirve cuando los venv
  locales no se pueden usar, p. ej. si Windows bloquea la DLL de psycopg2.

  baseline_degradation tarda ~15 min: necesita 10 ventanas de 60 s de linea
  base antes de eliminar el indice.

NOTA
  En cada ciclo se toma el snapshot solo del motor del escenario, con un db_id
  propio ("querylens-db-01-postgres" / "-mysql", ver pipeline_one_engine.py):
  el pipeline todavia les pone el mismo db_id a los dos, y asi la historia y
  los hallazgos de cada motor no se mezclan. El engine procesa la cola
  completa (tambien los mensajes que ya hubiera al empezar).
"""

import argparse
import functools
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = ROOT / "telemetry_pipeline"
ENGINE_DIR = ROOT / "detection_engine"

PG_CONTAINER = "ql_postgres"
MYSQL_CONTAINER = "ql_mysql"
LOAD_CONTAINER = "ql_sysbench"
WORKLOAD_SCRIPT = "/scripts/scenarios/workload.sh"
STOP_FILE = "/tmp/ql_scn_stop"

# db_id con el que pipeline_one_engine.py encola el snapshot de cada motor
DB_ID_BY_ENGINE = {"postgres": "querylens-db-01-postgres", "mysql": "querylens-db-01-mysql"}
PIPELINE_ONE_ENGINE = Path(__file__).resolve().parent / "pipeline_one_engine.py"

# --docker: pipeline y detection_engine en sus contenedores, no en los venv
USE_DOCKER = False
# tope de espera a que el detection_engine del contenedor vacie la cola
DOCKER_DRAIN_TIMEOUT_SECONDS = 120


# ------------------------------------------------------------------
# SQL de preparacion por motor
# ------------------------------------------------------------------

SETUP_SQL = {
    "postgres": """
        DROP TABLE IF EXISTS ql_scn;
        CREATE TABLE ql_scn (
            id SERIAL PRIMARY KEY,
            k INT NOT NULL,
            c INT NOT NULL,
            pad CHAR(60) NOT NULL DEFAULT 'relleno'
        );
        INSERT INTO ql_scn (k, c)
        SELECT floor(random() * 100000)::int, floor(random() * 100000)::int
        FROM generate_series(1, 1000000);
        CREATE INDEX ql_scn_k ON ql_scn (k);
        ANALYZE ql_scn;
    """,
    "mysql": """
        DROP TABLE IF EXISTS ql_scn;
        CREATE TABLE ql_scn (
            id INT AUTO_INCREMENT PRIMARY KEY,
            k INT NOT NULL,
            c INT NOT NULL,
            pad CHAR(60) NOT NULL DEFAULT 'relleno'
        );
        INSERT INTO ql_scn (k, c)
        WITH d AS (
            SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
            UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
            UNION ALL SELECT 8 UNION ALL SELECT 9
        )
        SELECT FLOOR(RAND() * 100000), FLOOR(RAND() * 100000)
        FROM d d1, d d2, d d3, d d4, d d5, d d6;
        CREATE INDEX ql_scn_k ON ql_scn (k);
        ANALYZE TABLE ql_scn;
    """,
}

TEARDOWN_SQL = (
    "DROP TABLE IF EXISTS ql_scn; DROP TABLE IF EXISTS ql_scn_corr; "
    "DROP TABLE IF EXISTS ql_n1_orders; DROP TABLE IF EXISTS ql_n1_customers;"
)

# indices extra del escenario de AP-05 (mismo DDL en ambos motores)
UNUSED_INDEX_SQL = """
    CREATE INDEX ql_scn_pad ON ql_scn (pad);
    CREATE INDEX ql_scn_k_dup ON ql_scn (k);
    CREATE INDEX ql_scn_c ON ql_scn (c);
    CREATE INDEX ql_scn_c_k ON ql_scn (c, k);
"""

# tabla del escenario de AP-08 (informe): 100.000 filas con a = b = c y 10
# valores, sin estadisticas extendidas ni histogramas
CORRELATED_SQL = {
    "postgres": """
        DROP TABLE IF EXISTS ql_scn_corr;
        CREATE TABLE ql_scn_corr (
            id SERIAL PRIMARY KEY,
            a INT NOT NULL,
            b INT NOT NULL,
            c INT NOT NULL
        );
        INSERT INTO ql_scn_corr (a, b, c)
        SELECT g % 10, g % 10, g % 10 FROM generate_series(1, 100000) g;
        ANALYZE ql_scn_corr;
    """,
    "mysql": """
        DROP TABLE IF EXISTS ql_scn_corr;
        CREATE TABLE ql_scn_corr (
            id INT AUTO_INCREMENT PRIMARY KEY,
            a INT NOT NULL,
            b INT NOT NULL,
            c INT NOT NULL
        );
        INSERT INTO ql_scn_corr (a, b, c)
        WITH d AS (
            SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
            UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
            UNION ALL SELECT 8 UNION ALL SELECT 9
        )
        SELECT d1.n, d1.n, d1.n
        FROM d d1, d d2, d d3, d d4, d d5;
        ANALYZE TABLE ql_scn_corr;
    """,
}

# tablas del escenario de AP-06: 500 clientes (region = id % 10, 50 por region)
# y 2.500 pedidos (customer_id = 1 + id % 500, 5 por cliente) unidos por una FK.
# workload.sh calcula los ids que devuelve cada consulta padre con estas formulas.
N_PLUS_ONE_SQL = {
    "postgres": """
        DROP TABLE IF EXISTS ql_n1_orders;
        DROP TABLE IF EXISTS ql_n1_customers;
        CREATE TABLE ql_n1_customers (
            id INT PRIMARY KEY,
            region INT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE ql_n1_orders (
            id INT PRIMARY KEY,
            customer_id INT NOT NULL REFERENCES ql_n1_customers (id),
            total NUMERIC(10, 2) NOT NULL
        );
        CREATE INDEX ql_n1_orders_customer_id ON ql_n1_orders (customer_id);
        INSERT INTO ql_n1_customers SELECT g, g % 10, 'cliente ' || g FROM generate_series(1, 500) g;
        INSERT INTO ql_n1_orders SELECT g, 1 + g % 500, g % 1000 FROM generate_series(1, 2500) g;
        ANALYZE ql_n1_customers;
        ANALYZE ql_n1_orders;
    """,
    "mysql": """
        DROP TABLE IF EXISTS ql_n1_orders;
        DROP TABLE IF EXISTS ql_n1_customers;
        CREATE TABLE ql_n1_customers (
            id INT PRIMARY KEY,
            region INT NOT NULL,
            name VARCHAR(40) NOT NULL
        );
        -- InnoDB crea el indice de customer_id junto con la FK
        CREATE TABLE ql_n1_orders (
            id INT PRIMARY KEY,
            customer_id INT NOT NULL,
            total DECIMAL(10, 2) NOT NULL,
            CONSTRAINT ql_n1_orders_customer_fk FOREIGN KEY (customer_id) REFERENCES ql_n1_customers (id)
        );
        SET SESSION cte_max_recursion_depth = 5000;
        INSERT INTO ql_n1_customers
        WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 500)
        SELECT n, n % 10, CONCAT('cliente ', n) FROM g;
        INSERT INTO ql_n1_orders
        WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 2500)
        SELECT n, 1 + n % 500, n % 1000 FROM g;
        ANALYZE TABLE ql_n1_customers, ql_n1_orders;
    """,
}

DROP_K_INDEX_SQL = {
    "postgres": "DROP INDEX ql_scn_k;",
    "mysql": "DROP INDEX ql_scn_k ON ql_scn;",
}


# ------------------------------------------------------------------
# Definicion de escenarios
# ------------------------------------------------------------------

@dataclass
class Expectation:
    descripcion: str
    match: Callable[[dict], bool]
    # por que podria fallar, si ya se sabe de antemano
    nota: str | None = None
    # True: el escenario pasa si NINGUN hallazgo coincide en todos los ciclos
    # (corridas de control); el hallazgo guardado es el que lo hizo fallar
    absent: bool = False


@dataclass
class Scenario:
    name: str
    descripcion: str
    expectations: list[Expectation]
    workload: str | None = None  # tipo de carga de workload.sh
    workers: int = 0
    warmup_seconds: int = 20
    interval_seconds: int = 30
    max_cycles: int = 4
    extra_setup: Callable[[str], str] | None = None
    # (ciclo despues del cual se inyecta, SQL por motor)
    inject_after_cycle: int | None = None
    inject_sql: dict[str, str] = field(default_factory=dict)
    # configuracion global del servidor (como root/superusuario) que el escenario
    # necesita, y como dejarla al terminar
    server_setup: dict[str, str] = field(default_factory=dict)
    server_teardown: dict[str, str] = field(default_factory=dict)
    # Toma un snapshot antes de arrancar la carga y no cuenta sus hallazgos: la
    # primera ventana del escenario empieza despues de la preparacion. Sin el,
    # la primera ventana se mide desde el ultimo snapshot del escenario anterior
    # e incluye lo que su carga ejecuto hasta detenerse.
    cut_snapshot: bool = False


def _evidencia_text(finding: dict) -> str:
    return json.dumps(finding.get("evidencia") or {}, default=str)


def _n_plus_one(table: str, direccion: str) -> Callable[[dict], bool]:
    def match(finding: dict) -> bool:
        evidencia = finding.get("evidencia") or {}
        return (
            finding["antipatron"] == "n_plus_one"
            and finding.get("table_name") == table
            and evidencia.get("direccion") == direccion
        )
    return match


def _unused(index_name: str, subtipo: str) -> Callable[[dict], bool]:
    def match(finding: dict) -> bool:
        evidencia = finding.get("evidencia") or {}
        return (
            finding["antipatron"] == "unused_index"
            and evidencia.get("index_name") == index_name
            and evidencia.get("subtipo") == subtipo
            # todo hallazgo tiene que traer la query asociada
            and finding.get("query_id") is not None
        )
    return match


SCENARIOS = {
    scenario.name: scenario
    for scenario in [
        Scenario(
            name="disk_spill",
            descripcion="AP-07: GROUP BY de 100.000 grupos con memoria de trabajo reducida.",
            workload="disk_spill",
            workers=2,
            # MySQL 8.0 desborda las tablas temporales a archivos mmap, que
            # SUM_CREATED_TMP_DISK_TABLES no cuenta; sin mmap van a InnoDB en disco
            server_setup={"mysql": "SET GLOBAL temptable_use_mmap = OFF;"},
            server_teardown={"mysql": "SET GLOBAL temptable_use_mmap = ON;"},
            expectations=[
                Expectation(
                    "disk_spill sobre una consulta de ql_scn",
                    lambda f: f["antipatron"] == "disk_spill" and "ql_scn" in _evidencia_text(f),
                ),
            ],
        ),
        Scenario(
            name="avoidable_full_scan",
            descripcion="AP-02 y AP-04: WHERE c = ? sobre 1.000.000 de filas, sin indice en c.",
            workload="full_scan",
            workers=4,
            expectations=[
                Expectation(
                    "avoidable_full_scan sobre ql_scn",
                    lambda f: f["antipatron"] == "avoidable_full_scan" and f.get("table_name") == "ql_scn",
                    nota="necesita EXPLAIN: la consulta debe estar en top_impact y ejecutandose al tomar el snapshot",
                ),
                Expectation(
                    "missing_index sobre ql_scn.c",
                    lambda f: (
                        f["antipatron"] == "missing_index"
                        and f.get("table_name") == "ql_scn"
                        and (f.get("evidencia") or {}).get("equality_columns") == ["c"]
                    ),
                    nota="mismas condiciones de escaneo completo que avoidable_full_scan",
                ),
            ],
        ),
        Scenario(
            name="non_sargable_predicate",
            descripcion="AP-03: WHERE k + 0 = ? con indice en k (escenario del informe).",
            workload="non_sargable",
            workers=4,
            expectations=[
                Expectation(
                    "non_sargable_predicate arithmetic_on_column sobre ql_scn.k",
                    lambda f: (
                        f["antipatron"] == "non_sargable_predicate"
                        and f.get("table_name") == "ql_scn"
                        and (f.get("evidencia") or {}).get("column") == "k"
                        and (f.get("evidencia") or {}).get("subtipo") == "arithmetic_on_column"
                    ),
                    nota="necesita EXPLAIN: la consulta debe estar en top_impact y ejecutandose al tomar el snapshot",
                ),
            ],
        ),
        Scenario(
            name="unused_index",
            descripcion=(
                "AP-05: indice sobre pad que nadie consulta, duplicado exacto de k "
                "(escenario del informe) e indice c cubierto por (c, k), con una "
                "consulta WHERE k + 0 = ? que filtra la tabla sin usar ningun indice."
            ),
            extra_setup=lambda engine: UNUSED_INDEX_SQL,
            # AP-05 asocia cada hallazgo a una query de top_impact_queries que corrio
            # en el snapshot y filtra la tabla: sin carga no habria a quien asociarlo
            workload="non_sargable",
            workers=4,
            expectations=[
                Expectation(
                    "redundante: ql_scn_c es prefijo de ql_scn_c_k",
                    _unused("ql_scn_c", "redundante"),
                ),
                Expectation(
                    "redundante: ql_scn_k_dup duplica a ql_scn_k (informe)",
                    lambda f: _unused("ql_scn_k_dup", "redundante")(f) or _unused("ql_scn_k", "redundante")(f),
                ),
                Expectation(
                    "no_usado: ql_scn_pad",
                    _unused("ql_scn_pad", "no_usado"),
                    nota=(
                        "depende de DEFAULT_MIN_STATS_WINDOW en unused_index.py: el motor tiene "
                        "que llevar al menos ese tiempo encendido"
                    ),
                ),
            ],
        ),
        Scenario(
            name="cardinality_misestimate",
            descripcion=(
                "AP-08: WHERE a = ? AND b = ? AND c = ? sobre 100.000 filas con a = b = c "
                "(escenario del informe: ~100 estimadas frente a ~10.000 reales)."
            ),
            workload="correlated",
            workers=4,
            extra_setup=lambda engine: CORRELATED_SQL[engine],
            expectations=[
                Expectation(
                    "cardinality_misestimate sobre ql_scn_corr",
                    lambda f: f["antipatron"] == "cardinality_misestimate" and f.get("table_name") == "ql_scn_corr",
                    nota=(
                        "necesita EXPLAIN (consulta en top_impact y ejecutandose al tomar el snapshot) "
                        "y un snapshot anterior: el primer ciclo solo guarda los contadores"
                    ),
                ),
            ],
        ),
        Scenario(
            name="n_plus_one",
            descripcion=(
                "AP-06: lee 50 clientes y consulta los pedidos de cada uno (uno a muchos), y lee "
                "50 pedidos y consulta el cliente de cada uno (muchos a uno), en bucle."
            ),
            workload="n_plus_one",
            workers=2,
            extra_setup=lambda engine: N_PLUS_ONE_SQL[engine],
            cut_snapshot=True,
            expectations=[
                Expectation(
                    "n_plus_one uno_a_muchos: ql_n1_orders WHERE customer_id = ?",
                    _n_plus_one("ql_n1_orders", "uno_a_muchos"),
                    nota="padre e hija tienen que medirse contra el mismo snapshot anterior (o ser nuevas las dos)",
                ),
                Expectation(
                    "n_plus_one muchos_a_uno: ql_n1_customers WHERE id = ?",
                    _n_plus_one("ql_n1_customers", "muchos_a_uno"),
                    nota="padre e hija tienen que medirse contra el mismo snapshot anterior (o ser nuevas las dos)",
                ),
            ],
        ),
        Scenario(
            name="n_plus_one_control",
            descripcion=(
                "AP-06 (control): los mismos datos que n_plus_one, traidos con JOIN e IN (...). "
                "No debe aparecer ningun n_plus_one."
            ),
            workload="n_plus_one_control",
            workers=2,
            extra_setup=lambda engine: N_PLUS_ONE_SQL[engine],
            cut_snapshot=True,
            expectations=[
                Expectation(
                    "ningun n_plus_one sobre las tablas ql_n1_*",
                    lambda f: f["antipatron"] == "n_plus_one" and (f.get("table_name") or "").startswith("ql_n1_"),
                    absent=True,
                ),
            ],
        ),
        Scenario(
            name="baseline_degradation",
            descripcion=(
                "AP-01: 10 min de linea base con SELECT ... WHERE k = ?, luego se elimina "
                "el indice ql_scn_k (escenario del informe)."
            ),
            workload="point_lookup",
            workers=2,
            warmup_seconds=10,
            interval_seconds=60,
            # ciclo 1 crea la fila, ciclos 2-11 juntan las 10 ventanas; desde el 12 ya puede opinar
            inject_after_cycle=11,
            inject_sql=DROP_K_INDEX_SQL,
            max_cycles=14,
            expectations=[
                Expectation(
                    "baseline_degradation sobre SELECT pad FROM ql_scn WHERE k = ?",
                    lambda f: (
                        f["antipatron"] == "baseline_degradation"
                        and "ql_scn" in ((f.get("evidencia") or {}).get("query_text") or "")
                    ),
                    nota="el informe espera el hallazgo en <= 2 ventanas despues de eliminar el indice",
                ),
            ],
        ),
    ]
}


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
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        cmd, input=input_text, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Fallo: {' '.join(cmd[:6])}...\n--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
        )
    return result.stdout


@functools.cache
def compose_container(service):
    # Id del contenedor de <service> en el compose de la raiz. Sin container_name
    # fijo el nombre depende del proyecto (docker compose -p, COMPOSE_PROJECT_NAME).
    out = run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), "ps", "-q", service]).strip()
    if not out:
        raise RuntimeError(f"el servicio {service} del compose de la raiz no esta corriendo")
    return out


def venv_python(project_dir: Path) -> str:
    for candidate in (project_dir / "venv" / "Scripts" / "python.exe", project_dir / "venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f"No se encontro el venv de {project_dir.name} (esperado en {project_dir / 'venv'})")


def exec_sql(engine: str, sql: str) -> None:
    if engine == "postgres":
        cmd = ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "ql_user", "-d", "ql_demo",
               "-v", "ON_ERROR_STOP=1", "-q"]
    else:
        cmd = ["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uapp_user", "-papp_pass", "ql_demo"]
    run(cmd, input_text=sql)


def exec_server_sql(engine: str, sql: str) -> None:
    if engine == "postgres":
        # ql_user es el POSTGRES_USER del contenedor, o sea superusuario
        exec_sql(engine, sql)
    else:
        run(["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uroot", "-pql_root"], input_text=sql)


def querylens_sql(sql: str) -> str:
    return run([
        "docker", "exec", "-i", compose_container("postgres"), "psql", "-U", "ql_user", "-d", "ql_demo", "-At",
    ], input_text=sql).strip()


def queue_length() -> int:
    return int(querylens_sql("SELECT queue_length FROM pgmq.metrics('analyze_job');") or 0)


def start_workload(engine: str, kind: str, workers: int, max_seconds: int) -> None:
    run(["docker", "exec", LOAD_CONTAINER, "rm", "-f", STOP_FILE])
    for _ in range(workers):
        run(["docker", "exec", "-d", LOAD_CONTAINER, "bash", WORKLOAD_SCRIPT, engine, kind, str(max_seconds)])


def workload_running() -> bool:
    # el contenedor no trae ps/pgrep: se revisa /proc a mano. El patron va como
    # [w]orkload para que no coincida con la linea de comandos de esta misma busqueda
    script = (
        'for p in /proc/[0-9]*; do '
        'grep -qa "scenarios/[w]orkload.sh" "$p/cmdline" 2>/dev/null && exit 0; '
        'done; exit 1'
    )
    result = subprocess.run(["docker", "exec", LOAD_CONTAINER, "bash", "-c", script], capture_output=True)
    return result.returncode == 0


def stop_workload(timeout_seconds: int = 30) -> None:
    run(["docker", "exec", LOAD_CONTAINER, "touch", STOP_FILE], check=False)
    # el pipe hacia psql/mysql puede tener cientos de consultas encoladas que se
    # seguirian ejecutando (con consultas de 500 ms, varios minutos) y caerian en
    # el siguiente escenario: se termina el cliente directamente. Lleva la marca
    # ql_scn_workload; el patron va como [q]l_scn para no coincidir con esta busqueda.
    kill_script = (
        'for p in /proc/[0-9]*; do '
        'grep -qa "[q]l_scn_workload" "$p/cmdline" 2>/dev/null && kill "${p#/proc/}" 2>/dev/null; '
        'done; true'
    )
    run(["docker", "exec", LOAD_CONTAINER, "bash", "-c", kill_script], check=False)
    deadline = time.monotonic() + timeout_seconds
    while workload_running() and time.monotonic() < deadline:
        time.sleep(1)
    if workload_running():
        log(f"Aviso: la carga sigue activa tras {timeout_seconds}s; el siguiente escenario podria verse afectado.")


def root_env() -> dict[str, str]:
    # telemetry_pipeline/config/connections.py busca el .env en telemetry_pipeline/
    # (quedo asi al mover el archivo a config/), no en la raiz: sin esto el
    # pipeline cae al puerto 5432, que es el Postgres del sandbox y no querylens_db.
    # Se pasan las variables del .env de la raiz; load_dotenv no pisa las que ya existen.
    env = dict(os.environ)
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def run_pipeline(engine: str) -> None:
    if USE_DOCKER:
        # el contenedor ya trae las variables de conexion (MONITOR_*, QUERYLENS_*)
        run(["docker", "cp", str(PIPELINE_ONE_ENGINE), f"{compose_container('pipeline')}:/tmp/pipeline_one_engine.py"])
        run(["docker", "exec", "-e", "QL_PIPELINE_DIR=/app", "-w", "/app", compose_container("pipeline"),
             "python", "/tmp/pipeline_one_engine.py", engine])
        return
    run([venv_python(PIPELINE_DIR), str(PIPELINE_ONE_ENGINE), engine], cwd=PIPELINE_DIR, env=root_env())


def drain_queue() -> int:
    if USE_DOCKER:
        # el detection_engine del contenedor consume la cola solo; un mensaje se
        # archiva despues de guardar sus hallazgos, asi que cola vacia = procesado
        pending = queue_length()
        deadline = time.monotonic() + DOCKER_DRAIN_TIMEOUT_SECONDS
        while queue_length() and time.monotonic() < deadline:
            time.sleep(1)
        if queue_length():
            log(f"Aviso: la cola no se vacio en {DOCKER_DRAIN_TIMEOUT_SECONDS}s; esta corriendo el servicio detection-engine?")
        return pending
    # --oneshot procesa un mensaje por llamada; se repite hasta vaciar la cola
    processed = 0
    pending = queue_length()
    for _ in range(pending + 2):
        if queue_length() == 0:
            break
        run([venv_python(ENGINE_DIR), "main.py", "--oneshot"], cwd=ENGINE_DIR)
        processed += 1
    return processed


def peek_snapshots() -> list[dict]:
    raw = querylens_sql("SELECT message::text FROM pgmq.q_analyze_job ORDER BY msg_id;")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def diagnose(snapshots: list[dict], engine: str) -> list[str]:
    # Que paso con las consultas de ql_scn en el snapshot de este motor: si no
    # aparece el hallazgo, esto dice en que etapa se quedo.
    lines = []
    for snapshot in snapshots:
        if snapshot.get("db_id") != DB_ID_BY_ENGINE[engine]:
            continue
        top = {c.get("query_id"): c for c in snapshot.get("top_impact_queries") or []}
        explained = {e.get("query_id") for e in snapshot.get("canonic_explains") or []}
        for statement in snapshot.get("statements") or []:
            text = (statement.get("query_text") or "").replace("`", "")
            if "ql_scn" not in text or not text.lstrip().upper().startswith("SELECT"):
                continue
            candidate = top.get(statement.get("query_id"))
            lines.append(
                f"    {text[:60]!r} | ejec={statement.get('execution_count')} "
                f"| top_impact={'si ' + str(candidate.get('selected_by')) if candidate else 'no'} "
                f"| activa={'si' if candidate and candidate.get('real_query_found') else 'no'} "
                f"| explain={'si' if statement.get('query_id') in explained else 'no'} "
                f"| disk_spill={statement.get('disk_spill_indicator')}"
            )
    return lines or ["    (ninguna consulta SELECT sobre ql_scn en el snapshot)"]


def findings_since(start: str, engine: str) -> list[dict]:
    raw = querylens_sql(
        "SELECT coalesce(json_agg(t), '[]') FROM ("
        " SELECT antipatron, severidad, query_id, table_name, evidencia"
        " FROM public.hallazgos"
        f" WHERE detectado_en >= '{start}'"
        f"   AND db_id = '{DB_ID_BY_ENGINE[engine]}'"
        ") t;"
    )
    return json.loads(raw or "[]")


# ------------------------------------------------------------------
# Ejecucion de un escenario
# ------------------------------------------------------------------

def run_scenario(scenario: Scenario, engine: str, keep: bool) -> list[tuple[Expectation, dict | None]]:
    log(f"===== {scenario.name} | {engine} =====")
    log(scenario.descripcion)

    pending = queue_length()
    if pending:
        log(f"La cola tiene {pending} mensaje(s) previos: se procesan antes de empezar.")
        drain_queue()

    results: dict[int, dict | None] = {}
    try:
        if engine in scenario.server_setup:
            log(f"Configuracion del servidor: {scenario.server_setup[engine].strip()}")
            exec_server_sql(engine, scenario.server_setup[engine])
        log("Preparando ql_scn (1.000.000 de filas)...")
        exec_sql(engine, SETUP_SQL[engine])
        if scenario.extra_setup:
            exec_sql(engine, scenario.extra_setup(engine))

        if scenario.cut_snapshot:
            log("Snapshot de corte (sus hallazgos no cuentan)...")
            run_pipeline(engine)
            drain_queue()

        start = querylens_sql("SELECT now();")

        if scenario.workload:
            total_seconds = scenario.warmup_seconds + scenario.max_cycles * (scenario.interval_seconds + 30) + 60
            log(f"Carga '{scenario.workload}' con {scenario.workers} worker(s)...")
            start_workload(engine, scenario.workload, scenario.workers, total_seconds)
            time.sleep(scenario.warmup_seconds)

        for cycle in range(1, scenario.max_cycles + 1):
            cycle_started = time.monotonic()

            run_pipeline(engine)
            snapshots = peek_snapshots()
            drain_queue()

            findings = findings_since(start, engine)
            for index, expectation in enumerate(scenario.expectations):
                if results.get(index) is None:
                    results[index] = next((f for f in findings if expectation.match(f)), None)
            met = sum(
                1 for index, expectation in enumerate(scenario.expectations)
                if (results.get(index) is None) == expectation.absent
            )

            log(f"Ciclo {cycle}/{scenario.max_cycles} | esperados cumplidos: {met}/{len(scenario.expectations)}")
            for line in diagnose(snapshots, engine):
                print(line, flush=True)

            # un control solo pasa si se corren todos los ciclos sin el hallazgo
            if met == len(scenario.expectations) and not any(e.absent for e in scenario.expectations):
                break

            if scenario.inject_after_cycle == cycle:
                log("Inyectando la falla: se elimina el indice ql_scn_k.")
                exec_sql(engine, scenario.inject_sql[engine])

            if cycle < scenario.max_cycles:
                elapsed = time.monotonic() - cycle_started
                time.sleep(max(0.0, scenario.interval_seconds - elapsed))
    finally:
        if scenario.workload:
            stop_workload()
        if engine in scenario.server_teardown:
            exec_server_sql(engine, scenario.server_teardown[engine])
            log(f"Configuracion restaurada: {scenario.server_teardown[engine].strip()}")
        if not keep:
            exec_sql(engine, TEARDOWN_SQL)
            log("Tablas del escenario eliminadas.")

    return [(expectation, results.get(index)) for index, expectation in enumerate(scenario.expectations)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Escenarios de prueba de los antipatrones de QueryLens.")
    parser.add_argument("scenario", nargs="?", help="nombre del escenario o 'all'")
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="postgres")
    parser.add_argument("--keep", action="store_true", help="no eliminar ql_scn al terminar")
    parser.add_argument("--list", action="store_true", help="listar escenarios")
    parser.add_argument(
        "--docker", action="store_true",
        help="correr pipeline y detection_engine en sus contenedores en vez de los venv locales",
    )
    args = parser.parse_args()
    global USE_DOCKER
    USE_DOCKER = args.docker

    if args.list or not args.scenario:
        for scenario in SCENARIOS.values():
            print(f"  {scenario.name:<24} {scenario.descripcion}")
        return 0

    if args.scenario == "all":
        # el de linea base al final: es el mas largo
        names = [name for name in SCENARIOS if name != "baseline_degradation"] + ["baseline_degradation"]
    elif args.scenario in SCENARIOS:
        names = [args.scenario]
    else:
        parser.error(f"escenario desconocido: {args.scenario} (usa --list)")

    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]

    summary = []
    for engine in engines:
        for name in names:
            for expectation, finding in run_scenario(SCENARIOS[name], engine, args.keep):
                summary.append((name, engine, expectation, finding))

    print("\n================ RESUMEN ================")
    failed = 0
    for name, engine, expectation, finding in summary:
        if expectation.absent and finding is None:
            print(f"PASS  {name:<24} {engine:<8} {expectation.descripcion}")
        elif not expectation.absent and finding is not None:
            print(f"PASS  {name:<24} {engine:<8} {expectation.descripcion} (severidad={finding['severidad']})")
        else:
            failed += 1
            print(f"FAIL  {name:<24} {engine:<8} {expectation.descripcion}")
            if expectation.absent:
                print(f"      aparecio: query_id={finding.get('query_id')} table={finding.get('table_name')}")
            if expectation.nota:
                print(f"      nota: {expectation.nota}")
    print(f"\n{len(summary) - failed}/{len(summary)} esperados encontrados.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
