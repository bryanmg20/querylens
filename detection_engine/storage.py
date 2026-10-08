import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from models import Hallazgo, StatementHistory
from logger import get_logger

logger = get_logger(__name__)

# DDL en querylens_database/, misma carpeta que init.sql: ahi vive el schema
# de la base compartida de QueryLens.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "querylens_database" / "hallazgos.sql"
_SAMPLES_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "querylens_database" / "statement_samples.sql"


def ensure_hallazgos_table(engine: Engine) -> None:
    # CREATE TABLE IF NOT EXISTS: idempotente, se puede llamar siempre al arrancar
    # sin importar si el contenedor ya la creo antes.
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_findings(engine: Engine, db_id: str | None, findings: list[Hallazgo]) -> None:
    if not findings:
        return

    rows = [
        {
            "db_id": db_id,
            "antipatron": finding.antipatron,
            "severidad": finding.severidad,
            "query_id": str(finding.query_id) if finding.query_id is not None else None,
            "table_name": finding.table_name,
            # default=str por si algun valor no serializable se cuela en evidencia (ej. datetime)
            "evidencia": json.dumps(finding.evidencia, default=str),
            "explicacion": finding.explicacion,
            "recomendacion": finding.recomendacion,
        }
        for finding in findings
    ]

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.hallazgos
                    (db_id, antipatron, severidad, query_id, table_name, evidencia, explicacion, recomendacion)
                VALUES
                    (:db_id, :antipatron, :severidad, :query_id, :table_name,
                     CAST(:evidencia AS JSONB), :explicacion, :recomendacion)
                """
            ),
            rows,
        )

    logger.info("Guardados %d hallazgos en public.hallazgos", len(findings))


def ensure_statement_samples_table(engine: Engine) -> None:
    ddl = _SAMPLES_SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def load_statement_history(engine: Engine, db_id: str) -> dict[str, StatementHistory]:
    # Una fila por query: alcanza con traer todas las de esta base
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT query_id, captured_at, counters_epoch, execution_count,
                       total_time_ms, disk_spill_indicator, window_means_ms, window_ends_at
                FROM public.statement_samples
                WHERE db_id = :db_id
                """
            ),
            {"db_id": db_id},
        ).mappings().all()

    return {
        row["query_id"]: StatementHistory(
            query_id=row["query_id"],
            captured_at=row["captured_at"],
            execution_count=row["execution_count"],
            total_time_ms=row["total_time_ms"],
            disk_spill_indicator=row["disk_spill_indicator"],
            counters_epoch=row["counters_epoch"],
            window_means_ms=list(row["window_means_ms"] or []),
            window_ends_at=list(row["window_ends_at"] or []),
        )
        for row in rows
    }


def save_statement_histories(
    engine: Engine,
    db_id: str,
    captured_at: datetime,
    histories: list[StatementHistory],
    stale_before: datetime,
) -> None:
    rows = [
        {
            "db_id": db_id,
            "query_id": history.query_id,
            "captured_at": history.captured_at,
            "counters_epoch": history.counters_epoch,
            "execution_count": history.execution_count,
            "total_time_ms": history.total_time_ms,
            "disk_spill_indicator": history.disk_spill_indicator,
            "window_means_ms": history.window_means_ms,
            "window_ends_at": history.window_ends_at,
        }
        for history in histories
    ]

    with engine.begin() as conn:
        if rows:
            # el WHERE ignora un reintento de un job que ya se aplico: sin el,
            # la fila retrocederia a contadores viejos
            conn.execute(
                text(
                    """
                    INSERT INTO public.statement_samples
                        (db_id, query_id, captured_at, counters_epoch, execution_count,
                         total_time_ms, disk_spill_indicator, window_means_ms, window_ends_at)
                    VALUES
                        (:db_id, :query_id, :captured_at, :counters_epoch, :execution_count,
                         :total_time_ms, :disk_spill_indicator, :window_means_ms, :window_ends_at)
                    ON CONFLICT (db_id, query_id) DO UPDATE SET
                        captured_at = EXCLUDED.captured_at,
                        counters_epoch = EXCLUDED.counters_epoch,
                        execution_count = EXCLUDED.execution_count,
                        total_time_ms = EXCLUDED.total_time_ms,
                        disk_spill_indicator = EXCLUDED.disk_spill_indicator,
                        window_means_ms = EXCLUDED.window_means_ms,
                        window_ends_at = EXCLUDED.window_ends_at
                    WHERE public.statement_samples.captured_at < EXCLUDED.captured_at
                    """
                ),
                rows,
            )

        # queries que no se ejecutan hace mucho (o cuyo query_id cambio, ej. al
        # recrear la tabla en Postgres): su fila ya no aporta y quedaria huerfana
        deleted = conn.execute(
            text(
                """
                DELETE FROM public.statement_samples
                WHERE db_id = :db_id AND captured_at < :stale_before
                """
            ),
            {"db_id": db_id, "stale_before": stale_before},
        ).rowcount

    logger.info(
        "statement_samples | actualizadas=%d | eliminadas_por_antiguedad=%d | captured_at=%s",
        len(rows), deleted, captured_at,
    )
