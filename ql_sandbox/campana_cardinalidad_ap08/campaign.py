"""Bateria de AP-08 (error grave de estimacion de cardinalidad) por el flujo real
de QueryLens.

Misma mecanica que la campana de AP-02/03/04: la carga sale de ql_sysbench, el
runner del pipeline toma los snapshots de las bases registradas, el
detection-engine los procesa con sus ACTIVE_RULES y se leen los hallazgos de
public.hallazgos para cada consulta (alias propio, SELECT id AS cardNN ...).

Lo que se quiere medir es de donde sale la estimacion que compara AP-08:
  - Postgres: el ultimo plan que registro auto_explain (consultas >= 50 ms) o,
    si no hay, el GENERIC_PLAN.
  - MySQL: el EXPLAIN de QUERY_SAMPLE_TEXT (la ejecucion mas lenta del digest).
En los dos casos es el plan de UNA ejecucion, con un valor de parametro, y se
compara con el promedio real de todas las ejecuciones de la ventana. Los
escenarios card07 y card08 mezclan valores con cantidades de filas muy
distintas sin que haya ningun error de estimacion: lo esperado es que AP-08
no aparezca.

Tabla ql_card, 1.000.000 de filas:
  k    int, con indice, 100.000 valores (10 filas cada uno)
  a,b,c int, sin indice, a = b = c = n % 50 (20.000 filas por valor, correlacionadas)
  u    int, sin indice, 1.000 valores (1.000 filas cada uno)
  sk   int, con indice: la mitad de las filas vale 0, el resto un valor unico
  sk2  igual que sk, sin indice
  g    int, 4 valores (join con ql_card_dim)
Tabla ql_card_stale: 1.000 filas analizadas (en MySQL con histograma sobre v),
despues 200.000 filas mas con v = 7 sin volver a analizar (autovacuum /
STATS_AUTO_RECALC apagados).

REQUISITOS
  - Sandbox (ql_postgres con auto_explain, ql_mysql, ql_sysbench) y compose de la raiz levantados.
  - Las bases del sandbox registradas en QueryLens.
  - cardinality_misestimate en ACTIVE_RULES del detection_engine (imagen al dia).

USO (desde la raiz del repo)
  python ql_sandbox/campana_cardinalidad_ap08/campaign.py [--engine postgres|mysql|both]
         [--only card01,card07] [--keep]

Salida: ql_sandbox/campana_cardinalidad_ap08/resultados_<fecha>.json y reporte_<fecha>.md
"""
import argparse
import functools
import json
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
LOAD_SCRIPT = HERE.parent / "campana_escaneo_completo_ap02_ap03_ap04" / "cmp_load.sh"
LOAD_CONTAINER = "ql_sysbench"
STOP_FILE = "/tmp/ql_cmp_stop"
ROWS = 1_000_000
STALE_ROWS = 200_000
WORKERS = 4
WARMUP_SECONDS = 5
OBSERVE_SECONDS = 28
LINES_PER_FILE = 2000
AP08 = "cardinality_misestimate"
ENGINE_NAMES = {"postgres": ("postgresql", "postgres"), "mysql": ("mysql",)}

SETUP = {
    "postgres": f"""
DROP TABLE IF EXISTS ql_card; DROP TABLE IF EXISTS ql_card_dim; DROP TABLE IF EXISTS ql_card_stale;
CREATE TABLE ql_card (
  id bigint PRIMARY KEY, k int, a int, b int, c int, u int, sk int, sk2 int, g int,
  pad char(60) DEFAULT 'relleno');
INSERT INTO ql_card (id, k, a, b, c, u, sk, sk2, g)
SELECT n, (n * 7919) % 100000, n % 50, n % 50, n % 50, (n * 104729) % 1000,
  CASE WHEN n % 2 = 0 THEN 0 ELSE n END, CASE WHEN n % 2 = 0 THEN 0 ELSE n END, n % 4
FROM generate_series(1::bigint, {ROWS}) AS n;
CREATE INDEX ql_card_k ON ql_card (k); CREATE INDEX ql_card_sk ON ql_card (sk);
CREATE TABLE ql_card_dim (id int PRIMARY KEY, name varchar(20));
INSERT INTO ql_card_dim SELECT g, 'grupo ' || g FROM generate_series(0, 3) g;
CREATE TABLE ql_card_stale (id bigserial PRIMARY KEY, v int, pad char(60) DEFAULT 'relleno')
  WITH (autovacuum_enabled = false);
INSERT INTO ql_card_stale (v) SELECT n % 1000 + 1 FROM generate_series(1, 1000) AS n;
ANALYZE ql_card; ANALYZE ql_card_dim; ANALYZE ql_card_stale;
INSERT INTO ql_card_stale (v) SELECT 7 FROM generate_series(1, {STALE_ROWS});
""",
    "mysql": f"""
DROP TABLE IF EXISTS ql_card; DROP TABLE IF EXISTS ql_card_dim; DROP TABLE IF EXISTS ql_card_stale;
CREATE TABLE ql_card (
  id bigint PRIMARY KEY, k int, a int, b int, c int, u int, sk int, sk2 int, g int,
  pad char(60) DEFAULT 'relleno');
INSERT INTO ql_card (id, k, a, b, c, u, sk, sk2, g)
WITH d AS (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
           UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9),
     s AS (SELECT d1.n + d2.n * 10 + d3.n * 100 + d4.n * 1000 + d5.n * 10000 + d6.n * 100000 + 1 AS n
           FROM d d1, d d2, d d3, d d4, d d5, d d6)
SELECT n, (n * 7919) % 100000, n % 50, n % 50, n % 50, (n * 104729) % 1000,
  IF(n % 2 = 0, 0, n), IF(n % 2 = 0, 0, n), n % 4
FROM s;
CREATE INDEX ql_card_k ON ql_card (k); CREATE INDEX ql_card_sk ON ql_card (sk);
CREATE TABLE ql_card_dim (id int PRIMARY KEY, name varchar(20));
INSERT INTO ql_card_dim VALUES (0, 'grupo 0'), (1, 'grupo 1'), (2, 'grupo 2'), (3, 'grupo 3');
CREATE TABLE ql_card_stale (id bigint AUTO_INCREMENT PRIMARY KEY, v int, pad char(60) DEFAULT 'relleno')
  STATS_PERSISTENT = 1 STATS_AUTO_RECALC = 0;
INSERT INTO ql_card_stale (v) SELECT id % 1000 + 1 FROM ql_card WHERE id <= 1000;
ANALYZE TABLE ql_card, ql_card_dim, ql_card_stale;
-- sin histograma MySQL no tiene estadisticas de v (estima 10 % fijo): el
-- histograma es lo que queda viejo, MySQL nunca lo actualiza solo
ANALYZE TABLE ql_card_stale UPDATE HISTOGRAM ON v WITH 100 BUCKETS;
INSERT INTO ql_card_stale (v) SELECT 7 FROM ql_card WHERE id <= {STALE_ROWS};
""",
}
TEARDOWN = "DROP TABLE IF EXISTS ql_card; DROP TABLE IF EXISTS ql_card_dim; DROP TABLE IF EXISTS ql_card_stale;"


# ---- escenarios ----

def _a(rng):
    return rng.randrange(50)


def _rare(rng):
    # un valor impar: una sola fila con ese sk / sk2
    return rng.randrange(ROWS // 2) * 2 + 1


def _mixed(hot_share):
    # 0 (la mitad de la tabla) con probabilidad hot_share; si no, un valor de una fila
    def pick(rng):
        return 0 if rng.random() < hot_share else _rare(rng)
    return pick


@dataclass
class Scenario:
    alias: str
    descripcion: str
    template: str  # {alias} y {v} (el valor de esa linea)
    value: Callable[[random.Random], int]
    # AP-08 esperado por motor
    expected: dict
    # filas reales por ejecucion esperadas (para leer el reporte)
    real_rows: str
    nota: str = ""


SCENARIOS = [
    # positivos: hay un error de estimacion real
    Scenario("card01", "columnas correlacionadas a = b = c",
             "SELECT id AS {alias} FROM ql_card WHERE a = {v} AND b = {v} AND c = {v}",
             _a, {"postgres": True, "mysql": True}, "20.000",
             "Postgres multiplica las tres selectividades (1/50^3); MySQL sin histograma usa 10 % cada una"),
    Scenario("card02", "correlacionadas dentro de un JOIN",
             "SELECT q.id AS {alias} FROM ql_card AS q JOIN ql_card_dim AS d ON d.id = q.g "
             "WHERE q.a = {v} AND q.b = {v} AND q.c = {v}",
             _a, {"postgres": True, "mysql": True}, "20.000"),
    Scenario("card03", "estadisticas desactualizadas (200.000 filas nuevas sin ANALYZE)",
             "SELECT id AS {alias} FROM ql_card_stale WHERE v = {v}",
             lambda rng: 7, {"postgres": True, "mysql": True}, "200.001"),
    # controles: la estimacion es buena (o el error no llega al umbral)
    Scenario("card04", "igualdad sobre indice, distribucion uniforme",
             "SELECT id AS {alias} FROM ql_card WHERE k = {v}",
             lambda rng: rng.randrange(100000), {"postgres": False, "mysql": False}, "10"),
    Scenario("card05", "igualdad sin indice, distribucion uniforme",
             "SELECT id AS {alias} FROM ql_card WHERE u = {v}",
             lambda rng: rng.randrange(1000), {"postgres": False, "mysql": True}, "1.000",
             "MySQL sin indice ni histograma estima 10 % (100.000): es un error real de su estimacion"),
    Scenario("card06", "indice sesgado, solo valores raros",
             "SELECT id AS {alias} FROM ql_card WHERE sk = {v}",
             _rare, {"postgres": False, "mysql": False}, "1"),
    # mezclas: cada plan es correcto para su valor; el promedio de la ventana no
    Scenario("card07", "indice sesgado, 1 % valor frecuente (500.000 filas) y 99 % raros",
             "SELECT id AS {alias} FROM ql_card WHERE sk = {v}",
             _mixed(0.01), {"postgres": False, "mysql": False}, "~5.000 (promedio)",
             "las raras usan el indice (< 50 ms, sin auto_explain); el sample de MySQL es la mas lenta"),
    Scenario("card08", "sin indice y sesgado, 10 % valor frecuente y 90 % raros",
             "SELECT id AS {alias} FROM ql_card WHERE sk2 = {v}",
             _mixed(0.10), {"postgres": False, "mysql": True}, "~50.000 (promedio)",
             "todas son escaneo completo (> 50 ms): auto_explain registra cada una y gana la ultima. "
             "MySQL sin indice ni histograma estima 10 % para cualquier valor: con las filas de la "
             "misma ejecucion, un valor raro (1 fila) es un error real, como card05"),
]


# ---- infraestructura (igual que la campana de AP-02/03/04) ----

def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(cmd, input_text=None, check=True) -> str:
    result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:5])}... fallo: {result.stderr.strip()[:800]}")
    return result.stdout


@functools.cache
def compose_container(service):
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
        "SELECT coalesce(json_agg(t ORDER BY t.detectado_en), '[]') FROM ("
        " SELECT antipatron, severidad, table_name, evidencia, detectado_en FROM public.hallazgos"
        f" WHERE db_id = '{db_id}' AND detectado_en >= '{since}'"
        f"   AND evidencia::text ILIKE '%{alias}%'"
        ") t;"
    )
    return json.loads(raw or "[]")


def snapshot_views(db_id: str, alias: str, since: str) -> list[dict]:
    # Como vio el pipeline la consulta en cada snapshot del escenario: de donde
    # salio el plan, cuantas filas estimo y cuantas devolvio en acumulado
    raw = querylens_sql(
        "SELECT coalesce(json_agg(message ORDER BY msg_id), '[]') FROM pgmq.a_analyze_job"
        f" WHERE message->>'db_id' = '{db_id}' AND enqueued_at >= '{since}'"
        f"   AND message::text ILIKE '%{alias}%';"
    )
    views = []
    for snapshot in json.loads(raw or "[]"):
        for candidate in snapshot.get("top_impact_queries") or []:
            if alias not in (candidate.get("canonic_query") or "").lower():
                continue
            view = {
                "execution_count": candidate.get("execution_count"),
                "rows_returned": candidate.get("rows_returned"),
            }
            for explain in snapshot.get("canonic_explains") or []:
                if explain.get("query_id") == candidate.get("query_id"):
                    view["explain_source"] = explain.get("explain_source")
                    view["output_rows"] = explain["canonical_plan"]["estimates"].get("output_rows")
            views.append(view)
    return views


# ---- evaluacion ----

def run_engine(engine: str, scenarios, keep: bool) -> list[dict]:
    db_id = registered_db_id(engine)
    if db_id is None:
        log(f"{engine}: no hay una base registrada y activa de este motor; se salta.")
        return []
    log(f"===== {engine} | db_id {db_id} =====")
    log(f"Preparando ql_card ({ROWS:,} filas) y ql_card_stale...")
    engine_sql(engine, SETUP[engine])
    run(["docker", "cp", str(LOAD_SCRIPT), f"{LOAD_CONTAINER}:/tmp/cmp_load.sh"])

    rng = random.Random(f"{engine}-ap08")
    results = []
    try:
        for scenario in scenarios:
            lines = "".join(
                scenario.template.format(alias=scenario.alias, v=scenario.value(rng)) + ";\n"
                for _ in range(LINES_PER_FILE)
            )
            file_name = f"/tmp/ql_card_{scenario.alias}.sql"
            local = HERE / f".{scenario.alias}.sql"
            local.write_text(lines, encoding="utf-8")
            run(["docker", "cp", str(local), f"{LOAD_CONTAINER}:{file_name}"])
            local.unlink()

            reset_statistics(engine)
            since = querylens_sql("SELECT now();")
            log(f"{scenario.alias} {scenario.descripcion}")
            start_load(engine, file_name, WARMUP_SECONDS + OBSERVE_SECONDS + 30)
            time.sleep(WARMUP_SECONDS + OBSERVE_SECONDS)
            stop_load()
            time.sleep(15)
            wait_queue_drained()

            findings = findings_for(db_id, scenario.alias, since)
            ap08 = [f for f in findings if f["antipatron"] == AP08]
            others = sorted({f["antipatron"] for f in findings} - {AP08})
            expected = scenario.expected[engine]
            got = bool(ap08)
            if got == expected:
                state = "VP" if got else "VN"
            else:
                state = "FP" if got else "FN"
            result = {
                "motor": engine, "alias": scenario.alias, "descripcion": scenario.descripcion,
                "sql": scenario.template, "filas_reales": scenario.real_rows, "nota": scenario.nota,
                "esperado": expected, "obtenido": got, "estado": state,
                "ap08": [
                    {key: f["evidencia"].get(key) for key in (
                        "estimated_rows", "actual_rows", "q_error", "direccion",
                        "explain_source", "window_calls")}
                    | {"severidad": f["severidad"], "detectado_en": f["detectado_en"]}
                    for f in ap08
                ],
                "otros_antipatrones": others,
                "snapshots": snapshot_views(db_id, scenario.alias, since),
            }
            results.append(result)
            sources = sorted({v.get("explain_source") or "sin plan" for v in result["snapshots"]})
            log(f"   -> {state} | AP-08 en {len(ap08)} snapshot(s) | planes: {sources}"
                + (f" | q-error {[a['q_error'] for a in result['ap08']]}" if ap08 else "")
                + (f" | otros: {others}" if others else ""))
    finally:
        stop_load()
        if not keep:
            engine_sql(engine, TEARDOWN)
    return results


def report(results: list[dict]) -> str:
    lines = ["# Bateria AP-08 error grave de estimacion de cardinalidad (flujo real)", ""]
    for engine in ("postgres", "mysql"):
        rows = [r for r in results if r["motor"] == engine]
        if not rows:
            continue
        ok = sum(r["estado"] in ("VP", "VN") for r in rows)
        lines += [f"## {engine}: {ok}/{len(rows)} como se esperaba", "",
                  "| Escenario | Filas reales | Esperado | Estado | Planes (fuente: filas estimadas) | AP-08 (q-error) | Otros |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            plans = sorted({
                f"{v.get('explain_source') or 'sin plan'}: {v.get('output_rows')}" for v in r["snapshots"]
            })
            q_errors = ", ".join(f"{a['q_error']:g} ({a['estimated_rows']:g} vs {a['actual_rows']:g})"
                                 for a in r["ap08"]) or "-"
            lines.append(
                f"| {r['alias']} {r['descripcion']} | {r['filas_reales']} | "
                f"{'detecta' if r['esperado'] else 'no detecta'} | {r['estado']} | "
                f"{'; '.join(plans) or '-'} | {q_errors} | {', '.join(r['otros_antipatrones']) or '-'} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="both")
    parser.add_argument("--only", help="aliases separados por coma (ej. card01,card07)")
    parser.add_argument("--keep", action="store_true", help="no borrar las tablas al terminar")
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
        json.dumps(results, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    text = report(results)
    (HERE / f"reporte_{stamp}.md").write_text(text, encoding="utf-8")
    print(text)
    return 0 if all(r["estado"] in ("VP", "VN") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
