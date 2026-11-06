"""Carga de trabajo minima para que los tests de integracion tengan algo que explicar.

pg_stat_statements y performance_schema arrancan vacios en un contenedor limpio, y
los tests que dependen de candidatos reales se saltan. Este script crea una tabla
pequena y la consulta hasta dejar varias entradas con digests distintos.

Corre en CI antes de pytest. No es la bateria del sandbox (esa va en
ql_sandbox/scripts/battery.sh); es lo minimo para que la capa de integracion
tenga material y no se salte a si misma.

Para Postgres el DDL y el trafico los genera el rol de aplicacion (ql_app), que
se crea en el paso del workflow. El rol de monitoreo (querylens_monitor) solo
observa (lectura) y nunca genera el trafico observado, replicando la topologia
del sandbox.

El trafico tiene que generarlo un rol DISTINTO al de monitoreo. STATEMENTS_QUERY
filtra userid != session_user, asi que si la carga corre como el monitor el
collector no ve nada y los tests de integracion no tienen con que trabajar. Por
eso APP_PG_USER existe: en CI se crea un rol de aplicacion aparte y la carga se
conecta con el, replicando la topologia del sandbox (ql_app genera trafico,
querylens_monitor observa).

MySQL replica la misma topologia con APP_MY_USER (ql_mysql_app en CI, app_user en
el sandbox): el trafico lo genera el rol app, el monitor solo observa. Para los
digests de statements el rol monitor alcanzaria igual (el filtro de MySQL es por
forma de texto, no por rol), pero active_queries excluye el CURRENT_USER, asi que
esa carga tiene que venir de un rol distinto.
"""
import os
import sys
import time

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_mysql_app,
    get_connection_postgres,
)

# Rol de aplicacion para generar trafico en Postgres. El pipeline nunca se
# conecta con el: solo existe para que haya consultas de otro userid que el
# collector pueda observar.
APP_PG_USER = os.getenv("APP_PG_USER", "ql_app")
APP_PG_PASSWORD = os.getenv("APP_PG_PASSWORD", "app_pass")
APP_PG_DB = os.getenv("MONITOR_PG_DB", "ql_demo")

# Rol de aplicacion para generar trafico en MySQL: mismo criterio que Postgres.
# En el sandbox es app_user (lo crea MYSQL_USER del compose); en CI lo crea el
# paso 'rol de aplicacion MySQL' del workflow (ql_mysql_app). Host y puerto se
# toman de MONITOR_MY_HOST/PORT en get_connection_mysql_app.
APP_MY_USER = os.getenv("APP_MY_USER", "app_user")
APP_MY_PASSWORD = os.getenv("APP_MY_PASSWORD", "app_pass")
APP_MY_DB = os.getenv("APP_MY_DB", "ql_demo")

QUERIES = [
    "SELECT * FROM ql_ci WHERE id = 7",
    "SELECT * FROM ql_ci WHERE k = 12",
    "SELECT count(*), k FROM ql_ci GROUP BY k ORDER BY 2 LIMIT 5",
    "SELECT DISTINCT k FROM ql_ci ORDER BY k LIMIT 10",
    "SELECT a.id FROM ql_ci a JOIN ql_ci b ON a.id = b.id WHERE a.k > 3 ORDER BY 1 LIMIT 20",
    "SELECT pad FROM ql_ci WHERE id IN (1, 2, 3, 4, 5) ORDER BY id",
    "SELECT sum(id) OVER (PARTITION BY k) FROM ql_ci ORDER BY 1 LIMIT 10",
    "SELECT k, count(*) FROM ql_ci WHERE id < 1000 GROUP BY k HAVING count(*) > 1 ORDER BY 1",
]

SCHEMA = "ql_ci_load"

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.ql_ci (
    id integer PRIMARY KEY,
    k integer NOT NULL,
    pad text
)
"""

SEED = f"""
INSERT INTO {SCHEMA}.ql_ci (id, k, pad)
SELECT g, g % 97, repeat('x', 50)
FROM generate_series(1, 5000) AS g
ON CONFLICT DO NOTHING
"""


def _qualify(query):
    """Cualifica ql_ci con su schema. Solo para Postgres: MySQL usa USE."""
    return query.replace("ql_ci", f"{SCHEMA}.ql_ci")


def _count(engine, sql, use=None):
    """El conteo es solo informativo: si el rol no puede leer la vista, no es
    motivo para abortar la carga."""
    try:
        with engine.connect() as conn:
            if use:
                conn.execute(text(f"USE {use}"))
            return conn.execute(text(sql)).scalar()
    except Exception as exc:
        return f"no verificable ({type(exc).__name__})"


def _app_engine():
    """Engine de aplicacion para Postgres.

    Es separado a proposito del engine de monitoreo: el filtro de
    STATEMENTS_QUERY es userid != session_user, y con el mismo rol la carga
    seria invisible para el collector. El rol de aplicacion hace el DDL y el
    trafico; el monitor solo observa y nunca escribe.
    """
    return create_engine(
        f"postgresql+psycopg2://{APP_PG_USER}:{APP_PG_PASSWORD}"
        f"@{os.getenv('MONITOR_PG_HOST', 'localhost')}"
        f":{os.getenv('MONITOR_PG_PORT', '5432')}/{APP_PG_DB}"
    )


def load_postgres(rounds=40):
    """DDL y trafico con el rol de aplicacion.

    Falla duro si ese rol no conecta: caer de vuelta al monitor dejaria el
    trafico con el mismo userid que el collector filtra, y los tests de
    integracion pasarian sin ver nada. Prefiere romper aqui que en el pytest.
    """
    engine = _app_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(DDL))
            conn.execute(text(SEED))
            conn.execute(text(f"ANALYZE {SCHEMA}.ql_ci"))
    except Exception as exc:
        raise RuntimeError(
            f"el rol de aplicacion {APP_PG_USER!r} no pudo crear el schema "
            f"{SCHEMA!r} ({type(exc).__name__}). En CI lo crea el paso 'rol de "
            f"aplicacion separado del monitor'. Si se cae al rol monitor, el "
            f"collector no ve el trafico y los tests de integracion pasan en "
            f"vacio."
        ) from exc

    seen = 0
    for _ in range(rounds):
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path TO {SCHEMA}"))
            for query in QUERIES:
                try:
                    conn.execute(text(_qualify(query)))
                    seen += 1
                except Exception as exc:
                    print(
                        f"aviso postgres: la carga fallo ({type(exc).__name__}), se corta",
                        file=sys.stderr,
                    )
                    return seen
        time.sleep(0.05)

    total = _count(get_connection_postgres(), "SELECT count(*) FROM pg_stat_statements WHERE query LIKE '%ql_ci%'")
    print(f"postgres: {seen} ejecuciones, {total} entradas en pg_stat_statements")


MY_DDL = """
CREATE TABLE IF NOT EXISTS ql_ci (
    id integer PRIMARY KEY,
    k integer NOT NULL,
    pad text
)
"""

MY_SEED = (
    "INSERT IGNORE INTO ql_ci (id, k, pad) "
    "SELECT seq, seq % 97, REPEAT('x', 50) FROM "
    "(SELECT a.n + b.n * 10 + c.n * 100 + 1 AS seq "
    " FROM (SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) a, "
    "(SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) b, "
    "(SELECT 0 n UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) c) t"
)


def load_mysql(rounds=40):
    """DDL y trafico con el rol de aplicacion (APP_MY_USER), no con el monitor.

    Espejo de load_postgres: la topologia es 'el rol app genera, el monitor
    observa'. El trafico del propio monitor alcanzaria para los digests de
    statements (MySQL filtra por forma de texto), pero active_queries excluye el
    CURRENT_USER, asi que la carga tiene que venir de otro rol. Si ese rol no
    conecta se falla duro, con el mismo criterio que load_postgres: prefiere
    romper aqui que pasar tests en vacio."""
    engine = get_connection_mysql_app()
    try:
        with engine.begin() as conn:
            conn.execute(text("USE ql_demo"))
            for statement in (MY_DDL, MY_SEED, "ANALYZE TABLE ql_ci"):
                try:
                    conn.execute(text(statement))
                except Exception as exc:
                    print(
                        f"aviso mysql: {statement.split()[0:3]} fallo ({type(exc).__name__}); "
                        f"se asume que la tabla ya existe",
                        file=sys.stderr,
                    )
    except Exception as exc:
        raise RuntimeError(
            f"el rol de aplicacion {APP_MY_USER!r} no pudo conectar para crear "
            f"ql_ci ({type(exc).__name__}). En CI lo crea el paso 'rol de "
            f"aplicacion MySQL'. Si la carga cae al rol monitor, active_queries "
            f"no ve el trafico y el control de captura pasaria en vacio."
        ) from exc

    seen = 0
    warned = False
    for _ in range(rounds):
        with engine.begin() as conn:
            conn.execute(text("USE ql_demo"))
            for query in QUERIES:
                try:
                    conn.execute(text(query))
                    seen += 1
                except Exception as exc:
                    if not warned:
                        print(
                            f"aviso mysql: la carga fallo ({type(exc).__name__}), "
                            f"se corta. Sin tabla no hay digests y los tests se saltan.",
                            file=sys.stderr,
                        )
                        warned = True
                    return seen
        time.sleep(0.05)

    total = _count(
        get_connection_mysql(),
        "SELECT count(*) FROM performance_schema.events_statements_summary_by_digest "
        "WHERE DIGEST_TEXT LIKE '%ql_ci%'",
        use="ql_demo",
    )
    print(f"mysql: {seen} ejecuciones, {total} digests")


if __name__ == "__main__":
    for name, loader in (("postgres", load_postgres), ("mysql", load_mysql)):
        try:
            loader()
        except Exception as exc:
            print(f"{name}: no se pudo cargar ({type(exc).__name__}: {exc})", file=sys.stderr)
