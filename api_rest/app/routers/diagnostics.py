from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DiagnosticsResponse
from app.security import get_database_identifier

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def fetch_latest_diagnostics(db: Session, database_identifier: str) -> list[dict[str, Any]]:
    """Últimos diagnósticos de la base de datos `database_identifier`.

    PENDIENTE: conectar con la tabla de diagnósticos que escribe el pipeline
    (consultarla filtrando por database_identifier y devolver las filas más
    recientes como dicts). Mientras tanto devuelve una lista vacía.
    """

    return []


@router.get("", response_model=DiagnosticsResponse)
def get_diagnostics(
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

    return DiagnosticsResponse(
        database_identifier=database_identifier,
        connection_name=row["connection_name"],
        engine=row["engine"],
        is_active=row["is_active"],
        diagnostics=fetch_latest_diagnostics(db, database_identifier),
        server_time=datetime.now(timezone.utc),
    )
