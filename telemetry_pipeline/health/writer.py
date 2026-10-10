"""Vuelca un HealthReport al schema pipeline_health de la base de QueryLens.

Nada de aqui puede tumbar el pipeline: perder un registro de salud no es
motivo para no entregar el snapshot. Cada funcion publica atrapa y loguea.
"""
import json
import os
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from health.catalog import CATALOG
from health.report import HealthReport
from logger import get_logger

logger = get_logger(__name__)

DEFAULT_RETENTION_DAYS = 15

LAST_RUN_UPSERT = """
INSERT INTO pipeline_health.last_run
    (db_id, started_at, finished_at, status, sections_ok, sections_failed, msg_id, engine_version)
VALUES
    (:db_id, :started_at, now(), :status, :sections_ok, :sections_failed, :msg_id, :engine_version)
ON CONFLICT (db_id) DO UPDATE SET
    started_at      = EXCLUDED.started_at,
    finished_at     = EXCLUDED.finished_at,
    status          = EXCLUDED.status,
    sections_ok     = EXCLUDED.sections_ok,
    sections_failed = EXCLUDED.sections_failed,
    msg_id          = EXCLUDED.msg_id,
    -- Un ciclo que no llego a conectar no conoce la version: se conserva la ultima.
    engine_version  = COALESCE(EXCLUDED.engine_version, pipeline_health.last_run.engine_version)
"""

ISSUE_UPSERT = """
INSERT INTO pipeline_health.issues
    (db_id, code, scope, category, severity, section, message, remediation, params)
VALUES
    (:db_id, :code, :scope, :category, :severity, :section, :message, :remediation,
     CAST(:params AS JSONB))
ON CONFLICT (db_id, code, section) WHERE resolved_at IS NULL DO UPDATE SET
    last_seen   = now(),
    occurrences = pipeline_health.issues.occurrences + 1,
    scope       = EXCLUDED.scope,
    category    = EXCLUDED.category,
    severity    = EXCLUDED.severity,
    message     = EXCLUDED.message,
    remediation = EXCLUDED.remediation,
    params      = EXCLUDED.params
"""

# Solo se resuelve lo que esta corrida volvio a evaluar (scope = ANY): si la
# conexion cayo, un PG_STATEMENTS_NOT_INSTALLED abierto no se da por arreglado.
RESOLVE_MISSING = """
UPDATE pipeline_health.issues
SET resolved_at = now()
WHERE db_id = :db_id
  AND resolved_at IS NULL
  AND scope = ANY(:scopes)
  AND NOT (code || '/' || section = ANY(:seen))
"""

# Resueltos viejos, y abiertos que nadie vuelve a ver (base desactivada o
# borrada de registered_databases): ambos dejan de servirle al cliente.
PURGE_ISSUES = """
DELETE FROM pipeline_health.issues
WHERE (resolved_at IS NOT NULL AND resolved_at < now() - make_interval(days => :days))
   OR last_seen < now() - make_interval(days => :days)
"""
PURGE_LAST_RUN = """
DELETE FROM pipeline_health.last_run
WHERE finished_at < now() - make_interval(days => :days)
"""

_schema_warned = False


def retention_days() -> int:
    """HEALTH_RETENTION_DAYS; cualquier valor no usable cae a 15."""
    raw = os.getenv("HEALTH_RETENTION_DAYS", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return value if value > 0 else DEFAULT_RETENTION_DAYS


def _handle_error(action: str, db_id: str | None, exc: Exception) -> None:
    """Schema ausente: un warning por proceso (runner cicla cada 10 s). Lo demas: error."""
    global _schema_warned
    if isinstance(exc, ProgrammingError) and getattr(exc.orig, "pgcode", None) in ("42P01", "3F000"):
        if not _schema_warned:
            _schema_warned = True
            logger.warning(
                "health | schema pipeline_health no existe | aplicar "
                "querylens_database/pipeline_health.sql; no se registra la salud"
            )
        return
    label = f" | db_id={db_id}" if db_id else ""
    logger.error(f"health | {action}{label} | {type(exc).__name__}: {str(exc)[:200]}")


def _issue_rows(db_id: str, report: HealthReport) -> list[dict]:
    rows = []
    for (code, section), params in report.issues.items():
        definition = CATALOG[code]
        rows.append({
            "db_id": db_id,
            "code": code,
            "scope": definition.scope,
            "category": definition.category,
            "severity": definition.severity,
            "section": section,
            "message": definition.message,
            "remediation": definition.remediation,
            "params": json.dumps(params, sort_keys=True, default=str),
        })
    return rows


def flush(engine, db_id: str, report: HealthReport, started_at: datetime) -> None:
    """last_run + upsert de issues + resolucion, en una sola transaccion."""
    try:
        with engine.begin() as conn:
            conn.execute(text(LAST_RUN_UPSERT), {
                "db_id": db_id,
                "started_at": started_at,
                "status": report.status(),
                "sections_ok": list(report.sections_ok),
                "sections_failed": list(report.sections_failed),
                "msg_id": report.msg_id,
                "engine_version": report.engine_version,
            })
            rows = _issue_rows(db_id, report)
            if rows:
                conn.execute(text(ISSUE_UPSERT), rows)
            if report.scopes:
                conn.execute(text(RESOLVE_MISSING), {
                    "db_id": db_id,
                    "scopes": sorted(report.scopes),
                    "seen": [f"{code}/{section}" for code, section in report.issues],
                })
    except Exception as exc:
        _handle_error("flush", db_id, exc)


def record_target_issue(engine, db_id: str, code: str, **params) -> None:
    """Falla antes de poder correr (credencial o config de la fila registrada)."""
    report = HealthReport()
    report.add(code, **params)
    report.checked("credentials")
    flush(engine, db_id, report, datetime.now(timezone.utc))


def purge(engine, days: int | None = None) -> None:
    """Borra lo vencido segun la retencion (15 dias por defecto)."""
    days = days if days is not None else retention_days()
    try:
        with engine.begin() as conn:
            issues = conn.execute(text(PURGE_ISSUES), {"days": days}).rowcount
            runs = conn.execute(text(PURGE_LAST_RUN), {"days": days}).rowcount
        logger.debug(f"health | purge | {issues} issue(s), {runs} last_run(s) | > {days} dias")
    except Exception as exc:
        _handle_error("purge", None, exc)
