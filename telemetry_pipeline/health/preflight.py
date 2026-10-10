"""Chequeos de configuracion que no producen error pero degradan el snapshot.

Un error del collect ya se clasifica en CollectStage; aqui se mira lo que
falla en silencio (track_counts apagado, consumers de performance_schema
desactivados, version por debajo del minimo soportado...). Corre como mucho una vez cada
HEALTH_PREFLIGHT_TTL_S por base: entre medio se reinyecta el resultado
cacheado SIN marcar el scope 'preflight', asi que nada se da por resuelto sin
volver a evaluarlo.

Todas las consultas son legibles por un rol sin superusuario. En MySQL quedan
fuera del STATEMENTS_QUERY por su forma (SELECT @@..., performance_schema,
information_schema): el pipeline no se observa a si mismo.
"""
import math
import os
import time
from dataclasses import dataclass, field

from sqlalchemy import text

from logger import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_S = 300.0

PG_SETTINGS = """
SELECT current_setting('server_version_num')::int AS version_num,
       current_setting('server_version') AS version,
       current_setting('track_counts') AS track_counts
"""
PG_STATEMENTS_INFO = """
SELECT dealloc,
       stats_reset > now() - interval '1 hour' AS recently_reset
FROM pg_stat_statements_info
"""
MY_SETTINGS = """
SELECT @@performance_schema AS performance_schema,
       @@version AS version,
       @@performance_schema_max_sql_text_length AS sql_text_limit
"""
MY_CONSUMERS = """
SELECT NAME AS name, ENABLED AS enabled
FROM performance_schema.setup_consumers
WHERE NAME IN ('global_instrumentation', 'thread_instrumentation',
               'statements_digest', 'events_statements_current')
"""
# PROCESS no se chequea aqui leyendo grants (USER_PRIVILEGES no ve los que
# llegan por un ROLE): sin PROCESS la seccion active_queries falla con errno
# 1227 al leer innodb_trx, y classify lo reporta desde el resultado real.

# Minimo soportado (PIPELINE_FLOW, "Versiones soportadas"): STATEMENTS_QUERY
# lee pg_stat_statements.stats_since, que existe desde PG 17.
PG_MIN_VERSION_NUM = 170000


@dataclass
class PreflightResult:
    issues: list[tuple[str, dict]] = field(default_factory=list)
    engine_version: str | None = None
    # Datos que necesitan los chequeos post-collect (health/checks.py).
    facts: dict = field(default_factory=dict)


# clave de la base -> (instante del chequeo, resultado)
_CACHE: dict[str, tuple[float, PreflightResult]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def ttl_from_env() -> float:
    """HEALTH_PREFLIGHT_TTL_S; cualquier valor no usable cae a 300 s."""
    raw = os.getenv("HEALTH_PREFLIGHT_TTL_S", "")
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TTL_S
    if not math.isfinite(value) or value < 0:
        return DEFAULT_TTL_S
    return value


def _first_row(conn, sql):
    """Primera fila o None. Un error se descarta con rollback: el collect que
    sigue usa la misma conexion y en Postgres la transaccion quedaria abortada."""
    try:
        return next(iter(conn.execute(text(sql)).mappings()), None)
    except Exception as e:
        conn.rollback()
        logger.debug(f"health | preflight | {type(e).__name__}")
        return None


def _rows(conn, sql):
    try:
        return [dict(row) for row in conn.execute(text(sql)).mappings()]
    except Exception as e:
        conn.rollback()
        logger.debug(f"health | preflight | {type(e).__name__}")
        return []


def check_postgres(conn, previous: dict | None = None) -> PreflightResult:
    """previous = facts del preflight anterior de esta base (para deltas)."""
    previous = previous or {}
    result = PreflightResult()
    settings = _first_row(conn, PG_SETTINGS)
    if settings is not None:
        result.engine_version = settings.get("version")
        version_num = settings.get("version_num")
        if version_num is not None and int(version_num) < PG_MIN_VERSION_NUM:
            result.issues.append(
                ("PG_VERSION_UNSUPPORTED", {"server_version_num": int(version_num)})
            )
        if settings.get("track_counts") == "off":
            result.issues.append(("TRACK_COUNTS_OFF", {}))

    # Sin la extension esto falla: lo reporta el collect con el SQLSTATE exacto.
    info = _first_row(conn, PG_STATEMENTS_INFO)
    if info is not None:
        # dealloc es acumulado desde el ultimo pg_stat_statements_reset(): un
        # valor > 0 solo dice que alguna vez se descarto algo. Lo que importa es
        # si sigue creciendo entre dos preflights; el primero solo fija la base.
        dealloc = int(info.get("dealloc") or 0)
        result.facts["pg_dealloc"] = dealloc
        before = previous.get("pg_dealloc")
        if before is not None and dealloc > before:
            result.issues.append(("STATEMENTS_EVICTING", {"evicted": dealloc - before}))
        if info.get("recently_reset"):
            result.issues.append(("STATS_RECENTLY_RESET", {}))
    return result


def check_mysql(conn, previous: dict | None = None) -> PreflightResult:
    result = PreflightResult()
    settings = _first_row(conn, MY_SETTINGS)
    if settings is not None:
        result.engine_version = settings.get("version")
        if settings.get("sql_text_limit") is not None:
            result.facts["sql_text_limit"] = int(settings["sql_text_limit"])
        if not int(settings.get("performance_schema") or 0):
            result.issues.append(("PERFORMANCE_SCHEMA_OFF", {}))
            # Sin performance_schema setup_consumers no dice nada util.
            return result

    disabled = sorted(
        str(row.get("name")) for row in _rows(conn, MY_CONSUMERS)
        if str(row.get("enabled")).upper() != "YES"
    )
    if disabled:
        result.issues.append(("PS_CONSUMER_DISABLED", {"consumers": disabled}))
    return result


CHECKS = {"postgres": check_postgres, "mysql": check_mysql}


def _cache_key(collector) -> str:
    engine = getattr(collector, "engine", None)
    url = getattr(engine, "url", None)
    if url is not None and hasattr(url, "render_as_string"):
        return url.render_as_string(hide_password=True)
    return collector.source_dialect


class PreflightStage:
    def __init__(self, collector, report, ttl=None, clock=time.monotonic):
        self.collector = collector
        self.report = report
        self.ttl = ttl
        self.clock = clock

    def execute(self, conn) -> PreflightResult:
        check = CHECKS.get(self.collector.source_dialect)
        if check is None:
            return PreflightResult()

        ttl = self.ttl if self.ttl is not None else ttl_from_env()
        key = _cache_key(self.collector)
        now = self.clock()
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < ttl:
            result = cached[1]
        else:
            result = check(conn, cached[1].facts if cached is not None else None)
            _CACHE[key] = (now, result)
            self.report.checked("preflight")

        for code, params in result.issues:
            self.report.add(code, **params)
        if result.engine_version:
            self.report.engine_version = result.engine_version
        self.report.facts.update(result.facts)
        return result
