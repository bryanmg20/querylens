import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from models import Hallazgo, StatementHistory, StatementSample
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


def load_statement_history(
    engine: Engine,
    db_id: str | None,
    captured_at: datetime,
    baseline_window: int,
    min_calls: int,
) -> dict[str, StatementHistory]:
    # Solo samples anteriores a captured_at: si el job se reintenta despues de
    # haber guardado sus samples, no se compara contra si mismo.
    params = {"db_id": db_id, "captured_at": captured_at}
    with engine.begin() as conn:
        last_rows = conn.execute(
            text(
                """
                SELECT DISTINCT ON (query_id)
                    query_id, execution_count, total_time_ms, stats_reset
                FROM public.statement_samples
                WHERE db_id IS NOT DISTINCT FROM :db_id
                  AND captured_at < :captured_at
                ORDER BY query_id, captured_at DESC, id DESC
                """
            ),
            params,
        ).mappings().all()

        # los intervalos con pocas ejecuciones no entran a la linea base (mismo
        # criterio min_calls que se exige al intervalo actual)
        interval_rows = conn.execute(
            text(
                """
                SELECT query_id, interval_mean_ms
                FROM (
                    SELECT
                        query_id, interval_mean_ms, captured_at, id,
                        ROW_NUMBER() OVER (
                            PARTITION BY query_id ORDER BY captured_at DESC, id DESC
                        ) AS rn
                    FROM public.statement_samples
                    WHERE db_id IS NOT DISTINCT FROM :db_id
                      AND captured_at < :captured_at
                      AND interval_mean_ms IS NOT NULL
                      AND interval_calls >= :min_calls
                ) recent
                WHERE rn <= :baseline_window
                ORDER BY query_id, captured_at, id
                """
            ),
            {**params, "min_calls": min_calls, "baseline_window": baseline_window},
        ).mappings().all()

    history: dict[str, StatementHistory] = {}
    for row in last_rows:
        history[row["query_id"]] = StatementHistory(
            last_sample=StatementSample(
                query_id=row["query_id"],
                execution_count=row["execution_count"],
                total_time_ms=row["total_time_ms"],
                stats_reset=row["stats_reset"],
            )
        )
    for row in interval_rows:
        history.setdefault(row["query_id"], StatementHistory()).recent_interval_means.append(
            row["interval_mean_ms"]
        )
    return history


def save_statement_samples(
    engine: Engine,
    db_id: str | None,
    captured_at: datetime,
    samples: list[StatementSample],
) -> None:
    if not samples:
        return

    rows = [
        {
            "db_id": db_id,
            "query_id": sample.query_id,
            "captured_at": captured_at,
            "stats_reset": sample.stats_reset,
            "execution_count": sample.execution_count,
            "total_time_ms": sample.total_time_ms,
            "interval_calls": sample.interval_calls,
            "interval_mean_ms": sample.interval_mean_ms,
        }
        for sample in samples
    ]

    with engine.begin() as conn:
        # un reintento del mismo job reemplaza sus samples en vez de duplicarlos
        conn.execute(
            text(
                """
                DELETE FROM public.statement_samples
                WHERE db_id IS NOT DISTINCT FROM :db_id AND captured_at = :captured_at
                """
            ),
            {"db_id": db_id, "captured_at": captured_at},
        )
        conn.execute(
            text(
                """
                INSERT INTO public.statement_samples
                    (db_id, query_id, captured_at, stats_reset, execution_count,
                     total_time_ms, interval_calls, interval_mean_ms)
                VALUES
                    (:db_id, :query_id, :captured_at, :stats_reset, :execution_count,
                     :total_time_ms, :interval_calls, :interval_mean_ms)
                """
            ),
            rows,
        )

    logger.info("Guardados %d samples en public.statement_samples", len(samples))
