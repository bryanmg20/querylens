from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Finding(BaseModel):
    """Un problema detectado por el detection_engine (public.problemas) con lo
    que pasó en la sesión de detección actual. La evidencia, la explicación y
    la recomendación son las de su última detección."""

    id: int
    antipatron: str
    # La más alta que tuvo en la sesión: un pico no queda escondido y la lista
    # no cambia de orden en cada actualización.
    severidad: str
    # La de la última detección.
    severidad_actual: str
    query_id: str | None
    table_name: str | None
    evidencia: dict[str, Any]
    explicacion: str
    recomendacion: str
    # Última detección.
    detectado_en: datetime
    # Primera detección, en todo el histórico.
    primera_deteccion: datetime
    veces_sesion: int
    veces_total: int
    # Detecciones de la sesión por severidad (solo las que tuvo).
    por_severidad: dict[str, int]


class ActivityBucket(BaseModel):
    """Detecciones de una hora (inicio de la hora, en UTC)."""

    hora: datetime
    detecciones: int


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
    # Inicio de la sesión de detección: diagnostics trae solo los problemas
    # detectados desde entonces.
    detection_session_started_at: datetime
    # Problemas con al menos una detección en la sesión, del detectado más
    # recientemente al más viejo.
    diagnostics: list[Finding]
    # Detecciones por hora de las últimas 24 h (solo las horas que tuvieron).
    activity: list[ActivityBucket]
    health: Health
    server_time: datetime
