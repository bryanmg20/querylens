from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Finding(BaseModel):
    """Hallazgo (antipatrón) detectado por el detection_engine."""

    id: int
    antipatron: str
    severidad: str
    query_id: str | None
    table_name: str | None
    evidencia: dict[str, Any]
    explicacion: str
    recomendacion: str
    detectado_en: datetime


class LastRun(BaseModel):
    """Resultado del último ciclo de extracción del pipeline para esta base."""

    status: str  # ok | degraded | failed
    started_at: datetime
    finished_at: datetime
    sections_ok: list[str]
    sections_failed: list[str]
    msg_id: int | None
    engine_version: str | None


class HealthIssue(BaseModel):
    """Problema de extracción de la base del cliente. message y remediation
    ya vienen redactados en español desde el pipeline: se muestran tal cual."""

    id: int
    code: str
    category: str
    severity: str  # blocking | degraded | info
    section: str
    message: str
    remediation: str
    params: dict[str, Any]
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    resolved_at: datetime | None


class Health(BaseModel):
    # None cuando el pipeline todavía no corrió contra esta base.
    last_run: LastRun | None
    issues: list[HealthIssue]


class DiagnosticsResponse(BaseModel):
    database_identifier: str
    connection_name: str
    engine: str
    is_active: bool
    # False cuando el usuario pauso el monitoreo desde la pagina de diagnosticos.
    monitoring_enabled: bool
    # Hallazgos más recientes del detection_engine, del más nuevo al más viejo.
    diagnostics: list[Finding]
    health: Health
    server_time: datetime
