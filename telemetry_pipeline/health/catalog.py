"""Catalogo de problemas que el pipeline le reporta al cliente.

El code es el contrato estable con la API REST; message y remediation viajan
ya redactados en cada fila de pipeline_health.issues, asi que cambiar un texto
aqui se refleja en el siguiente ciclo sin migrar datos.

scope dice que parte del pipeline evalua el problema: un issue abierto solo se
marca resuelto en un ciclo que volvio a evaluar su scope (writer.flush).
"""
from dataclasses import dataclass

BLOCKING = "blocking"
DEGRADED = "degraded"
INFO = "info"


@dataclass(frozen=True)
class IssueDef:
    scope: str
    category: str
    severity: str
    message: str
    remediation: str


CATALOG: dict[str, IssueDef] = {
    # ---------- conexion y credenciales ----------
    "AUTH_FAILED": IssueDef(
        "connection", "connection", BLOCKING,
        "La base rechazo el usuario o la contrasena registrados.",
        "Verifica el usuario y la contrasena de la conexion y vuelve a registrarla.",
    ),
    "HOST_UNREACHABLE": IssueDef(
        "connection", "connection", BLOCKING,
        "No se pudo llegar al host de la base.",
        "Revisa el host y el puerto, que el servidor este encendido y que el "
        "firewall permita conexiones desde QueryLens.",
    ),
    "CONNECT_TIMEOUT": IssueDef(
        "connection", "connection", BLOCKING,
        "La conexion a la base supero el tiempo de espera.",
        "Revisa la conectividad de red entre QueryLens y la base, y la carga del servidor.",
    ),
    "DATABASE_NOT_FOUND": IssueDef(
        "connection", "connection", BLOCKING,
        "La base de datos registrada no existe en el servidor.",
        "Corrige el nombre de la base en la conexion registrada.",
    ),
    "TOO_MANY_CONNECTIONS": IssueDef(
        "connection", "connection", BLOCKING,
        "El servidor no acepta mas conexiones.",
        "Libera conexiones o sube max_connections en el servidor.",
    ),
    "CONNECTION_FAILED": IssueDef(
        "connection", "connection", BLOCKING,
        "No se pudo abrir la conexion a la base.",
        "Revisa los datos de la conexion registrada; si persiste, contacta a soporte.",
    ),
    "CREDENTIALS_UNREADABLE": IssueDef(
        "credentials", "internal", BLOCKING,
        "QueryLens no pudo leer las credenciales guardadas de esta conexion.",
        "Vuelve a registrar la conexion; si persiste, contacta a soporte.",
    ),
    "TARGET_CONFIG_INVALID": IssueDef(
        "credentials", "config", BLOCKING,
        "La conexion registrada tiene un motor o un puerto invalido.",
        "Vuelve a registrar la conexion con un motor soportado y un puerto numerico.",
    ),
    # ---------- Postgres ----------
    "PG_STATEMENTS_NOT_INSTALLED": IssueDef(
        "collect", "extension", BLOCKING,
        "La extension pg_stat_statements no esta creada en la base.",
        "Ejecuta CREATE EXTENSION pg_stat_statements; en la base registrada.",
    ),
    "PG_STATEMENTS_NOT_PRELOADED": IssueDef(
        "collect", "extension", BLOCKING,
        "pg_stat_statements esta creada pero no se cargo al iniciar el servidor.",
        "Agrega pg_stat_statements a shared_preload_libraries en postgresql.conf "
        "y reinicia el servidor.",
    ),
    "PG_STATEMENTS_OUTDATED": IssueDef(
        "collect", "version", BLOCKING,
        "La version de pg_stat_statements no expone las columnas que QueryLens necesita.",
        "QueryLens requiere PostgreSQL 17 o superior (pg_stat_statements 1.11); "
        "luego ejecuta ALTER EXTENSION pg_stat_statements UPDATE;",
    ),
    "MISSING_PG_READ_ALL_STATS": IssueDef(
        "collect", "permission", DEGRADED,
        "El usuario de monitoreo no puede ver el texto de las queries de otros usuarios.",
        "Ejecuta GRANT pg_read_all_stats TO <usuario_de_monitoreo>;",
    ),
    "TRACK_COUNTS_OFF": IssueDef(
        "preflight", "config", DEGRADED,
        "track_counts esta desactivado: no hay estadisticas de uso de tablas e indices.",
        "Activa track_counts = on en postgresql.conf y recarga la configuracion.",
    ),
    "GENERIC_PLAN_UNSUPPORTED": IssueDef(
        "preflight", "version", DEGRADED,
        "La version del servidor no soporta EXPLAIN (GENERIC_PLAN): no se analizan planes.",
        "Actualiza a PostgreSQL 16 o superior.",
    ),
    "STATEMENTS_EVICTING": IssueDef(
        "preflight", "config", INFO,
        "pg_stat_statements esta descartando queries por falta de espacio; "
        "las poco frecuentes pueden no aparecer.",
        "Sube pg_stat_statements.max en postgresql.conf y reinicia el servidor.",
    ),
    "STATS_RECENTLY_RESET": IssueDef(
        "preflight", "config", INFO,
        "Las estadisticas de queries se reiniciaron hace poco: los datos cubren una ventana corta.",
        "No requiere accion; los resultados ganan precision a medida que se acumula actividad.",
    ),
    # ---------- MySQL ----------
    "PERFORMANCE_SCHEMA_OFF": IssueDef(
        "preflight", "config", BLOCKING,
        "performance_schema esta desactivado: no hay estadisticas de queries.",
        "Activa performance_schema = ON en my.cnf y reinicia el servidor.",
    ),
    "PS_SELECT_DENIED": IssueDef(
        "collect", "permission", BLOCKING,
        "El usuario de monitoreo no puede leer performance_schema.",
        "Ejecuta GRANT SELECT ON performance_schema.* TO <usuario_de_monitoreo>;",
    ),
    "PS_CONSUMER_DISABLED": IssueDef(
        "preflight", "config", DEGRADED,
        "Hay consumers de performance_schema desactivados: faltan digests o muestras de queries.",
        "Ejecuta UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' "
        "WHERE NAME IN ('statements_digest', 'events_statements_current');",
    ),
    "MISSING_PROCESS_PRIVILEGE": IssueDef(
        "collect", "permission", DEGRADED,
        "El usuario de monitoreo no tiene PROCESS: no se ven las queries en ejecucion ni sus transacciones.",
        "Ejecuta GRANT PROCESS ON *.* TO <usuario_de_monitoreo>; si se lo diste por un rol, "
        "activalo con SET DEFAULT ROLE ALL TO <usuario_de_monitoreo>;",
    ),
    "QUERY_TEXT_TRUNCATED": IssueDef(
        "collect", "config", INFO,
        "Algunas queries llegan truncadas y no se pueden analizar sus planes.",
        "Sube performance_schema_max_sql_text_length en my.cnf y reinicia el servidor.",
    ),
    # ---------- secciones del collect ----------
    "SECTION_PERMISSION_DENIED": IssueDef(
        "collect", "permission", DEGRADED,
        "El usuario de monitoreo no tiene permiso para leer una seccion de la telemetria.",
        "Revisa los permisos de lectura del usuario de monitoreo sobre las vistas de estadisticas.",
    ),
    "SECTION_FAILED": IssueDef(
        "collect", "internal", DEGRADED,
        "No se pudo leer una seccion de la telemetria.",
        "Si persiste, contacta a soporte.",
    ),
    "QUERY_TIMEOUT": IssueDef(
        "collect", "config", DEGRADED,
        "Una consulta de telemetria supero el tiempo limite.",
        "Revisa la carga del servidor; si persiste, contacta a soporte.",
    ),
    # ---------- EXPLAIN ----------
    "EXPLAIN_PERMISSION_DENIED": IssueDef(
        "explain", "permission", DEGRADED,
        "El usuario de monitoreo no puede generar el plan de algunas queries.",
        "Concede SELECT sobre las tablas que usan esas queries al usuario de monitoreo.",
    ),
    "EXPLAIN_FOREIGN_DB_CONNECT_DENIED": IssueDef(
        "explain", "permission", DEGRADED,
        "Hay queries de otras bases del servidor a las que el usuario de monitoreo no puede conectarse.",
        "Ejecuta GRANT CONNECT ON DATABASE <base> TO <usuario_de_monitoreo>;",
    ),
    "EXPLAIN_FAILED": IssueDef(
        "explain", "internal", DEGRADED,
        "No se pudo generar el plan de algunas queries.",
        "Si persiste, contacta a soporte.",
    ),
    "NO_EXPLAINABLE_CANDIDATES": IssueDef(
        "explain", "config", INFO,
        "En este ciclo ninguna query candidata se pudo analizar (solo se analizan SELECT/WITH).",
        "No requiere accion.",
    ),
    # ---------- pipeline interno ----------
    "SNAPSHOT_VALIDATION_FAILED": IssueDef(
        "pipeline", "internal", BLOCKING,
        "QueryLens tuvo un problema interno al procesar la telemetria.",
        "No requiere accion de tu parte; el equipo de QueryLens fue notificado.",
    ),
    "ENQUEUE_FAILED": IssueDef(
        "pipeline", "internal", BLOCKING,
        "QueryLens tuvo un problema interno al enviar la telemetria a analisis.",
        "No requiere accion de tu parte; el equipo de QueryLens fue notificado.",
    ),
    "PIPELINE_FAILED": IssueDef(
        "pipeline", "internal", BLOCKING,
        "QueryLens tuvo un problema interno al extraer la telemetria de esta base.",
        "No requiere accion de tu parte; el equipo de QueryLens fue notificado.",
    ),
    "PAYLOAD_TOO_LARGE": IssueDef(
        "pipeline", "internal", INFO,
        "La telemetria de esta base es muy grande y puede tardar mas en analizarse.",
        "No requiere accion.",
    ),
}
