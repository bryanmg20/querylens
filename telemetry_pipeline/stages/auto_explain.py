"""Planes de auto_explain leidos del log JSON de Postgres.

auto_explain escribe en el log el plan de cada query que pasa de
auto_explain.log_min_duration. La guia references/006 lo activa con
log_format=json + jsonlog y crea querylens.log_files() / querylens.read_log()
para que el rol de monitoreo lea ese log sin permisos sobre archivos.

Este stage lee la ventana mas reciente del log y deja en
stats["auto_explain_plans"] el ultimo plan registrado de cada candidato
({query_id: {"Plan": ...}}). ExplainStage lo usa antes que
EXPLAIN (GENERIC_PLAN): es el plan de una ejecucion real. Si faltan las
funciones, el log o el plan de un candidato, ese candidato sigue por el plan
generico, como antes.

El cruce es por query_id: jsonlog lo escribe en cada linea y es el mismo
queryid de pg_stat_statements. Los candidatos ya excluyen las queries del rol
de monitoreo (STATEMENTS_QUERY), asi que las suyas nunca se cruzan.
"""
import json
import re

from sqlalchemy import text

from health.report import HealthReport
from logger import get_logger
from models.stats import Stats

logger = get_logger(__name__)

LOG_FILES_SQL = "SELECT name, size FROM querylens.log_files() ORDER BY modification DESC"
READ_LOG_SQL = "SELECT querylens.read_log(:name, :start_offset, :max_bytes) AS chunk"

# Bytes que se leen por ciclo, del final del log hacia atras y repartidos entre
# los archivos mas recientes. No hay estado entre ciclos: cada uno relee la
# ventana. Un plan JSON ocupa unos 2-6 KB, asi que entran varios cientos; una
# query que no se registro dentro de la ventana sigue con el plan generico.
LOG_WINDOW_BYTES = 2 * 1024 * 1024

# query_id es clave de primer nivel de la linea jsonlog. Dentro de un string
# JSON toda comilla va escapada (\"), asi que ,"query_id": no puede salir del
# texto de la query: se filtra la linea sin parsearla entera.
_QUERY_ID_RE = re.compile(rb',"query_id":(-?\d+)')

# auto_explain escribe "duration: <ms> ms  plan:\n<plan>".
_PLAN_MARKER = "plan:\n"


class AutoExplainStage:
    def __init__(self, collector, report: HealthReport | None = None):
        self.collector = collector
        self.report = report if report is not None else HealthReport()

    def execute(self, stats: Stats, conn) -> Stats:
        stats["auto_explain_plans"] = {}
        if self.collector.source_dialect != "postgres":
            return stats

        wanted = {
            str(candidate["query_id"]): candidate
            for candidate in stats.get("top_impact_queries") or []
            if candidate.get("query_id") is not None and candidate.get("ready_for_explain")
        }
        if not wanted:
            return stats

        # El issue va en el scope "explain" (el CHECK de pipeline_health no
        # tiene otro): ExplainStage lo marca evaluado en cada ciclo.
        try:
            # Transaccion corta, como cada EXPLAIN (M-12): si la funcion no
            # existe, el rollback deja la conexion lista para ExplainStage.
            with conn.begin():
                plans = self._read_plans(conn, wanted)
        except Exception as e:
            logger.info(f"postgres | AUTO_EXPLAIN | log no disponible, se usa el plan generico | {e}")
            self.report.add("AUTO_EXPLAIN_LOG_UNAVAILABLE")
            return stats

        stats["auto_explain_plans"] = plans
        logger.debug(f"postgres | AUTO_EXPLAIN | {len(plans)}/{len(wanted)} candidatos con plan del log")
        return stats

    def _read_plans(self, conn, wanted):
        plans = {}
        budget = LOG_WINDOW_BYTES
        files = list(conn.execute(text(LOG_FILES_SQL)).mappings())
        # Del archivo mas nuevo al mas viejo: el primer plan que aparece de un
        # query_id es el ultimo que se registro.
        for log_file in files:
            if budget <= 0 or len(plans) == len(wanted):
                break
            size = int(log_file["size"] or 0)
            if size <= 0:
                continue
            length = min(size, budget)
            start_offset = size - length
            result = conn.execute(
                text(READ_LOG_SQL).bindparams(
                    name=log_file["name"], start_offset=start_offset, max_bytes=length
                )
            )
            row = next(result.mappings(), None)
            chunk = row["chunk"] if row is not None else None
            # NULL: el archivo roto entre listar y leer.
            if chunk is None:
                continue
            budget -= length
            _collect_plans(bytes(chunk), start_offset > 0, wanted, plans)
        return plans


def _collect_plans(chunk, starts_mid_line, wanted, plans):
    lines = chunk.split(b"\n")
    if starts_mid_line:
        # La ventana empieza en medio de una linea (o de un caracter UTF-8):
        # ese pedazo no es JSON valido.
        lines = lines[1:]
    for line in reversed(lines):
        match = _QUERY_ID_RE.search(line)
        if match is None:
            continue
        candidate = wanted.get(match.group(1).decode())
        if candidate is None or candidate["query_id"] in plans:
            continue
        plan = _plan_from_line(line, candidate.get("database_name"))
        if plan is not None:
            plans[candidate["query_id"]] = plan
            if len(plans) == len(wanted):
                return


def _plan_from_line(line, database_name):
    """El plan de una linea jsonlog de auto_explain, o None si no lo es.

    Solo se conserva "Plan": "Query Text" y "Query Parameters" traen los
    valores reales de la query y no hacen falta para normalizar el plan."""
    try:
        record = json.loads(line)
    except ValueError:
        # Ultima linea a medio escribir, o bytes que no son UTF-8.
        return None
    if not isinstance(record, dict):
        return None
    # queryid no incluye la base: dos bases del servidor pueden compartirlo.
    if database_name and record.get("dbname") != database_name:
        return None
    message = record.get("message")
    if not isinstance(message, str) or not message.startswith("duration:"):
        return None
    _, marker, body = message.partition(_PLAN_MARKER)
    if not marker:
        return None
    try:
        logged = json.loads(body)
    except ValueError:
        # auto_explain.log_format distinto de json.
        return None
    if not isinstance(logged, dict) or not isinstance(logged.get("Plan"), dict):
        return None
    return {"Plan": logged["Plan"]}
