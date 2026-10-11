from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DiagnosticsResponse, Finding, Health, HealthIssue, LastRun
from app.security import get_database_identifier

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

UNDEFINED_TABLE = "42P01"  # relation does not exist (SQLSTATE)
MAX_FINDINGS = 50


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


def fetch_latest_findings(db: Session, database_identifier: str) -> list[Finding]:
    """Hallazgos del detection_engine para esta base, del más reciente al más viejo."""

    rows = _rows(
        db,
        "SELECT id, antipatron, severidad, query_id, table_name, evidencia, "
        "       explicacion, recomendacion, detectado_en "
        "FROM public.hallazgos WHERE db_id = :identifier "
        "ORDER BY detectado_en DESC, id DESC LIMIT :limit",
        {"identifier": database_identifier, "limit": MAX_FINDINGS},
    )
    return [Finding(**row) for row in rows]


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
                "SELECT connection_name, engine, is_active "
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
    return DiagnosticsResponse(
        database_identifier=database_identifier,
        connection_name=row["connection_name"],
        engine=row["engine"],
        is_active=row["is_active"],
        diagnostics=fetch_latest_findings(db, database_identifier),
        health=fetch_health(db, database_identifier, since=now - timedelta(hours=since_hours)),
        server_time=now,
    )
