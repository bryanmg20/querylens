"""Excepcion del driver -> code del catalogo.

Solo se mira el SQLSTATE (psycopg2), el errno (pymysql) y, en la fase de
conexion, unas pocas frases fijas del mensaje: libpq no trae SQLSTATE para la
mayoria de las fallas de conexion. El mensaje crudo nunca sale de aqui: puede
traer texto de la query, el host o el usuario.
"""

# Fase de conexion: Postgres por SQLSTATE, MySQL por errno.
_PG_CONNECT_STATES = {
    "28P01": "AUTH_FAILED",
    "28000": "AUTH_FAILED",
    "3D000": "DATABASE_NOT_FOUND",
    "53300": "TOO_MANY_CONNECTIONS",
}
_MY_CONNECT_ERRNOS = {
    1044: "CONNECT_PERMISSION_DENIED",   # Access denied for user to database
    1045: "AUTH_FAILED",
    2003: "HOST_UNREACHABLE",
    2005: "HOST_UNREACHABLE",
    1049: "DATABASE_NOT_FOUND",
    1040: "TOO_MANY_CONNECTIONS",
    2013: "CONNECT_TIMEOUT",
}
# libpq/psycopg2 sin pgcode: el orden importa ("timeout expired" antes que
# cualquier frase generica).
_CONNECT_PHRASES = (
    # Sin CONNECT sobre la base: libpq lo trae como FATAL sin pgcode
    # (verificado: 'FATAL:  permission denied for database "x"', pgcode=None).
    ("permission denied for database", "CONNECT_PERMISSION_DENIED"),
    ("password authentication failed", "AUTH_FAILED"),
    ("no pg_hba.conf entry", "AUTH_FAILED"),
    ("timeout expired", "CONNECT_TIMEOUT"),
    ("timed out", "CONNECT_TIMEOUT"),
    ("could not translate host name", "HOST_UNREACHABLE"),
    ("name or service not known", "HOST_UNREACHABLE"),
    ("connection refused", "HOST_UNREACHABLE"),
    ("no route to host", "HOST_UNREACHABLE"),
    ("network is unreachable", "HOST_UNREACHABLE"),
    ("too many clients", "TOO_MANY_CONNECTIONS"),
    ("remaining connection slots", "TOO_MANY_CONNECTIONS"),
)

# pg_stat_statements falla de forma distinta segun que le falte.
_PG_STATEMENTS_STATES = {
    "42P01": "PG_STATEMENTS_NOT_INSTALLED",   # relation does not exist
    "42883": "PG_STATEMENTS_NOT_INSTALLED",   # function does not exist
    "55000": "PG_STATEMENTS_NOT_PRELOADED",   # must be loaded via shared_preload_libraries
    "42703": "PG_STATEMENTS_OUTDATED",        # column does not exist (stats_since < 1.11)
}
_PG_PERMISSION = {"42501"}
_MY_PERMISSION = {1142, 1143, 1227}
# ER_SPECIFIC_ACCESS_DENIED_ERROR: "you need (at least one of) the X privilege(s)".
_MY_SPECIFIC_PRIVILEGE = 1227
_PG_TIMEOUT = {"57014"}
_MY_TIMEOUT = {3024, 2013}


def error_codes(exc) -> tuple[str | None, int | None]:
    """(sqlstate, errno) de una excepcion SQLAlchemy o del driver directo."""
    orig = getattr(exc, "orig", None) or exc
    sqlstate = getattr(orig, "pgcode", None)
    errno = None
    args = getattr(orig, "args", ())
    if args and isinstance(args[0], int):
        errno = args[0]
    return sqlstate, errno


def _params(exc, sqlstate, errno) -> dict:
    params = {"exc_type": type(getattr(exc, "orig", None) or exc).__name__}
    if sqlstate:
        params["sqlstate"] = sqlstate
    if errno is not None:
        params["errno"] = errno
    return params


def _connect_code(exc, sqlstate, errno) -> str:
    if sqlstate in _PG_CONNECT_STATES:
        return _PG_CONNECT_STATES[sqlstate]
    if errno in _MY_CONNECT_ERRNOS:
        return _MY_CONNECT_ERRNOS[errno]
    text = str(exc).lower()
    for phrase, code in _CONNECT_PHRASES:
        if phrase in text:
            return code
    if "does not exist" in text:
        if 'database "' in text:
            return "DATABASE_NOT_FOUND"
        if 'role "' in text:
            return "AUTH_FAILED"
    return "CONNECTION_FAILED"


def _collect_code(exc, dialect, section, sqlstate, errno) -> str:
    # Se mide el resultado, no los grants: sin PROCESS (directo o via un ROLE
    # activo) active_queries falla al leer innodb_trx. El mensaje solo se mira
    # para saber QUE privilegio falta; no se guarda.
    if errno == _MY_SPECIFIC_PRIVILEGE and "PROCESS" in str(exc):
        return "MISSING_PROCESS_PRIVILEGE"
    if dialect == "postgres" and section == "statements" and sqlstate in _PG_STATEMENTS_STATES:
        return _PG_STATEMENTS_STATES[sqlstate]
    if sqlstate in _PG_TIMEOUT or errno in _MY_TIMEOUT:
        return "QUERY_TIMEOUT"
    if dialect == "mysql" and section == "statements" and errno in _MY_PERMISSION:
        return "PS_SELECT_DENIED"
    if sqlstate in _PG_PERMISSION or errno in _MY_PERMISSION:
        return "SECTION_PERMISSION_DENIED"
    return "SECTION_FAILED"


def _explain_code(exc, phase, sqlstate, errno) -> str:
    if phase == "connect":
        # Abrir la base ajena falla en la fase de conexion: mismo diagnostico
        # que el target (frases de libpq), no el SQLSTATE de una sentencia.
        denied = _connect_code(exc, sqlstate, errno) == "CONNECT_PERMISSION_DENIED"
        return "EXPLAIN_FOREIGN_DB_CONNECT_DENIED" if denied else "EXPLAIN_FAILED"
    denied = sqlstate in _PG_PERMISSION or errno in _MY_PERMISSION
    return "EXPLAIN_PERMISSION_DENIED" if denied else "EXPLAIN_FAILED"


def classify(exc, *, dialect: str, scope: str, section: str = "", phase: str = "") -> tuple[str, dict]:
    """(code, params) para la excepcion. params solo lleva codigos, nunca el mensaje."""
    sqlstate, errno = error_codes(exc)
    if scope == "connection":
        code = _connect_code(exc, sqlstate, errno)
    elif scope == "collect":
        code = _collect_code(exc, dialect, section, sqlstate, errno)
    elif scope == "explain":
        code = _explain_code(exc, phase, sqlstate, errno)
    else:
        raise ValueError(f"scope sin clasificacion: {scope!r}")
    return code, _params(exc, sqlstate, errno)
