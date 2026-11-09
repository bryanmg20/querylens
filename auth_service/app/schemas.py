from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ConnectionParams(BaseModel):
    """Datos de conexion enviados por el formulario del frontend."""

    engine: Literal["postgresql", "mysql"] = "postgresql"
    host: str = Field(min_length=1)
    port: int
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    # Obligatorio en PostgreSQL (una conexion apunta a una base). En MySQL no se usa: el
    # pipeline observa todo el servidor, asi que se normaliza a "" y no afecta la huella.
    database_name: str = ""

    @model_validator(mode="after")
    def _database_name_por_motor(self) -> "ConnectionParams":
        if self.engine == "mysql":
            self.database_name = ""
        elif not self.database_name.strip():
            raise ValueError("El nombre de la base de datos es obligatorio en PostgreSQL")
        return self


class TestConnectionRequest(ConnectionParams):
    pass


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[float] = None


class DatabaseCreateRequest(ConnectionParams):
    connection_name: str = Field(min_length=1, max_length=255)


class DatabaseRegisteredResponse(BaseModel):
    id: UUID
    database_identifier: str
    connection_name: str
    engine: str
    host: str
    port: int
    database_name: str
    is_active: bool
    monitoring_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DatabaseSessionResponse(DatabaseRegisteredResponse):
    """Respuesta al iniciar sesión: la base de datos más su access token."""

    access_token: str
    token_type: str = "bearer"


class MonitoringUpdateRequest(BaseModel):
    enabled: bool
    # Solo al iniciar o reanudar: empezar una sesión de detección nueva.
    new_session: bool = False


class MonitoringResponse(BaseModel):
    database_identifier: str
    monitoring_enabled: bool
    detection_session_started_at: datetime
