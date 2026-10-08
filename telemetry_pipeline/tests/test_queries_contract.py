import re

import pytest

from collectors.mysql.queries import (
    ACTIVE_QUERIES_QUERY as MYSQL_ACTIVE_QUERIES_QUERY,
)
from collectors.mysql.queries import (
    LOCKS_QUERY as MYSQL_LOCKS_QUERY,
)
from collectors.mysql.queries import STATEMENTS_QUERY
from collectors.postgres.queries import (
    ACTIVE_QUERIES_QUERY as PG_ACTIVE_QUERIES_QUERY,
)
from collectors.postgres.queries import (
    LOCKS_QUERY as PG_LOCKS_QUERY,
)
from collectors.postgres.queries import STATEMENTS_QUERY as PG_STATEMENTS_QUERY

pytestmark = pytest.mark.contract


def test_stmt_query_defines_avg_rows_per_call():
    assert "AS avg_rows_per_call" in STATEMENTS_QUERY


def test_stmt_query_keeps_fractional_avg_rows():
    assert re.search(
        r"ROUND\(s\.SUM_ROWS_SENT / NULLIF\(s\.COUNT_STAR, 0\), [1-9]\)",
        STATEMENTS_QUERY,
    )


def test_pg_stmt_query_keeps_fractional_avg_rows():
    assert re.search(
        r"ROUND\(rows::numeric / NULLIF\(calls, 0\), [1-9]\)",
        PG_STATEMENTS_QUERY,
    )


def test_stmt_query_exposes_stddev_and_coeff():
    """MySQL no calcula la desviacion: performance_schema no la da. El contrato
    exige que la columna exista, pero como NULL, no un agregado cualquiera.

    Aceptar 'NULL AS stddev_time_ms' era lo que hacia el assert vacuamente
    cierto: la query ya traia NULL y el test pasaba sin exigir nada.
    """
    assert re.search(
        r"NULL\s+AS stddev_time_ms", STATEMENTS_QUERY
    ), "MySQL debe declarar stddev_time_ms como NULL, no calcularlo a mano"
    assert re.search(
        r"NULL\s+AS coeff_of_variation", STATEMENTS_QUERY
    ), "MySQL no puede derivar el coeficiente de variacion de MIN/MAX/AVG"
    # El calculo real vive en el collector, no en la query.
    from collectors.mysql.collector import Mysql_Collector

    assert hasattr(Mysql_Collector, "calculate_stddev_coeff")


def test_mysql_autofiltrado_cubre_toda_la_huella_del_pipeline():
    """El filtro SQL y PIPELINE_FINGERPRINT tienen que decir lo mismo.

    Son dos listas que cumplen la misma funcion en lugares distintos: una
    excluye en la query, la otra mide la contaminacion en los goldens. Cuando
    divergieron, COMMIT aparecio en statements y el test de integracion lo
    cazo. Este test es el que evita que vuelvan a separarse en silencio.
    """
    from ci.regenerate_goldens import PIPELINE_FINGERPRINT

    # Los backticks se normalizan en los dos lados: el digest trae
    # 'SET `AUTOCOMMIT`' y tanto el filtro como la huella pueden escribirse con o
    # sin ellos.
    normalizado = STATEMENTS_QUERY.upper().replace("`", "")
    for huella in PIPELINE_FINGERPRINT:
        # Sin strip: el espacio final de 'use ' es parte del prefijo real
        # ('USE %'), no ruido. Strippearlo buscaria 'USE%' y no encontraria nada.
        prefijo = huella.upper().replace("`", "")
        cubierto = f"NOT LIKE '{prefijo}%" in normalizado
        assert cubierto, (
            f"la huella {huella!r} no tiene filtro en STATEMENTS_QUERY: "
            "ampliar el filtro o el fingerprint, pero no dejarlos distintos"
        )


def test_mysql_statement_id_is_qualified_by_schema():
    """La PK de events_statements_summary_by_digest es (SCHEMA_NAME, DIGEST):
    el mismo digest en dos esquemas son dos filas distintas. query_id copiaba
    solo el digest, asi que una fila de cada par se descartaba por dedup. El
    SID compuesto hace a cada fila unica y atribuida."""
    assert re.search(
        r"CONCAT\(s\.schema_name, '/', s\.DIGEST\)\s+AS query_id",
        STATEMENTS_QUERY,
    ), "query_id de MySQL debe ser {schema}/{digest}, no solo el digest"


def test_mysql_statement_row_carries_database_name():
    assert re.search(
        r"s\.schema_name\s+AS database_name", STATEMENTS_QUERY
    ), "El statement de MySQL debe declarar database_name (base = schema)"


def test_mysql_active_query_id_is_qualified_by_schema():
    """El query_id de una query activa tiene que ser comparable con el del
    statement del que proviene: ambos compuestos por el mismo esquema.

    events_statements_current expone el esquema como CURRENT_SCHEMA (SCHEMA_NAME
    solo existe en las tablas summary); con el nombre equivocado la query falla
    con 1054 y active_queries queda en None.
    """
    assert re.search(
        r"CONCAT\(s\.CURRENT_SCHEMA, '/', s\.DIGEST\)\s+AS query_id",
        MYSQL_ACTIVE_QUERIES_QUERY,
    )


def test_grid_sections_carry_database_name():
    for query in (PG_STATEMENTS_QUERY, PG_LOCKS_QUERY, PG_ACTIVE_QUERIES_QUERY):
        assert re.search(r"AS database_name", query)
    for query in (MYSQL_LOCKS_QUERY, MYSQL_ACTIVE_QUERIES_QUERY):
        assert re.search(r"AS database_name", query)
    # pg_stat_activity ya expone datname; statements y locks necesitan union
    # contra pg_database (pg_stat_statements.dbid / pg_locks.database).
    assert re.search(r"pg_database", PG_STATEMENTS_QUERY)
    assert re.search(r"pg_database", PG_LOCKS_QUERY)
    assert re.search(r"d\.datname\s+AS database_name", PG_STATEMENTS_QUERY)
    assert re.search(r"d\.datname\s+AS database_name", PG_LOCKS_QUERY)
    assert re.search(r"datname\s+AS database_name", PG_ACTIVE_QUERIES_QUERY)
    assert re.search(r"l\.object_schema\s+AS database_name", MYSQL_LOCKS_QUERY)
    # CURRENT_SCHEMA es la columna real de events_statements_current (SCHEMA_NAME
    # solo existe en las tablas summary; ver test_mysql_active_query_id_...).
    assert re.search(r"s\.CURRENT_SCHEMA\s+AS database_name", MYSQL_ACTIVE_QUERIES_QUERY)


# --- C-6 regression: LOCKS_QUERY filters out pipeline's own locks ---


def test_pg_locks_query_excludes_pipeline_user():
    """C-6: PG LOCKS_QUERY must filter out locks from the monitoring user (session_user)."""
    assert "session_user" in PG_LOCKS_QUERY
    assert "usesysid !=" in PG_LOCKS_QUERY
    assert "pg_stat_activity" in PG_LOCKS_QUERY
    assert "JOIN pg_stat_activity" in PG_LOCKS_QUERY


def test_mysql_locks_query_excludes_pipeline_user():
    """C-6: MySQL LOCKS_QUERY must filter out locks from the monitoring user (CURRENT_USER)."""
    assert "CURRENT_USER" in MYSQL_LOCKS_QUERY
    assert "processlist_user !=" in MYSQL_LOCKS_QUERY
    assert "performance_schema.threads" in MYSQL_LOCKS_QUERY
    assert "JOIN performance_schema.threads" in MYSQL_LOCKS_QUERY or "LEFT JOIN performance_schema.threads" in MYSQL_LOCKS_QUERY

# --- Lock rows sin process_id: el hilo sale de data_locks, no de innodb_trx ---


def test_mysql_locks_query_resolves_thread_from_data_locks():
    """innodb_trx es un cache que se materializa antes que data_locks: las
    transacciones nuevas quedaban sin par y process_id llegaba NULL, lo que
    tiraba el snapshot completo (LockRow.process_id es int obligatorio).
    Verificado en vivo con carga FOR UPDATE: 48/60 lecturas con NULL."""
    assert "JOIN information_schema.innodb_trx" not in MYSQL_LOCKS_QUERY
    assert re.search(r"ON\s+t\.thread_id\s*=\s*l\.thread_id", MYSQL_LOCKS_QUERY)
    assert re.search(r"t\.processlist_id\s+AS process_id", MYSQL_LOCKS_QUERY)


def test_mysql_locks_query_never_emits_null_process_id():
    """Los hilos de fondo no tienen processlist_id; sin este filtro su fila
    invalidaria el snapshot entero en vez de omitirse."""
    assert "t.processlist_id IS NOT NULL" in MYSQL_LOCKS_QUERY
