import hashlib
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
    # public.problemas, public.detecciones y la vista public.hallazgos (y el
    # renombre de la tabla anterior): idempotente, se puede llamar siempre al
    # arrancar sin importar si db-roles ya lo aplico.
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


# Abrazos mortales que el modulo de contencion identifica con una firma (patron
# + sentencias del ciclo, deadlocks/cycles.deadlock_signature): el mismo abrazo
# que se repite es el mismo problema.
_SIGNED_DEADLOCKS = frozenset({
    "inconsistent_lock_order_deadlock",  # CT-01
    "lock_mode_conversion_deadlock",  # CT-02
    "range_contention_deadlock",  # CT-03
    "lock_amplification_deadlock",  # CT-04
    "unclassified_deadlock",
})

# Patrones cuyo problema es la consulta sola: la tabla del hallazgo sale del
# plan de esa ventana y puede cambiar sin que cambie el problema.
_QUERY_ONLY = frozenset({
    "baseline_degradation",  # AP-01
    "disk_spill",  # AP-07
    "cardinality_misestimate",  # AP-08
})


def _key(*parts: object) -> str:
    return "|".join("" if part is None else str(part) for part in parts)


def _qualified(schema: object, table: object) -> str | None:
    if not table:
        return None
    return f"{schema}.{table}" if schema else str(table)


_NO_TEXT = (None, "", "Not available")


def _query_identity(finding: Hallazgo) -> str:
    """La consulta por su texto normalizado, no por el query_id del motor: en
    Postgres el query_id depende de la tabla interna, asi que borrar y crear la
    tabla (escenarios, campanas, una migracion) le da otro a la misma consulta.
    El texto es el canonico del pipeline o, si no lo hay (AP-01 solo trae
    query_text, ya normalizado por el motor), el del motor. Va como huella
    corta: el texto puede medir KB y la clave esta en un indice unico. Sin
    ningun texto, el query_id."""
    evidence = finding.evidencia or {}
    for text in (evidence.get("canonic_query"), evidence.get("query_text")):
        if text not in _NO_TEXT:
            return "t" + hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:16]
    return _key(finding.query_id)


def finding_key(finding: Hallazgo) -> str:
    """Identidad del problema dentro de su antipatron: las detecciones con la
    misma clave (y el mismo db_id) son el mismo problema y van a la misma fila
    de public.problemas.

    No incluye lo que puede cambiar de una ventana a otra sin que cambie el
    problema: la severidad, la consulta que AP-05 asocia en cada snapshot, ni el
    query_id (la consulta se identifica por su texto, ver _query_identity).
    """
    evidence = finding.evidencia or {}
    antipatron = finding.antipatron

    if antipatron == "unused_index":  # AP-05: el indice, sin la consulta
        return _key(
            _qualified(evidence.get("schema_name"), evidence.get("table_name")),
            evidence.get("index_name"),
            evidence.get("subtipo"),
        )
    if antipatron in _SIGNED_DEADLOCKS and evidence.get("signature"):
        return _key(evidence["signature"])
    if antipatron == "long_transaction_cascade":  # CT-05: una por transaccion
        root = evidence.get("root") or {}
        return _key(root.get("process_id"), root.get("transaction_start_time"))
    query = _query_identity(finding)
    if antipatron in _QUERY_ONLY:
        # el schema separa el mismo texto corrido sobre schemas distintos
        return _key(query, evidence.get("schema_name"))

    table = _qualified(evidence.get("schema_name"), finding.table_name)
    if antipatron == "non_sargable_predicate":  # AP-03: una por columna
        return _key(query, table, evidence.get("column"))
    # AP-02, AP-04, AP-06 y cualquier patron sin clave propia
    return _key(query, table)


# Una deteccion: actualiza (o crea) el problema con lo de esta deteccion y
# anota la deteccion. El ON CONFLICT usa problemas_clave_uq.
_SAVE_FINDING = """
WITH problema AS (
    INSERT INTO public.problemas AS p
        (db_id, antipatron, finding_key, severidad, query_id, table_name, database_name,
         evidencia, explicacion, recomendacion)
    VALUES
        (:db_id, :antipatron, :finding_key, :severidad, :query_id, :table_name, :database_name,
         CAST(:evidencia AS JSONB), :explicacion, :recomendacion)
    ON CONFLICT (db_id, antipatron, finding_key) DO UPDATE SET
        severidad = EXCLUDED.severidad,
        query_id = EXCLUDED.query_id,
        table_name = EXCLUDED.table_name,
        database_name = EXCLUDED.database_name,
        evidencia = EXCLUDED.evidencia,
        explicacion = EXCLUDED.explicacion,
        recomendacion = EXCLUDED.recomendacion,
        ultima_deteccion = now()
    RETURNING p.id
)
INSERT INTO public.detecciones (problema_id, severidad)
SELECT id, :severidad FROM problema
"""


def save_findings(engine: Engine, db_id: str | None, findings: list[Hallazgo]) -> None:
    if not findings:
        return

    rows = [
        {
            "db_id": db_id,
            "antipatron": finding.antipatron,
            "finding_key": finding_key(finding),
            "severidad": finding.severidad,
            "query_id": str(finding.query_id) if finding.query_id is not None else None,
            "table_name": finding.table_name,
            "database_name": finding.database_name,
            # default=str por si algun valor no serializable se cuela en evidencia (ej. datetime)
            "evidencia": json.dumps(finding.evidencia, default=str),
            "explicacion": finding.explicacion,
            "recomendacion": finding.recomendacion,
        }
        for finding in findings
    ]

    with engine.begin() as conn:
        conn.execute(text(_SAVE_FINDING), rows)

    logger.info(
        "Guardadas %d detecciones de %d problemas en public.detecciones",
        len(rows), len({(row["antipatron"], row["finding_key"]) for row in rows}),
    )


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
                       total_time_ms, disk_spill_indicator, sort_merge_passes, rows_returned,
                       window_means_ms, window_ends_at
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
            sort_merge_passes=row["sort_merge_passes"],
            rows_returned=row["rows_returned"],
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
            "sort_merge_passes": history.sort_merge_passes,
            "rows_returned": history.rows_returned,
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
                         total_time_ms, disk_spill_indicator, sort_merge_passes, rows_returned,
                         window_means_ms, window_ends_at)
                    VALUES
                        (:db_id, :query_id, :captured_at, :counters_epoch, :execution_count,
                         :total_time_ms, :disk_spill_indicator, :sort_merge_passes, :rows_returned,
                         :window_means_ms, :window_ends_at)
                    ON CONFLICT (db_id, query_id) DO UPDATE SET
                        captured_at = EXCLUDED.captured_at,
                        counters_epoch = EXCLUDED.counters_epoch,
                        execution_count = EXCLUDED.execution_count,
                        total_time_ms = EXCLUDED.total_time_ms,
                        disk_spill_indicator = EXCLUDED.disk_spill_indicator,
                        sort_merge_passes = EXCLUDED.sort_merge_passes,
                        rows_returned = EXCLUDED.rows_returned,
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
