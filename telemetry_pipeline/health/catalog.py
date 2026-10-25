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
    "CONNECT_PERMISSION_DENIED": IssueDef(
        "connection", "permission", BLOCKING,
        "El usuario de monitoreo no tiene permiso para conectarse a la base registrada.",
        "Ejecuta GRANT CONNECT ON DATABASE <base> TO <usuario_de_monitoreo>;",
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
        "Ejecuta ALTER EXTENSION pg_stat_statements UPDATE; (QueryLens necesita la 1.11 o superior).",
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
    "PG_VERSION_UNSUPPORTED": IssueDef(
        "preflight", "version", BLOCKING,
        "La version de PostgreSQL es anterior a la minima soportada por QueryLens (17).",
        "Actualiza el servidor a PostgreSQL 17 o superior.",
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
    # Info y no degraded: la query sigue en pg_stat_statements y fallaria en
    # cada ciclo; como degraded dejaria la base degradada para siempre por un
    # limite conocido del plan generico, sin nada que el cliente pueda arreglar.
    "EXPLAIN_PARAM_TYPES_AMBIGUOUS": IssueDef(
        "explain", "config", INFO,
        "Algunas queries no admiten plan generico: solo pasan valores a funciones u "
        "operadores con varias versiones y Postgres no puede elegir sin los tipos.",
        "No requiere accion. Con auto_explain instalado, su plan llega del log cuando "
        "una ejecucion pasa de auto_explain.log_min_duration.",
    ),
    "NO_EXPLAINABLE_CANDIDATES": IssueDef(
        "explain", "config", INFO,
        "En este ciclo ninguna query candidata se pudo analizar (solo se analizan SELECT/WITH).",
        "No requiere accion.",
    ),
    "AUTO_EXPLAIN_LOG_UNAVAILABLE": IssueDef(
        "explain", "config", INFO,
        "No se pudo leer el log de auto_explain: los planes son genericos, sin los valores reales de cada ejecucion.",
        "Opcional: sigue la guia de instalacion de auto_explain (funciones querylens.log_files y querylens.read_log).",
    ),
    # ---------- contencion (DeadlocksStage) ----------
    "DEADLOCK_REPORTS_UNAVAILABLE": IssueDef(
        "collect", "config", DEGRADED,
        "No se pudieron leer los informes de abrazo mortal: QueryLens no puede "
        "analizar los abrazos mortales de esta base.",
        "Postgres: sigue la guia de instalacion de auto_explain (funciones "
        "querylens.log_files y querylens.read_log). MySQL: otorga PROCESS al "
        "usuario de monitoreo (GRANT PROCESS ON *.* TO <usuario_de_monitoreo>;).",
    ),
    "DEADLOCK_REPORTS_PARTIAL": IssueDef(
        "collect", "config", INFO,
        "Los informes de abrazo mortal llegan incompletos: si hay varios abrazos "
        "seguidos, algunos se cuentan sin detalle o no se ven.",
        "Postgres: log_error_verbosity distinto de terse y log_min_messages en "
        "error o mas bajo. MySQL 8.0.22 o superior con "
        "SET PERSIST innodb_print_all_deadlocks = ON; SET PERSIST log_error_verbosity = 3;",
    ),
    # ---------- calidad de la captura (EngineCountersStage, writer) ----------
    "DEADLOCK_COUNTER_UNAVAILABLE": IssueDef(
        "collect", "config", INFO,
        "No se pudo leer el contador de abrazos mortales del motor: el reporte "
        "no puede decir cuantos abrazos hubo ni cuantos se perdieron.",
        "Postgres: el usuario de monitoreo necesita leer pg_stat_database. MySQL: "
        "otorga PROCESS al usuario de monitoreo y activa el contador con "
        "SET GLOBAL innodb_monitor_enable = 'lock_deadlocks';",
    ),
    "LOG_WINDOW_OVERRUN": IssueDef(
        "collect", "config", DEGRADED,
        "El log de Postgres crecio entre dos ciclos mas de lo que QueryLens lee "
        "por ciclo: los abrazos mortales y los planes de auto_explain escritos "
        "en ese tramo no se leyeron.",
        "Baja el volumen del log: sube auto_explain.log_min_duration y revisa "
        "log_statement, log_min_duration_statement y log_connections.",
    ),
    # ---------- pipeline interno ----------
    "SNAPSHOT_VALIDATION_FAILED": IssueDef(
        "pipeline", "internal", BLOCKING,
        "QueryLens tuvo un problema interno al procesar la telemetria.",
        "No requiere accion de tu parte; el equipo de QueryLens fue notificado.",
    ),
    "ENQUEUE_FAILED": IssueDef(
        "enqueue", "internal", BLOCKING,
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
