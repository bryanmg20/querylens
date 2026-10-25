"""Campana de AP-02 (escaneo completo evitable), AP-03 (predicado no sargable)
y AP-04 (indice ausente) por el flujo real de QueryLens.

Nada se simula: la carga sale de ql_sysbench, el runner del servicio pipeline
toma los snapshots de las bases registradas (cada EXTRACT_INTERVAL_S), el
servicio detection-engine los procesa con sus ACTIVE_RULES y los
hallazgos quedan en public.hallazgos con el db_id de la base registrada, que es
lo que muestra el frontend.

Cada escenario inyecta UNA consulta con un alias propio (SELECT id AS qNN ...)
y compara lo que aparecio en public.hallazgos para esa consulta con lo
esperado: ni menos (falso negativo) ni mas (falso positivo).

Tabla ql_cmp, 1.000.000 de filas:
  con indice:  k (int), r (int), s (varchar), t (timestamp), code (char(10))
  sin indice:  c, c2 (int), s2 (varchar), z (int, NULL en 20 filas), g (int, 4 valores)
Cada valor de k, c, c2, r, s, s2 y code aparece 10 veces; t tiene ~500 filas por dia.

REQUISITOS
  - Sandbox (ql_postgres, ql_mysql, ql_sysbench) y compose de la raiz levantados.
  - Las bases del sandbox registradas en QueryLens (registered_databases).
  - AP-02, AP-03 y AP-04 en ACTIVE_RULES del detection_engine (imagen al dia).

USO (desde la raiz del repo)
  python ql_sandbox/campana_escaneo_completo_ap02_ap03_ap04/campaign.py [--engine postgres|mysql|both]
         [--only q01,q07] [--keep]

Salida: ql_sandbox/campana_escaneo_completo_ap02_ap03_ap04/resultados_<fecha>.json y reporte_<fecha>.md
"""
import argparse
import functools
import json
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOAD_CONTAINER = "ql_sysbench"
STOP_FILE = "/tmp/ql_cmp_stop"
ROWS = 1_000_000
WORKERS = 4
WARMUP_SECONDS = 5
OBSERVE_SECONDS = 28
LINES_PER_FILE = 2000
RULES = ("avoidable_full_scan", "non_sargable_predicate", "missing_index")
ENGINE_NAMES = {"postgres": ("postgresql", "postgres"), "mysql": ("mysql",)}

SETUP = {
    "postgres": f"""
DROP TABLE IF EXISTS ql_cmp; DROP TABLE IF EXISTS ql_cmp_dim;
CREATE TABLE ql_cmp (
  id bigint PRIMARY KEY, k int, c int, c2 int, r int, s varchar(20), s2 varchar(20),
  t timestamp, code char(10), z int, g int, pad char(60) DEFAULT 'relleno');
INSERT INTO ql_cmp (id, k, c, c2, r, s, s2, t, code, z, g)
SELECT n, (n * 7919) % 100000, (n * 104729) % 100000, (n * 1299709) % 100000,
  (n * 15485863) % 100000, 'v' || ((n * 7) % 100000), 'w' || ((n * 11) % 100000),
  timestamp '2020-01-01' + ((n * 31) % 2880000) * interval '1 minute',
  lpad(((n * 17) % 100000)::text, 10, '0'), CASE WHEN n % 50000 = 0 THEN NULL ELSE n END, n % 4
FROM generate_series(1::bigint, {ROWS}) AS n;
CREATE INDEX ql_cmp_k ON ql_cmp (k); CREATE INDEX ql_cmp_r ON ql_cmp (r);
CREATE INDEX ql_cmp_s ON ql_cmp (s); CREATE INDEX ql_cmp_t ON ql_cmp (t);
CREATE INDEX ql_cmp_code ON ql_cmp (code);
CREATE TABLE ql_cmp_dim (id int PRIMARY KEY, name varchar(20));
INSERT INTO ql_cmp_dim SELECT g, 'grupo ' || g FROM generate_series(0, 3) g;
ANALYZE ql_cmp; ANALYZE ql_cmp_dim;
""",
    "mysql": f"""
DROP TABLE IF EXISTS ql_cmp; DROP TABLE IF EXISTS ql_cmp_dim;
CREATE TABLE ql_cmp (
  id bigint PRIMARY KEY, k int, c int, c2 int, r int, s varchar(20), s2 varchar(20),
  t datetime, code char(10), z int, g int, pad char(60) DEFAULT 'relleno');
INSERT INTO ql_cmp (id, k, c, c2, r, s, s2, t, code, z, g)
WITH d AS (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
           UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9),
     s AS (SELECT d1.n + d2.n * 10 + d3.n * 100 + d4.n * 1000 + d5.n * 10000 + d6.n * 100000 + 1 AS n
           FROM d d1, d d2, d d3, d d4, d d5, d d6)
SELECT n, (n * 7919) % 100000, (n * 104729) % 100000, (n * 1299709) % 100000,
  (n * 15485863) % 100000, CONCAT('v', (n * 7) % 100000), CONCAT('w', (n * 11) % 100000),
  TIMESTAMP '2020-01-01 00:00:00' + INTERVAL ((n * 31) % 2880000) MINUTE,
  LPAD((n * 17) % 100000, 10, '0'), CASE WHEN n % 50000 = 0 THEN NULL ELSE n END, n % 4
FROM s;
CREATE INDEX ql_cmp_k ON ql_cmp (k); CREATE INDEX ql_cmp_r ON ql_cmp (r);
CREATE INDEX ql_cmp_s ON ql_cmp (s); CREATE INDEX ql_cmp_t ON ql_cmp (t);
CREATE INDEX ql_cmp_code ON ql_cmp (code);
CREATE TABLE ql_cmp_dim (id int PRIMARY KEY, name varchar(20));
INSERT INTO ql_cmp_dim VALUES (0, 'grupo 0'), (1, 'grupo 1'), (2, 'grupo 2'), (3, 'grupo 3');
ANALYZE TABLE ql_cmp, ql_cmp_dim;
""",
}
TEARDOWN = "DROP TABLE IF EXISTS ql_cmp; DROP TABLE IF EXISTS ql_cmp_dim;"


# ---- escenarios ----

AP02 = "avoidable_full_scan"
AP03 = "non_sargable_predicate"
AP04 = "missing_index"


@dataclass
class Scenario:
    alias: str
    descripcion: str
    where: str  # con marcadores {R}, {R2}, {S}, {S2}, {D}, {CODE}, {N}, {G}
    # lo esperado: {AP02: True} | {AP03: [(subtipo, columna)]} |
    # {AP04: (igualdad, rango, por_rama)}; lo que no esta debe faltar
    expected: dict
    # por motor, si cambia: (where, expected)
    by_engine: dict = field(default_factory=dict)
    # consulta completa en vez de SELECT id AS qNN FROM ql_cmp WHERE ...
    full_sql: str | None = None

    def for_engine(self, engine):
        where, expected = self.by_engine.get(engine, (self.where, self.expected))
        if self.full_sql:
            sql = self.full_sql.format(alias=self.alias, where=where)
        else:
            sql = f"SELECT id AS {self.alias} FROM ql_cmp WHERE {where}"
        return sql, expected


def ap04(equality=(), range_=(), per_branch=False):
    return {AP02: True, AP04: (sorted(equality), sorted(range_), per_branch)}


def ap03(*pairs, ap04_fix=None):
    expected = {AP02: True, AP03: sorted(pairs)}
    if ap04_fix:
        expected[AP04] = ap04_fix
    return expected


ONLY_AP02 = {AP02: True}
NOTHING = {}

SCENARIOS = [
    # AP-04 indice ausente
    Scenario("q01", "igualdad sin indice", "c = {R}", ap04(["c"])),
    Scenario("q02", "rango sin indice", "c BETWEEN {R} AND {R} + 5", ap04(range_=["c"])),
    Scenario("q03", "IN sin indice", "c IN ({R}, {R2}, {R3})", ap04(["c"])),
    Scenario("q04", "IS NULL sin indice", "z IS NULL", ap04(["z"])),
    Scenario("q05", "AND de dos igualdades sin indice", "c = {R} AND c2 = {R2}", ap04(["c", "c2"])),
    Scenario("q06", "AND igualdad + rango sin indice", "c = {R} AND c2 > {R2}", ap04(["c"], ["c2"])),
    Scenario("q07", "OR sin ningun indice", "c = {R} OR c2 = {R2}", ap04(["c", "c2"], per_branch=True)),
    Scenario("q08", "OR con una rama indexada", "k = {R} OR c = {R2}", ap04(["c"], per_branch=True)),
    Scenario("q09", "OR igualdad + rango sin indice", "c = {R} OR c2 BETWEEN {R2} AND {R2} + 3",
             ap04(["c"], ["c2"], per_branch=True)),
    # AP-03 predicado no sargable
    Scenario("q10", "aritmetica sobre columna indexada (informe)", "k + 0 = {R}",
             ap03(("arithmetic_on_column", "k"))),
    Scenario("q11", "LOWER sobre columna indexada", "LOWER(s) = {S}", ap03(("function_on_column", "s"))),
    Scenario("q12", "DATE sobre columna indexada", "DATE(t) = {D}", ap03(("function_on_column", "t"))),
    Scenario("q13", "conversion de char a numero", "CAST(code AS INT) = {N}",
             ap03(("implicit_cast", "code")),
             by_engine={"mysql": ("code = {N}", ap03(("implicit_cast", "code")))}),
    Scenario("q14", "OR con la otra rama indexada", "k + 0 = {R} OR r = {R2}",
             ap03(("arithmetic_on_column", "k"))),
    Scenario("q15", "OR de dos columnas envueltas indexadas", "k + 0 = {R} OR LOWER(s) = {S}",
             ap03(("arithmetic_on_column", "k"), ("function_on_column", "s"))),
    Scenario("q16", "entero contra decimal", "k = {R}.5",
             # Postgres convierte la columna (k)::numeric; MySQL convierte el valor y usa el indice
             ap03(("implicit_cast", "k")),
             by_engine={"mysql": ("k = {R}.5", NOTHING)}),
    # AP-03 y AP-04 a la vez: dos arreglos posibles en un AND
    Scenario("q17", "AND: columna sin indice y columna envuelta", "c = {R} AND LOWER(s) = {S}",
             ap03(("function_on_column", "s"), ap04_fix=(["c"], [], False))),
    # solo AP-02
    Scenario("q18", "OR mixto: rama sin indice y rama envuelta", "c = {R} OR LOWER(s) = {S}", ONLY_AP02),
    Scenario("q19", "funcion sobre columna sin indice", "LOWER(s2) = {S2}", ONLY_AP02),
    Scenario("q20", "LIKE con comodin inicial", "s2 LIKE {LIKE}", ONLY_AP02),
    Scenario("q21", "columna contra columna", "c > c2 + 99000", ONLY_AP02),
    # controles: ningun hallazgo de los tres
    Scenario("q22", "busqueda por indice", "k = {R}", NOTHING),
    Scenario("q23", "OR con las dos ramas indexadas", "k = {R} OR r = {R2}", NOTHING),
    Scenario("q24", "AND con una parte indexada", "c = {R} AND k = {R2}", NOTHING),
    Scenario("q25", "selectividad alta (25 %)", "g = {G}", NOTHING),
    Scenario("q26", "JOIN (no es SELECT simple)", "q.c = {R}", NOTHING,
             full_sql="SELECT q.id AS {alias} FROM ql_cmp AS q JOIN ql_cmp_dim AS d ON d.id = q.g WHERE {where}"),
    Scenario("q27", "agregado (no es SELECT simple)", "c = {R}", NOTHING,
             full_sql="SELECT COUNT(*) AS {alias} FROM ql_cmp WHERE {where}"),
    Scenario("q28", "LIMIT (no es SELECT simple)", "c = {R}", NOTHING,
             full_sql="SELECT id AS {alias} FROM ql_cmp WHERE {where} LIMIT 5"),
]


def render(template: str, rng: random.Random) -> str:
    values = {
        "R": rng.randrange(100000), "R2": rng.randrange(100000), "R3": rng.randrange(100000),
        "S": f"'v{rng.randrange(100000)}'", "S2": f"'w{rng.randrange(100000)}'",
        # t cubre 2.000 dias desde 2020-01-01, ~500 filas por dia
        "D": f"'{datetime.fromordinal(datetime(2020, 1, 1).toordinal() + rng.randrange(1990)).date()}'",
        "CODE": f"'{rng.randrange(100000):010d}'", "N": rng.randrange(100000), "G": rng.randrange(4),
        "LIKE": f"'%{rng.randrange(1000, 100000)}'",
    }
    return template.format(**values)


# ---- infraestructura ----

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(cmd, input_text=None, check=True) -> str:
    result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:5])}... fallo: {result.stderr.strip()[:800]}")
    return result.stdout


@functools.cache
def compose_container(service):
    # Id del contenedor de <service> en el compose de la raiz. Sin container_name
    # fijo el nombre depende del proyecto (docker compose -p, COMPOSE_PROJECT_NAME).
    out = run(["docker", "compose", "-f", str(HERE.parents[1] / "docker-compose.yml"), "ps", "-q", service]).strip()
    if not out:
        raise RuntimeError(f"el servicio {service} del compose de la raiz no esta corriendo")
    return out


def engine_sql(engine: str, sql: str) -> str:
    if engine == "postgres":
        cmd = ["docker", "exec", "-i", "ql_postgres", "psql", "-U", "ql_user", "-d", "ql_demo",
               "-At", "-v", "ON_ERROR_STOP=1"]
    else:
        cmd = ["docker", "exec", "-i", "ql_mysql", "mysql", "-uroot", "-pql_root", "ql_demo", "-N"]
    return run(cmd, sql)


def querylens_sql(sql: str) -> str:
    return run(["docker", "exec", "-i", compose_container("postgres"), "psql", "-U", "ql_user", "-d", "ql_demo", "-At"],
               sql).strip()


def registered_db_id(engine: str) -> str | None:
    names = ", ".join(f"'{name}'" for name in ENGINE_NAMES[engine])
    row = querylens_sql(
        "SELECT database_identifier FROM public.registered_databases "
        f"WHERE lower(engine) IN ({names}) AND is_active AND monitoring_enabled ORDER BY created_at LIMIT 1;"
    )
    return row or None


def queue_length() -> int:
    return int(querylens_sql("SELECT queue_length FROM pgmq.metrics('analyze_job');") or 0)


def wait_queue_drained(timeout=120) -> None:
    deadline = time.monotonic() + timeout
    while queue_length() and time.monotonic() < deadline:
        time.sleep(1)


def reset_statistics(engine: str) -> None:
    # top_impact_queries son las 10 de mas tiempo acumulado: sin esto, las
    # consultas de los escenarios anteriores ocupan el top
    if engine == "postgres":
        engine_sql(engine, "SELECT pg_stat_statements_reset();")
    else:
        engine_sql(engine, "TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")


def start_load(engine: str, file_name: str, seconds: int) -> None:
    run(["docker", "exec", LOAD_CONTAINER, "rm", "-f", STOP_FILE])
    for _ in range(WORKERS):
        run(["docker", "exec", "-d", LOAD_CONTAINER, "bash", "/tmp/cmp_load.sh", engine, file_name, str(seconds)])


def stop_load() -> None:
    run(["docker", "exec", LOAD_CONTAINER, "touch", STOP_FILE], check=False)
    kill_script = (
        'for p in /proc/[0-9]*; do '
        'grep -qa "[q]l_cmp_workload" "$p/cmdline" 2>/dev/null && kill "${p#/proc/}" 2>/dev/null; '
        'done; true'
    )
    run(["docker", "exec", LOAD_CONTAINER, "bash", "-c", kill_script], check=False)


def findings_for(db_id: str, alias: str, since: str) -> list[dict]:
    raw = querylens_sql(
        "SELECT coalesce(json_agg(t), '[]') FROM ("
        " SELECT antipatron, severidad, table_name, evidencia FROM public.hallazgos"
        f" WHERE db_id = '{db_id}' AND detectado_en >= '{since}'"
        f"   AND evidencia::text ILIKE '%{alias}%'"
        ") t;"
    )
    return json.loads(raw or "[]")


def last_snapshot_view(db_id: str, alias: str) -> dict:
    # Para diagnosticar un fallo: como vio el pipeline la consulta en el
    # ultimo snapshot archivado de esa base
    raw = querylens_sql(
        "SELECT message::text FROM pgmq.a_analyze_job"
        f" WHERE message->>'db_id' = '{db_id}' AND message::text ILIKE '%{alias}%'"
        " ORDER BY msg_id DESC LIMIT 1;"
    )
    if not raw:
        return {"en_snapshot": False}
    snapshot = json.loads(raw)
    view = {"en_snapshot": True}
    for candidate in snapshot.get("top_impact_queries") or []:
        if alias in (candidate.get("canonic_query") or "").lower():
            view["canonic_query"] = candidate.get("canonic_query")
            view["execution_count"] = candidate.get("execution_count")
            for explain in snapshot.get("canonic_explains") or []:
                if explain.get("query_id") == candidate.get("query_id"):
                    view["explain_source"] = explain.get("explain_source")
                    view["scans"] = [
                        {key: op.get(key) for key in ("relation", "access_method", "index_name", "predicate")}
                        for op in explain["canonical_plan"]["physical_operations"] if op.get("type") == "scan"
                    ]
    if "canonic_query" not in view:
        view["top_impact"] = False
    return view


# ---- evaluacion ----

def summarize_findings(findings: list[dict]) -> dict:
    got = {}
    for finding in findings:
        rule = finding["antipatron"]
        evidencia = finding.get("evidencia") or {}
        if rule == AP02:
            got[AP02] = True
        elif rule == AP03:
            got.setdefault(AP03, set()).add((evidencia.get("subtipo"), evidencia.get("column")))
        elif rule == AP04:
            got.setdefault(AP04, set()).add((
                tuple(sorted(evidencia.get("equality_columns") or [])),
                tuple(sorted(evidencia.get("range_columns") or [])),
                bool(evidencia.get("indice_por_rama")),
            ))
    if AP03 in got:
        got[AP03] = sorted(got[AP03])
    if AP04 in got:
        got[AP04] = sorted(got[AP04])
    return got


def compare(expected: dict, got: dict) -> list[str]:
    problems = []
    for rule in RULES:
        if rule in expected and rule not in got:
            problems.append(f"FALTA {rule}")
        elif rule not in expected and rule in got:
            problems.append(f"SOBRA {rule}: {got[rule]}")
        elif rule == AP03 and rule in expected and [tuple(p) for p in expected[rule]] != got[rule]:
            problems.append(f"AP-03 distinto: esperado {expected[rule]}, obtenido {got[rule]}")
        elif rule == AP04 and rule in expected:
            equality, range_, per_branch = expected[rule]
            want = [(tuple(equality), tuple(range_), per_branch)]
            if got[rule] != want:
                problems.append(f"AP-04 distinto: esperado {want}, obtenido {got[rule]}")
    return problems


def run_engine(engine: str, scenarios, keep: bool) -> list[dict]:
    db_id = registered_db_id(engine)
    if db_id is None:
        log(f"{engine}: no hay una base registrada y activa de este motor; se salta.")
        return []
    log(f"===== {engine} | db_id {db_id} =====")
    log(f"Preparando ql_cmp ({ROWS:,} filas)...")
    engine_sql(engine, SETUP[engine])
    run(["docker", "cp", str(HERE / "cmp_load.sh"), f"{LOAD_CONTAINER}:/tmp/cmp_load.sh"])

    rng = random.Random(f"{engine}-20261015")
    results = []
    try:
        for scenario in scenarios:
            sql_template, expected = scenario.for_engine(engine)
            lines = "".join(render(sql_template, rng) + ";\n" for _ in range(LINES_PER_FILE))
            file_name = f"/tmp/ql_cmp_{scenario.alias}.sql"
            local = HERE / f".{scenario.alias}.sql"
            local.write_text(lines, encoding="utf-8")
            run(["docker", "cp", str(local), f"{LOAD_CONTAINER}:{file_name}"])
            local.unlink()

            reset_statistics(engine)
            since = querylens_sql("SELECT now();")
            log(f"{scenario.alias} {scenario.descripcion}: {sql_template}")
            start_load(engine, file_name, WARMUP_SECONDS + OBSERVE_SECONDS + 30)
            time.sleep(WARMUP_SECONDS + OBSERVE_SECONDS)
            stop_load()
            # un ciclo mas del runner y que el detector vacie la cola
            time.sleep(15)
            wait_queue_drained()

            findings = findings_for(db_id, scenario.alias, since)
            got = summarize_findings(findings)
            problems = compare(expected, got)
            result = {
                "motor": engine, "alias": scenario.alias, "descripcion": scenario.descripcion,
                "sql": sql_template, "esperado": {k: v for k, v in expected.items()},
                "obtenido": got, "hallazgos": len(findings), "problemas": problems,
                "estado": "OK" if not problems else "FALLA",
            }
            if problems:
                result["diagnostico"] = last_snapshot_view(db_id, scenario.alias)
            results.append(result)
            log(f"   -> {result['estado']} | {len(findings)} hallazgo(s) | obtenido {got}"
                + (f" | {problems}" if problems else ""))
    finally:
        stop_load()
        if not keep:
            engine_sql(engine, TEARDOWN)
    return results


def report(results: list[dict]) -> str:
    lines = ["# Campana AP-02 / AP-03 / AP-04 por el flujo real", ""]
    for engine in ("postgres", "mysql"):
        rows = [r for r in results if r["motor"] == engine]
        if not rows:
            continue
        ok = sum(r["estado"] == "OK" for r in rows)
        lines += [f"## {engine}: {ok}/{len(rows)} escenarios OK", "",
                  "| Escenario | Consulta | Esperado | Obtenido | Estado |", "|---|---|---|---|---|"]
        for r in rows:
            expected = ", ".join(sorted(r["esperado"])) or "nada"
            got = ", ".join(sorted(r["obtenido"])) or "nada"
            state = r["estado"] if not r["problemas"] else f"FALLA: {'; '.join(r['problemas'])}"
            lines.append(f"| {r['alias']} {r['descripcion']} | `{r['sql']}` | {expected} | {got} | {state} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    parser.add_argument("--only", help="aliases separados por coma (ej. q01,q07)")
    parser.add_argument("--keep", action="store_true", help="no borrar ql_cmp al terminar")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in SCENARIOS if s.alias in wanted]
    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]

    results = []
    for engine in engines:
        results += run_engine(engine, scenarios, args.keep)

    stamp = time.strftime("%Y%m%d_%H%M")
    (HERE / f"resultados_{stamp}.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    text = report(results)
    (HERE / f"reporte_{stamp}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if all(r["estado"] == "OK" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
