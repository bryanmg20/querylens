from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ActivityBucket,
    DiagnosticsResponse,
    EvaluationReason,
    Finding,
    Health,
    HealthIssue,
    LastRun,
    PathologyEvaluation,
)
from app.security import get_database_identifier

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

UNDEFINED_TABLE = "42P01"  # relation does not exist (SQLSTATE)
# Problemas (no detecciones): cada uno es una fila aunque se haya detectado
# miles de veces.
MAX_FINDINGS = 200
ACTIVITY_WINDOW = timedelta(hours=24)

# Un problema por fila con lo de la sesión: cuántas veces, la severidad más
# alta (alta 3, media 2, baja 1), cuántas de cada una y el impacto acumulado.
# El total recorre el índice (problema_id, detectado_en) de public.detecciones.
#
# Orden por impacto (SegundoInforme 6.2, issue #51): de mayor a menor impacto
# de la sesión (la suma del de cada detección: el tiempo de base de datos que
# el problema costó desde que empezó la sesión); los empates, por la severidad
# más alta y después por el código de la patología. Es la misma regla que
# detection_engine/prioritization.py: un impacto sin medir (abrazos mortales)
# cuenta como 0, un código fuera del catálogo va al final, y la clave del
# problema deja el orden fijo cuando todo lo demás empata.
SESSION_FINDINGS_SQL = """
WITH s AS (
    SELECT d.problema_id,
           count(*) AS veces_sesion,
           sum(d.impacto_ms) AS impacto_sesion_ms,
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
SELECT p.id, p.antipatron, p.codigo,
       CASE s.rango_severidad WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low'
            ELSE p.severidad END AS severidad,
       p.severidad AS severidad_actual,
       p.query_id, p.table_name, p.database_name, p.evidencia, p.explicacion, p.recomendacion,
       p.riesgo,
       p.impacto_ms,
       s.impacto_sesion_ms,
       s.ultima AS detectado_en,
       p.primera_deteccion,
       s.veces_sesion,
       (SELECT count(*) FROM public.detecciones x WHERE x.problema_id = p.id) AS veces_total,
       s.altas, s.medias, s.bajas
FROM s
JOIN public.problemas p ON p.id = s.problema_id
ORDER BY coalesce(s.impacto_sesion_ms, 0) DESC,
         s.rango_severidad DESC,
         p.codigo NULLS LAST,
         p.finding_key,
         p.id
LIMIT :limit
"""

# Estado de evaluación por patología (SegundoInforme 6.1 y 6.3, issue #52).
# El detector deja una fila por patología, tramo de 10 minutos, estado y
# motivos (public.estado_evaluacion). Por patología: el estado de la última
# ventana procesada y cuántas ventanas de la sesión quedaron en cada estado.
EVALUATION_SQL = """
WITH ultima AS (
    SELECT DISTINCT ON (codigo)
           codigo, antipatron, estado, motivos, detalle, hasta
    FROM public.estado_evaluacion
    WHERE db_id = :identifier
    ORDER BY codigo, hasta DESC
), sesion AS (
    SELECT codigo, estado, sum(ventanas) AS ventanas
    FROM public.estado_evaluacion
    WHERE db_id = :identifier AND hasta >= :since
    GROUP BY codigo, estado
)
SELECT u.codigo, u.antipatron, u.estado, u.motivos, u.detalle, u.hasta AS actualizado_en,
       coalesce((SELECT json_object_agg(estado, ventanas) FROM sesion WHERE sesion.codigo = u.codigo),
                '{}'::json) AS ventanas_sesion
FROM ultima u
ORDER BY u.codigo
"""

# Patologías que dependen de la semántica de bloqueos de InnoDB (Tabla 3): en
# PostgreSQL no aplican. El detector las evalúa igual en los dos motores (no
# distingue el motor); quien muestra el estado sabe con qué motor se registró
# la base.
MYSQL_ONLY = frozenset({"CT-03", "CT-04"})

# Las 13 patologías (detection_engine/catalog.py), en su orden. Solo para el
# caso en que el detector todavía no dejó ningún estado y la extracción está
# fallando: sin snapshot no corre, y sin esto la lista vendría vacía en vez de
# decir que ninguna se pudo evaluar y por qué.
CATALOG = (
    ("AP-01", "baseline_degradation"),
    ("AP-02", "avoidable_full_scan"),
    ("AP-03", "non_sargable_predicate"),
    ("AP-04", "missing_index"),
    ("AP-05", "unused_index"),
    ("AP-06", "n_plus_one"),
    ("AP-07", "disk_spill"),
    ("AP-08", "cardinality_misestimate"),
    ("CT-01", "inconsistent_lock_order_deadlock"),
    ("CT-02", "lock_mode_conversion_deadlock"),
    ("CT-03", "range_contention_deadlock"),
    ("CT-04", "lock_amplification_deadlock"),
    ("CT-05", "long_transaction_cascade"),
)

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
    ordenados por impacto (ver SESSION_FINDINGS_SQL)."""

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


def fetch_evaluation(
    db: Session, database_identifier: str, since: datetime, engine: str, health: Health,
) -> list[PathologyEvaluation]:
    """Estado de evaluación de cada patología del catálogo, en su orden.

    Si el último ciclo del pipeline falló por un problema bloqueante, no hubo
    snapshot y el detector no corrió: las patologías cuyo estado es anterior a
    ese ciclo quedan como no evaluadas, con los problemas bloqueantes abiertos
    como motivo (todas, si el detector nunca dejó un estado). Sin filas y sin
    ese fallo (el detector todavía no procesó un snapshot) la lista viene
    vacía."""

    rows = _rows(db, EVALUATION_SQL, {"identifier": database_identifier, "since": since})
    blocking = [
        EvaluationReason(
            codigo=issue.code, origen="recoleccion", severidad=issue.severity, seccion=issue.section,
            mensaje=issue.message, accion=issue.remediation,
        )
        for issue in health.issues
        if issue.resolved_at is None and issue.severity == "blocking"
    ]
    last_run = health.last_run
    failed = last_run is not None and last_run.status == "failed" and bool(blocking)
    if failed and not rows:
        rows = [
            {"codigo": codigo, "antipatron": antipatron, "estado": "no_evaluada", "motivos": [], "detalle": {},
             "actualizado_en": last_run.finished_at, "ventanas_sesion": {}}
            for codigo, antipatron in CATALOG
        ]

    evaluations = []
    for row in rows:
        evaluation = PathologyEvaluation(
            **row, aplica=not (engine == "postgresql" and row["codigo"] in MYSQL_ONLY),
        )
        if failed and evaluation.actualizado_en <= last_run.finished_at:
            evaluation.estado = "no_evaluada"
            evaluation.motivos = blocking
            evaluation.actualizado_en = last_run.finished_at
        evaluations.append(evaluation)
    return evaluations


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
    health = fetch_health(db, database_identifier, since=now - timedelta(hours=since_hours))
    return DiagnosticsResponse(
        database_identifier=database_identifier,
        connection_name=row["connection_name"],
        engine=row["engine"],
        is_active=row["is_active"],
        monitoring_enabled=row["monitoring_enabled"],
        detection_session_started_at=session_start,
        diagnostics=fetch_session_findings(db, database_identifier, since=session_start),
        evaluation=fetch_evaluation(db, database_identifier, session_start, row["engine"], health),
        activity=fetch_activity(db, database_identifier, since=now - ACTIVITY_WINDOW),
        health=health,
        server_time=now,
    )
