"""Vuelca un HealthReport al schema pipeline_health de la base de QueryLens.

Nada de aqui puede tumbar el pipeline: perder un registro de salud no es
motivo para no entregar el snapshot. Cada funcion publica atrapa y loguea.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from health import cycle_sample
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
PURGE_SAMPLES = """
DELETE FROM pipeline_health.cycle_samples
WHERE taken_at < now() - make_interval(days => :days)
"""

# La muestra anterior de la base, contra la que se calculan las diferencias.
PREV_SAMPLE_SELECT = """
SELECT taken_at, engine_deadlocks, counter_epoch, log_files
FROM pipeline_health.cycle_samples
WHERE db_id = :db_id AND taken_at < :taken_at
ORDER BY taken_at DESC
LIMIT 1
"""

CYCLE_SAMPLE_INSERT = """
INSERT INTO pipeline_health.cycle_samples
    (db_id, taken_at, engine_deadlocks, counter_epoch, log_files,
     deadlocks_since_prev, counter_reset, log_bytes_since_prev,
     seconds_since_prev, log_window_overrun)
VALUES
    (:db_id, :taken_at, :engine_deadlocks, :counter_epoch, CAST(:log_files AS JSONB),
     :deadlocks_since_prev, :counter_reset, :log_bytes_since_prev,
     :seconds_since_prev, :log_window_overrun)
ON CONFLICT (db_id, taken_at) DO NOTHING
"""

# Abrazos que el motor conto y QueryLens no capturo, en la ultima hora: lo
# mismo que calidad_deadlocks del reporte de la CLI (cli/share/report.sql). La
# ventana termina MISSED_GRACE_S antes de la muestra, porque el detector guarda
# el evento despues, por la cola. Los eventos se cortan por las muestras: el
# delta de una cubre desde la anterior, asi que entran los de entre la muestra
# previa a la primera de la ventana y la ultima.
MISSED_WINDOW_S = 3600
MISSED_GRACE_S = 120

MISSED_DEADLOCKS_SELECT = """
WITH s AS (
    SELECT taken_at, deadlocks_since_prev, coalesce(seconds_since_prev, 0) AS seconds_since_prev
    FROM pipeline_health.cycle_samples
    WHERE db_id = :db_id
      AND taken_at >= :since
      AND taken_at < :until
      AND deadlocks_since_prev IS NOT NULL
)
SELECT (SELECT sum(deadlocks_since_prev) FROM s) AS counted,
       (SELECT count(*)
        FROM public.deadlock_events e
        WHERE e.db_id = :db_id
          AND e.detected_at > (SELECT min(taken_at - make_interval(secs => seconds_since_prev)) FROM s)
          AND e.detected_at <= (SELECT max(taken_at) FROM s)) AS captured
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
    """Schema o tabla ausente (p. ej. cycle_samples en una base sin migrar): un
    warning por proceso (runner cicla cada 10 s). Lo demas: error."""
    global _schema_warned
    if isinstance(exc, ProgrammingError) and getattr(exc.orig, "pgcode", None) in ("42P01", "3F000"):
        if not _schema_warned:
            _schema_warned = True
            logger.warning(
                "health | falta el schema pipeline_health o una de sus tablas | aplicar "
                "querylens_database/pipeline_health.sql; lo que dependa de ella no se registra"
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


def _record_sample(engine, db_id: str, report: HealthReport) -> bool:
    """Guarda la muestra del ciclo con sus diferencias contra la anterior. En
    su propia transaccion, antes que last_run: si la tabla falta (base sin
    migrar), last_run e issues se escriben igual. Un desborde de la ventana del
    log queda ademas como issue de este mismo flush. True si se guardo."""
    sample = report.cycle_sample
    if sample is None:
        return False
    try:
        with engine.begin() as conn:
            prev = conn.execute(text(PREV_SAMPLE_SELECT), {
                "db_id": db_id, "taken_at": sample["taken_at"],
            }).mappings().first()
            derived = cycle_sample.derive(dict(prev) if prev is not None else None, sample)
            conn.execute(text(CYCLE_SAMPLE_INSERT), {
                "db_id": db_id,
                "taken_at": sample["taken_at"],
                "engine_deadlocks": sample["engine_deadlocks"],
                "counter_epoch": sample["counter_epoch"],
                "log_files": json.dumps(sample["log_files"]) if sample["log_files"] is not None else None,
                **derived,
            })
    except Exception as exc:
        _handle_error("sample", db_id, exc)
        return False
    if derived["log_window_overrun"]:
        report.add(
            "LOG_WINDOW_OVERRUN", section="deadlocks",
            log_bytes=derived["log_bytes_since_prev"],
            seconds=round(derived["seconds_since_prev"], 1),
            window_bytes=cycle_sample.LOG_WINDOW_BYTES,
        )
    return True


def _check_missed(engine, db_id: str, report: HealthReport) -> None:
    """DEADLOCKS_MISSED si en la ventana el motor conto mas abrazos que los
    eventos que guardo el detector. Solo tras guardar la muestra del ciclo; si
    la comparacion no se pudo hacer, el scope capture no se evalua y un issue
    abierto sigue abierto."""
    until = report.cycle_sample["taken_at"] - timedelta(seconds=MISSED_GRACE_S)
    try:
        with engine.begin() as conn:
            row = conn.execute(text(MISSED_DEADLOCKS_SELECT), {
                "db_id": db_id, "since": until - timedelta(seconds=MISSED_WINDOW_S), "until": until,
            }).mappings().first()
    except Exception as exc:
        # deadlock_events la crea el detector al arrancar, y el pipeline la
        # lee con el GRANT de service_roles.sql: sin una de las dos, la
        # comparacion no corre pero el resto del flush si
        logger.warning(f"health | missed | db_id={db_id} | {type(exc).__name__}: {str(exc)[:200]}")
        return
    report.checked("capture")
    if row is None or row["counted"] is None:
        # ningun ciclo de la ventana pudo comparar el contador con el anterior
        return
    counted, captured = int(row["counted"]), int(row["captured"] or 0)
    if counted > captured:
        report.add(
            "DEADLOCKS_MISSED", section="deadlocks",
            missed=counted - captured, counted=counted, captured=captured, window_s=MISSED_WINDOW_S,
        )


def flush(engine, db_id: str, report: HealthReport, started_at: datetime) -> None:
    """Muestra del ciclo, y last_run + upsert de issues + resolucion en una
    sola transaccion."""
    if _record_sample(engine, db_id, report):
        _check_missed(engine, db_id, report)
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
    # Aparte: sin cycle_samples (base sin migrar) lo de arriba se purga igual
    try:
        with engine.begin() as conn:
            samples = conn.execute(text(PURGE_SAMPLES), {"days": days}).rowcount
        logger.debug(f"health | purge | {samples} muestra(s) | > {days} dias")
    except Exception as exc:
        _handle_error("purge", None, exc)
