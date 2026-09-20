import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from models import Hallazgo
from logger import get_logger

logger = get_logger(__name__)

# DDL en querylens_database/, misma carpeta que init.sql: ahi vive el schema
# de la base compartida de QueryLens.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "querylens_database" / "hallazgos.sql"


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
