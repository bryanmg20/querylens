from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ConnectionParams(BaseModel):
    """Datos de conexion enviados por el formulario del frontend."""

    engine: Literal["postgresql", "mysql"] = "postgresql"
    host: str = Field(min_length=1)
    port: int
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    database_name: str = Field(min_length=1)


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
    created_at: datetime

    model_config = {"from_attributes": True}
