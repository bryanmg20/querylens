"""classify: excepcion del driver -> code del catalogo, sin filtrar el mensaje."""
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from health.catalog import CATALOG
from health.classify import classify

pytestmark = pytest.mark.unit


class _PgError(Exception):
    """psycopg2: el SQLSTATE viaja en pgcode."""

    def __init__(self, message, pgcode=None):
        super().__init__(message)
        self.pgcode = pgcode


class _MyError(Exception):
    """pymysql: args = (errno, mensaje)."""


def _pg(message="boom", pgcode=None, cls=ProgrammingError):
    return cls("SELECT 1", {}, _PgError(message, pgcode))


def _my(errno, message="boom", cls=OperationalError):
    return cls("SELECT 1", {}, _MyError(errno, message))


# ---------- fase de conexion ----------


@pytest.mark.parametrize(
    "message, pgcode, code",
    [
        ('FATAL:  password authentication failed for user "monitor"', None, "AUTH_FAILED"),
        ("FATAL:  no pg_hba.conf entry for host", None, "AUTH_FAILED"),
        ('FATAL:  role "ghost" does not exist', None, "AUTH_FAILED"),
        ('could not translate host name "db.x" to address', None, "HOST_UNREACHABLE"),
        ("connection refused", None, "HOST_UNREACHABLE"),
        ("timeout expired", None, "CONNECT_TIMEOUT"),
        ('FATAL:  database "app" does not exist', None, "DATABASE_NOT_FOUND"),
        ("FATAL:  sorry, too many clients already", None, "TOO_MANY_CONNECTIONS"),
        ("algo raro", "28P01", "AUTH_FAILED"),
        ("algo raro", "3D000", "DATABASE_NOT_FOUND"),
        ("algo raro", None, "CONNECTION_FAILED"),
    ],
)
def test_postgres_connection_codes(message, pgcode, code):
    exc = _pg(message, pgcode, cls=OperationalError)
    assert classify(exc, dialect="postgres", scope="connection")[0] == code


@pytest.mark.parametrize(
    "errno, code",
    [
        (1045, "AUTH_FAILED"),
        (2003, "HOST_UNREACHABLE"),
        (1049, "DATABASE_NOT_FOUND"),
        (1040, "TOO_MANY_CONNECTIONS"),
        (2013, "CONNECT_TIMEOUT"),
        (9999, "CONNECTION_FAILED"),
    ],
)
def test_mysql_connection_codes(errno, code):
    assert classify(_my(errno), dialect="mysql", scope="connection")[0] == code


# ---------- collect ----------


@pytest.mark.parametrize(
    "pgcode, code",
    [
        ("42P01", "PG_STATEMENTS_NOT_INSTALLED"),
        ("55000", "PG_STATEMENTS_NOT_PRELOADED"),
        ("42703", "PG_STATEMENTS_OUTDATED"),
        ("42501", "SECTION_PERMISSION_DENIED"),
        ("57014", "QUERY_TIMEOUT"),
        ("XX000", "SECTION_FAILED"),
    ],
)
def test_postgres_statements_section(pgcode, code):
    exc = _pg(pgcode=pgcode)
    assert classify(exc, dialect="postgres", scope="collect", section="statements")[0] == code


def test_pg_statements_codes_only_apply_to_statements_section():
    exc = _pg(pgcode="42P01")
    assert classify(exc, dialect="postgres", scope="collect", section="locks")[0] == "SECTION_FAILED"


def test_mysql_performance_schema_denied_on_statements_is_blocking():
    code, _ = classify(_my(1142, cls=ProgrammingError), dialect="mysql", scope="collect",
                       section="statements")
    assert code == "PS_SELECT_DENIED"
    assert CATALOG[code].severity == "blocking"


def test_mysql_denied_on_other_section_is_degraded():
    code, _ = classify(_my(1142, cls=ProgrammingError), dialect="mysql", scope="collect",
                       section="locks")
    assert code == "SECTION_PERMISSION_DENIED"


def test_mysql_missing_process_is_detected_from_the_failed_section():
    # Verificado en MySQL 8.0: sin PROCESS, active_queries falla al leer innodb_trx.
    exc = _my(1227, "Access denied; you need (at least one of) the PROCESS privilege(s) for this operation")
    code, params = classify(exc, dialect="mysql", scope="collect", section="active_queries")
    assert code == "MISSING_PROCESS_PRIVILEGE"
    assert params == {"exc_type": "_MyError", "errno": 1227}


def test_mysql_1227_for_another_privilege_is_generic():
    exc = _my(1227, "Access denied; you need (at least one of) the SUPER privilege(s) for this operation")
    code, _ = classify(exc, dialect="mysql", scope="collect", section="locks")
    assert code == "SECTION_PERMISSION_DENIED"


def test_mysql_timeout():
    assert classify(_my(3024), dialect="mysql", scope="collect", section="statements")[0] == "QUERY_TIMEOUT"


# ---------- explain ----------


def test_explain_permission_denied():
    assert classify(_pg(pgcode="42501"), dialect="postgres", scope="explain")[0] == "EXPLAIN_PERMISSION_DENIED"


def test_explain_foreign_connect_denied():
    exc = _pg(pgcode="42501", cls=OperationalError)
    code, _ = classify(exc, dialect="postgres", scope="explain", phase="connect")
    assert code == "EXPLAIN_FOREIGN_DB_CONNECT_DENIED"


def test_explain_other_failure():
    assert classify(_pg(pgcode="42601"), dialect="postgres", scope="explain")[0] == "EXPLAIN_FAILED"


# ---------- privacidad y contrato ----------


def test_params_carry_codes_never_the_message():
    secret = 'SELECT * FROM users WHERE password = \'hunter2\' -- host db.interno'
    _, params = classify(_pg(secret, pgcode="42501"), dialect="postgres", scope="collect",
                         section="statements")
    assert params == {"exc_type": "_PgError", "sqlstate": "42501"}
    assert "hunter2" not in repr(params)


def test_mysql_params_carry_errno():
    _, params = classify(_my(1045, "Access denied for user 'x'@'y'"), dialect="mysql",
                         scope="connection")
    assert params == {"exc_type": "_MyError", "errno": 1045}


def test_raw_driver_exception_without_sqlalchemy_wrapper():
    assert classify(_PgError("x", "42501"), dialect="postgres", scope="explain")[0] == "EXPLAIN_PERMISSION_DENIED"


def test_unknown_scope_is_a_programming_error():
    with pytest.raises(ValueError):
        classify(_pg(), dialect="postgres", scope="nope")
