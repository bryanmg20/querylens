from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Finding(BaseModel):
    """Un problema detectado por el detection_engine (public.problemas) con lo
    que pasó en la sesión de detección actual. La evidencia, la explicación y
    la recomendación son las de su última detección."""

    id: int
    antipatron: str
    # Código de la patología (AP-01 a AP-08, CT-01 a CT-05; "CT" en un abrazo
    # sin clasificar). None en problemas guardados antes de la columna.
    codigo: str | None = None
    # La más alta que tuvo en la sesión: un pico no queda escondido y la lista
    # no cambia de orden en cada actualización.
    severidad: str
    # La de la última detección.
    severidad_actual: str
    query_id: str | None
    table_name: str | None
    # Base donde ocurrió (en MySQL, el schema). None si el detector no la sabe.
    database_name: str | None = None
    evidencia: dict[str, Any]
    explicacion: str
    recomendacion: str
    # Lo que puede pasar al aplicar la recomendación (no la gravedad del
    # problema). None en problemas no detectados desde que existe el campo.
    riesgo: str | None = None
    # Costo del problema en milisegundos (SegundoInforme 6.2): tiempo de base
    # de datos de su sentencia o espera acumulada en contención. impacto_ms es
    # el de la última detección (su ventana); impacto_sesion_ms, la suma de
    # las detecciones de la sesión, que es el criterio de orden de la lista.
    # None si no se pudo medir (abrazos mortales).
    impacto_ms: float | None = None
    impacto_sesion_ms: float | None = None
    # Última detección.
    detectado_en: datetime
    # Primera detección, en todo el histórico.
    primera_deteccion: datetime
    veces_sesion: int
    veces_total: int
    # Detecciones de la sesión por severidad (solo las que tuvo).
    por_severidad: dict[str, int]


class EvaluationReason(BaseModel):
    """Por qué una patología no se evaluó (o se evaluó en parte) y qué hacer.
    origen: recoleccion (un problema del estado de la recolección, con su
    acción de remedio) o detector (una condición de la propia regla)."""

    codigo: str
    origen: str
    severidad: str | None = None
    seccion: str = ""
    mensaje: str
    accion: str


class PathologyEvaluation(BaseModel):
    """Estado de evaluación de una patología (SegundoInforme 6.1 y 6.3):
    distingue "sin hallazgos" de "no evaluada"."""

    codigo: str
    antipatron: str
    # evaluada | parcial | no_evaluada, en la última ventana procesada
    estado: str
    motivos: list[EvaluationReason]
    # Lo que la regla tuvo para mirar en esa ventana (conteos).
    detalle: dict[str, Any]
    # Hora de la última ventana procesada.
    actualizado_en: datetime
    # Ventanas de la sesión de detección en cada estado.
    ventanas_sesion: dict[str, int]
    # False en CT-03 y CT-04 sobre PostgreSQL: dependen de InnoDB (Tabla 3).
    aplica: bool = True


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
    # Problemas con al menos una detección en la sesión, ordenados por impacto
    # (SegundoInforme 6.2).
    diagnostics: list[Finding]
    # Estado de evaluación de cada patología, en el orden del catálogo. Vacío
    # si el detector todavía no procesó ningún snapshot de esta base.
    evaluation: list[PathologyEvaluation]
    # Detecciones por hora de las últimas 24 h (solo las horas que tuvieron).
    activity: list[ActivityBucket]
    health: Health
    server_time: datetime
