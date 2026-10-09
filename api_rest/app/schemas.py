from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DiagnosticsResponse(BaseModel):
    database_identifier: str
    connection_name: str
    engine: str
    is_active: bool
    # Últimos diagnósticos (patrones) de esta base de datos. Vacío hasta que
    # se conecte con la tabla que escribe el pipeline.
    diagnostics: list[dict[str, Any]]
    server_time: datetime
