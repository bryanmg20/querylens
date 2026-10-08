"""Reglas lexicas del EXPLAIN: una sola sentencia y solo lectura.

Las dos reglas son puertas de seguridad (el driver de Postgres ejecuta
multi-sentencia en un solo execute; el rol monitor solo tiene SELECT), asi que
los tests fijan los dos sentidos: lo que debe pasar y lo que nunca debe pasar.
"""
import pytest

from stages.sql_text import is_explainable_command, is_single_statement, mask_sql

pytestmark = pytest.mark.unit


# --- is_single_statement: bugs de C-7 ---


@pytest.mark.parametrize(
    "query_text",
    [
        # El cierre de un dollar-quote reabria otro y ocultaba lo que seguia.
        "SELECT $$a$$; DELETE FROM t",
        "SELECT $x$a$x$; DELETE FROM t",
        # Un ' dentro de un comentario abria un string falso.
        "SELECT 1 -- it's\n; DELETE FROM t",
        "SELECT 1 /* it's */; DELETE FROM t",
        # Comentarios anidados de Postgres.
        "SELECT 1 /* a /* b */ ; */; DELETE FROM t",
        # Si el cierre reabriera, el $$ del medio se comeria el ; y el DELETE.
        "SELECT $$a $$; DELETE FROM t; SELECT $$b$$",
    ],
)
def test_hidden_second_statement_is_detected_on_postgres(query_text):
    assert is_single_statement(query_text, "postgres") is False


@pytest.mark.parametrize(
    "query_text",
    [
        # En MySQL $ es caracter de identificador, no dollar-quote.
        "SELECT a$b$c FROM t; DELETE FROM t",
        # Backslash escapa la comilla en strings de MySQL.
        r"SELECT 'it\'s'; DELETE FROM t",
        # # es comentario de linea en MySQL.
        "SELECT 1 # it's\n; DELETE FROM t",
        # /*! ... */ es codigo ejecutable en MySQL, no comentario.
        "SELECT 1 /*!50000 ; DELETE FROM t */",
    ],
)
def test_hidden_second_statement_is_detected_on_mysql(query_text):
    assert is_single_statement(query_text, "mysql") is False


@pytest.mark.parametrize(
    "query_text, dialect",
    [
        ("SELECT 1 /* a;b */", "postgres"),
        ("SELECT 1 -- a;b", "postgres"),
        ("SELECT 1 # a;b", "mysql"),
        ("SELECT $$a;b$$", "postgres"),
        ("SELECT $$a $$ || $$b;c$$", "postgres"),
        # Postgres anida: el ; sigue dentro del comentario exterior.
        ("SELECT 1 /* a /* b */ ; */", "postgres"),
        (r"SELECT E'it\'s;ok'", "postgres"),
        (r"SELECT 'it\'s;ok'", "mysql"),
        ("SELECT `a;b` FROM t", "mysql"),
        ("SELECT a$b$c FROM t", "mysql"),
        ("SELECT * FROM t WHERE id = $1", "postgres"),
        ("SELECT 1;", "postgres"),
    ],
)
def test_semicolon_inside_literal_or_comment_keeps_single_statement(query_text, dialect):
    """Antes un ; dentro de un comentario descartaba una query valida."""
    assert is_single_statement(query_text, dialect) is True


@pytest.mark.parametrize(
    "query_text, dialect",
    [
        ("SELECT * FROM t WHERE c = 'abc", "mysql"),  # sample truncado
        ("SELECT 1 /* sin cerrar", "postgres"),
        ("SELECT $$sin cerrar", "postgres"),
        ("SELECT 1 /*! sin cerrar", "mysql"),
    ],
)
def test_unterminated_text_is_not_single_statement(query_text, dialect):
    """Texto sin cerrar no se puede razonar: se trata como no explicable."""
    assert is_single_statement(query_text, dialect) is False
    assert mask_sql(query_text, dialect) is None


def test_pg_dash_dash_is_always_a_comment_but_mysql_needs_a_space():
    assert is_single_statement("SELECT 1 --x;\n", "postgres") is True
    # En MySQL "--x" es resta de un negativo, no comentario: el ; separa.
    assert is_single_statement("SELECT 1 --x; DELETE FROM t", "mysql") is False


# --- is_explainable_command: solo lectura ---


@pytest.mark.parametrize(
    "query_text, dialect",
    [
        ("SELECT * FROM t", "postgres"),
        ("with x as (select 1) select * from x", "postgres"),
        ("/* app */ SELECT * FROM t", "mysql"),
        ("(SELECT a FROM t) UNION (SELECT a FROM u)", "postgres"),
        ("SELECT last_update FROM t", "postgres"),
        ("SELECT * FROM t WHERE note = 'please update; for share'", "postgres"),
        ("SELECT * FROM t -- for update", "postgres"),
    ],
)
def test_read_only_select_is_explainable(query_text, dialect):
    assert is_explainable_command(query_text, dialect) is True


@pytest.mark.parametrize(
    "query_text, dialect",
    [
        ("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "postgres"),
        ("WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM u", "postgres"),
        ("WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i", "postgres"),
        ("SELECT * FROM t WHERE id = $1 FOR UPDATE", "postgres"),
        ("SELECT * FROM t FOR NO KEY UPDATE", "postgres"),
        ("SELECT * FROM t FOR SHARE", "postgres"),
        ("SELECT * FROM t FOR KEY SHARE", "postgres"),
        ("SELECT * FROM t WHERE id = ? FOR UPDATE", "mysql"),
        ("SELECT * FROM t LOCK IN SHARE MODE", "mysql"),
        ("select * from t for share", "mysql"),
        ("INSERT INTO t VALUES (1)", "postgres"),
        ("/* app */ DELETE FROM t", "mysql"),
        ("SET x = 1", "postgres"),
        ("SELECT 'abc", "mysql"),
    ],
)
def test_write_or_locking_statement_is_not_explainable(query_text, dialect):
    """El rol monitor solo tiene SELECT: el EXPLAIN de esto falla por permisos
    en cada ciclo. Va a non_explainable_candidates, sin plan."""
    assert is_explainable_command(query_text, dialect) is False
