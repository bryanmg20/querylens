from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ActivityBucket,
    DiagnosticsResponse,
    Finding,
    Health,
    HealthIssue,
    LastRun,
)
from app.security import get_database_identifier

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

UNDEFINED_TABLE = "42P01"  # relation does not exist (SQLSTATE)
# Problemas (no detecciones): cada uno es una fila aunque se haya detectado
# miles de veces.
MAX_FINDINGS = 200
ACTIVITY_WINDOW = timedelta(hours=24)

# Un problema por fila con lo de la sesión: cuántas veces, la severidad más
# alta (alta 3, media 2, baja 1) y cuántas de cada una. El total recorre el
# índice (problema_id, detectado_en) de public.detecciones.
SESSION_FINDINGS_SQL = """
WITH s AS (
    SELECT d.problema_id,
           count(*) AS veces_sesion,
           max(CASE d.severidad WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END)
               AS rango_severidad,
           count(*) FILTER (WHERE d.severidad = 'high') AS altas,
           count(*) FILTER (WHERE d.severidad = 'medium') AS medias,
           count(*) FILTER (WHERE d.severidad = 'low') AS bajas,
           max(d.detectado_en) AS ultima
    FROM public.detecciones d
    JOIN public.problemas p ON p.id = d.problema_id
    WHERE p.db_id = :identifier AND d.detectado_en >= :since
    GROUP BY d.problema_id
)
SELECT p.id, p.antipatron,
       CASE s.rango_severidad WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low'
            ELSE p.severidad END AS severidad,
       p.severidad AS severidad_actual,
       p.query_id, p.table_name, p.evidencia, p.explicacion, p.recomendacion,
       s.ultima AS detectado_en,
       p.primera_deteccion,
       s.veces_sesion,
       (SELECT count(*) FROM public.detecciones x WHERE x.problema_id = p.id) AS veces_total,
       s.altas, s.medias, s.bajas
FROM s
JOIN public.problemas p ON p.id = s.problema_id
ORDER BY s.ultima DESC, p.id DESC
LIMIT :limit
"""

ACTIVITY_SQL = """
SELECT date_trunc('hour', d.detectado_en) AS hora, count(*) AS detecciones
FROM public.detecciones d
JOIN public.problemas p ON p.id = d.problema_id
WHERE p.db_id = :identifier AND d.detectado_en >= :since
GROUP BY 1
ORDER BY 1
"""


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    """Ejecuta un SELECT y devuelve las filas como dicts.

    Si la tabla o el schema todavía no existen (volumen viejo, o el servicio
    que la crea aún no arrancó) devuelve [] en vez de fallar con un 500. Se
    hace rollback porque Postgres deja la transacción abortada tras el error.
    """

    try:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]
    except ProgrammingError as exc:
        if getattr(exc.orig, "pgcode", None) != UNDEFINED_TABLE:
            raise
        db.rollback()
        return []


def fetch_session_findings(db: Session, database_identifier: str, since: datetime) -> list[Finding]:
    """Problemas detectados desde `since` (inicio de la sesión de detección),
    del detectado más recientemente al más viejo."""

    rows = _rows(
        db,
        SESSION_FINDINGS_SQL,
        {"identifier": database_identifier, "since": since, "limit": MAX_FINDINGS},
    )
    findings = []
    for row in rows:
        counts = {"high": row.pop("altas"), "medium": row.pop("medias"), "low": row.pop("bajas")}
        findings.append(Finding(**row, por_severidad={k: n for k, n in counts.items() if n}))
    return findings


def fetch_activity(db: Session, database_identifier: str, since: datetime) -> list[ActivityBucket]:
    """Detecciones por hora desde `since` (las horas sin detecciones no vienen)."""

    rows = _rows(db, ACTIVITY_SQL, {"identifier": database_identifier, "since": since})
    return [ActivityBucket(**row) for row in rows]


def fetch_health(db: Session, database_identifier: str, since: datetime) -> Health:
    """Salud del pipeline: último ciclo y problemas (abiertos o resueltos desde `since`)."""

    last_run_rows = _rows(
        db,
        "SELECT status, started_at, finished_at, sections_ok, sections_failed, "
        "       msg_id, engine_version "
        "FROM pipeline_health.last_run WHERE db_id = :identifier",
        {"identifier": database_identifier},
    )
    issue_rows = _rows(
        db,
        "SELECT id, code, category, severity, section, message, remediation, "
        "       params, first_seen, last_seen, occurrences, resolved_at "
        "FROM pipeline_health.issues "
        "WHERE db_id = :identifier AND (resolved_at IS NULL OR resolved_at > :since) "
        "ORDER BY resolved_at IS NOT NULL, "
        "         CASE severity WHEN 'blocking' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END, "
        "         last_seen DESC",
        {"identifier": database_identifier, "since": since},
    )
    return Health(
        last_run=LastRun(**last_run_rows[0]) if last_run_rows else None,
        issues=[HealthIssue(**row) for row in issue_rows],
    )


@router.get("", response_model=DiagnosticsResponse)
def get_diagnostics(
    since_hours: int = Query(
        24, ge=1, le=24 * 15, description="Incluye problemas resueltos en las últimas N horas"
    ),
    database_identifier: str = Depends(get_database_identifier),
    db: Session = Depends(get_db),
) -> DiagnosticsResponse:
    row = (
        db.execute(
            text(
                "SELECT connection_name, engine, is_active, monitoring_enabled, "
                "       detection_session_started_at "
                "FROM registered_databases WHERE database_identifier = :identifier"
            ),
            {"identifier": database_identifier},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La base de datos del token ya no está registrada",
        )

    now = datetime.now(timezone.utc)
    session_start = row["detection_session_started_at"]
    return DiagnosticsResponse(
        database_identifier=database_identifier,
        connection_name=row["connection_name"],
        engine=row["engine"],
        is_active=row["is_active"],
        monitoring_enabled=row["monitoring_enabled"],
        detection_session_started_at=session_start,
        diagnostics=fetch_session_findings(db, database_identifier, since=session_start),
        activity=fetch_activity(db, database_identifier, since=now - ACTIVITY_WINDOW),
        health=fetch_health(db, database_identifier, since=now - timedelta(hours=since_hours)),
        server_time=now,
    )
