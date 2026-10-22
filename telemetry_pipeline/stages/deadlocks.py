"""Informes de abrazo mortal de la ventana reciente (CT-01 a CT-04).

El motor resuelve un abrazo mortal en cuanto lo detecta (Postgres tras
deadlock_timeout, InnoDB al instante): el ciclo nunca aparece en una muestra
de active_queries. Lo que queda es el informe que el motor escribe al
revertir la victima, y eso es lo que lee este stage.

Postgres: las lineas "deadlock detected" (state_code 40P01) del log JSON, de
la misma ventana que lee auto_explain (stages/pg_log.PgLogTail, una sola
lectura por ciclo; guia references/006). Los errores van al log con la
configuracion por defecto (log_min_messages=warning).

MySQL: SHOW ENGINE INNODB STATUS trae el detalle, pero solo del ultimo abrazo.
performance_schema.error_log trae el encabezado de cada uno (MY-012468, con
innodb_print_all_deadlocks=ON y log_error_verbosity=3), pero no el detalle,
que InnoDB escribe directo al stderr. Se combinan: el encabezado que coincide
con el ultimo informe le da su hora exacta; los demas de la ventana salen
como abrazos sin detalle (complete=False).

No hay estado entre ciclos: cada snapshot trae los informes de los ultimos
LOOKBACK_S segundos y el detector descarta los que ya vio.
"""
import json
from datetime import datetime, timedelta

from querylens_contract import to_naive_utc
from sqlalchemy import text

from health.report import HealthReport
from logger import get_logger
from models.stats import Stats
from stages.deadlock_reports import parse_innodb_status, parse_innodb_timestamp, parse_postgres_deadlock
from stages.pg_log import PgLogTail

logger = get_logger(__name__)

# Ventana de informes por snapshot. Cubre varios ciclos (10 s) y el backoff
# del runner (tope 60 s): un abrazo no se pierde porque un ciclo falle.
LOOKBACK_S = 120

# La ventana del log es la de PgLogTail (2 MB). Un dia con mucho auto_explain
# puede escribir mas que eso en LOOKBACK_S: los abrazos que queden antes de la
# ventana leida se pierden.

# Un encabezado de error_log y el informe de INNODB STATUS son el mismo abrazo
# si estan a menos de esto (INNODB STATUS escribe la hora sin fracciones).
_MATCH_TOLERANCE = timedelta(seconds=2)

# state_code es clave de primer nivel de la linea jsonlog: se filtra sin parsear.
_PG_DEADLOCK_MARKER = b'"state_code":"40P01"'

PG_SETTINGS_SQL = """
SELECT current_setting('log_error_verbosity') AS log_error_verbosity,
       current_setting('log_min_messages')    AS log_min_messages,
       current_database()                     AS database_name,
       now() AT TIME ZONE 'UTC'               AS now_utc
"""

# Cada linea jsonlog trae la hora en log_timezone con su abreviatura ("UTC",
# "-05", "CET"): el servidor la entiende sin ambiguedad.
PG_TIMESTAMPS_SQL = text(
    "SELECT raw, (raw::timestamptz AT TIME ZONE 'UTC') AS utc FROM unnest(CAST(:raw AS text[])) AS raw"
)

PG_RELATIONS_SQL = text(
    """
    SELECT c.oid::bigint AS oid, n.nspname AS schema_name, c.relname AS table_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = ANY(CAST(:oids AS oid[]))
    """
)

# La sesion del pipeline esta en +00:00 (config/connections): LOGGED y NOW(6)
# salen en UTC. INNODB STATUS escribe en la zona del sistema: el offset la
# pasa a UTC.
MYSQL_SETTINGS_SQL = """
SELECT UTC_TIMESTAMP(6) AS now_utc,
       TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', 'SYSTEM')) AS system_offset_s,
       @@innodb_print_all_deadlocks AS print_all_deadlocks,
       @@log_error_verbosity AS log_error_verbosity
"""

INNODB_STATUS_SQL = "SHOW ENGINE INNODB STATUS"

MYSQL_ERROR_LOG_SQL = f"""
SELECT LOGGED AS logged_at
FROM performance_schema.error_log
WHERE ERROR_CODE = 'MY-012468'
  AND LOGGED >= NOW(6) - INTERVAL {LOOKBACK_S} SECOND
ORDER BY LOGGED
"""

_PG_SILENT_LEVELS = {"log", "fatal", "panic"}


class DeadlocksStage:
    def __init__(self, collector, report: HealthReport | None = None):
        self.collector = collector
        self.report = report if report is not None else HealthReport()

    def execute(self, stats: Stats, conn, log: PgLogTail | None = None) -> Stats:
        stats["deadlocks"] = []
        dialect = self.collector.source_dialect
        if dialect not in ("postgres", "mysql"):
            return stats
        try:
            # Transaccion corta, como auto_explain: si falta una funcion o un
            # permiso, el rollback deja la conexion lista para lo que sigue.
            with conn.begin():
                if dialect == "postgres":
                    reports = self._postgres(conn, log if log is not None else PgLogTail())
                else:
                    reports = self._mysql(conn)
        except Exception as e:
            logger.info(f"{dialect} | DEADLOCKS | informes no disponibles | {e}")
            self.report.add("DEADLOCK_REPORTS_UNAVAILABLE", section="deadlocks")
            return stats
        stats["deadlocks"] = reports
        if reports:
            logger.info(
                f"{dialect} | DEADLOCKS | {len(reports)} en los ultimos {LOOKBACK_S} s "
                f"({sum(not r['complete'] for r in reports)} sin detalle)"
            )
        return stats

    # ---------------------------------------------------------- postgres

    def _postgres(self, conn, log: PgLogTail) -> list[dict]:
        settings = next(iter(conn.execute(text(PG_SETTINGS_SQL)).mappings()))
        if settings["log_error_verbosity"] == "terse" or settings["log_min_messages"] in _PG_SILENT_LEVELS:
            self.report.add(
                "DEADLOCK_REPORTS_PARTIAL", section="deadlocks",
                log_error_verbosity=settings["log_error_verbosity"],
                log_min_messages=settings["log_min_messages"],
            )
        since = to_naive_utc(settings["now_utc"]) - timedelta(seconds=LOOKBACK_S)

        records = _pg_deadlock_records(log, conn, since)
        if not records:
            return []
        reports = [parse_postgres_deadlock(record) for record in records]

        raw = sorted({report["detected_at"] for report in reports if report["detected_at"]})
        utc = {
            row["raw"]: to_naive_utc(row["utc"])
            for row in conn.execute(PG_TIMESTAMPS_SQL.bindparams(raw=raw)).mappings()
        } if raw else {}
        kept = []
        for report in reports:
            detected_at = utc.get(report["detected_at"])
            if detected_at is None or detected_at < since:
                continue
            report["detected_at"] = _iso(detected_at)
            kept.append(report)

        self._resolve_pg_relations(conn, kept, settings["database_name"])
        # el log se lee del archivo mas nuevo al mas viejo; como en MySQL, por hora
        return sorted(kept, key=lambda report: report["detected_at"])

    def _resolve_pg_relations(self, conn, reports: list[dict], database_name: str) -> None:
        """relation_oid -> schema y tabla, solo para la base conectada: los
        OID de otra base no significan nada aca."""
        local, foreign = [], []
        for report in reports:
            for trx in report["transactions"]:
                lock = trx["waits_for"]
                if lock.get("relation_oid") is not None:
                    (local if report["database_name"] == database_name else foreign).append(lock)
        for lock in foreign:
            lock.pop("relation_oid")
        oids = sorted({lock["relation_oid"] for lock in local})
        names = {
            int(row["oid"]): row
            for row in conn.execute(PG_RELATIONS_SQL.bindparams(oids=oids)).mappings()
        } if oids else {}
        for lock in local:
            name = names.get(lock.pop("relation_oid"))
            if name is not None:
                lock["schema_name"] = name["schema_name"]
                lock["table_name"] = name["table_name"]

    # ------------------------------------------------------------- mysql

    def _mysql(self, conn) -> list[dict]:
        settings = next(iter(conn.execute(text(MYSQL_SETTINGS_SQL)).mappings()))
        now = to_naive_utc(settings["now_utc"])
        since = now - timedelta(seconds=LOOKBACK_S)
        offset = timedelta(seconds=int(settings["system_offset_s"] or 0))

        status = next(iter(conn.execute(text(INNODB_STATUS_SQL)).mappings()), None)
        latest = parse_innodb_status(status["Status"] if status is not None else "")

        headers = self._mysql_error_log(conn, settings)
        reports = []
        if latest is not None:
            detected_at = parse_innodb_timestamp(latest["detected_at"]) - offset
            # el encabezado mas nuevo dentro de la tolerancia es el del ultimo informe
            match = max(
                (h for h in headers if abs(h - detected_at) <= _MATCH_TOLERANCE),
                default=None,
            )
            if match is not None:
                headers.remove(match)
                detected_at = match
            if detected_at >= since:
                latest["detected_at"] = _iso(detected_at)
                reports.append(latest)
        # los demas de la ventana: INNODB STATUS ya los piso con uno mas nuevo
        for header in headers:
            reports.append({
                "detected_at": _iso(header),
                "source": "mysql_error_log",
                "database_name": None,
                "complete": False,
                "transactions": [],
            })
        return sorted(reports, key=lambda report: report["detected_at"])

    def _mysql_error_log(self, conn, settings) -> list[datetime]:
        if not settings["print_all_deadlocks"] or int(settings["log_error_verbosity"] or 0) < 3:
            # Sin esto error_log no recibe los encabezados: solo se ve el
            # ultimo abrazo entre dos ciclos
            self.report.add(
                "DEADLOCK_REPORTS_PARTIAL", section="deadlocks",
                innodb_print_all_deadlocks=int(settings["print_all_deadlocks"] or 0),
                log_error_verbosity=int(settings["log_error_verbosity"] or 0),
            )
            return []
        try:
            return [to_naive_utc(row["logged_at"]) for row in conn.execute(text(MYSQL_ERROR_LOG_SQL)).mappings()]
        except Exception as e:
            # performance_schema.error_log existe desde 8.0.22
            logger.info(f"mysql | DEADLOCKS | error_log no disponible | {e}")
            self.report.add("DEADLOCK_REPORTS_PARTIAL", section="deadlocks")
            return []


def _pg_deadlock_records(log: PgLogTail, conn, since: datetime) -> list[dict]:
    """Un archivo que no cambio desde `since` no puede tener abrazos de la
    ventana: ahi se corta, sin leer los mas viejos."""
    records = []
    for lines in log.files(conn, modified_since=since):
        for line in lines:
            if _PG_DEADLOCK_MARKER not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("state_code") == "40P01":
                records.append(record)
    return records


def _iso(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="microseconds")
