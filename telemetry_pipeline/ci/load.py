"""Carga de trabajo minima para que los tests de integracion tengan algo que explicar.

pg_stat_statements y performance_schema arrancan vacios en un contenedor limpio, y
los tests que dependen de candidatos reales se saltan. Este script crea una tabla
pequena y la consulta hasta dejar varias entradas con digests distintos.

Corre en CI antes de pytest. No es la bateria del sandbox (esa va en
ql_sandbox/scripts/battery.sh); es lo minimo para que la capa de integracion
tenga material y no se salte a si misma.

Crea la tabla como el rol_duenio del motor (MONITOR_PG_USER en CI), porque el rol
de monitoreo solo tiene lectura: en el sandbox el DDL lo hace ql_user.
"""
import os
import sys
import time

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_postgres,
)

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


def load_postgres(rounds=40):
    engine = get_connection_postgres()
    with engine.begin() as conn:
        conn.execute(text(DDL))
        conn.execute(text(SEED))
        conn.execute(text(f"ANALYZE {SCHEMA}.ql_ci"))

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

    total = _count(engine, "SELECT count(*) FROM pg_stat_statements WHERE query LIKE '%ql_ci%'")
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
    """El rol de monitoreo no tiene CREATE en ql_demo: el DDL lo aplica quien sea
    dueno (en CI, root via docker exec). Aqui solo se intenta; si falla, se sigue,
    porque lo que importa es generar trafico para los digests."""
    engine = get_connection_mysql()
    with engine.begin() as conn:
        conn.execute(text("USE ql_demo"))
        for statement in (MY_DDL, MY_SEED, "ANALYZE TABLE ql_ci"):
            try:
                conn.execute(text(statement))
            except Exception as exc:
                print(
                    f"aviso mysql: {statement.split()[0:3]} fallo ({type(exc).__name__}); "
                    f"se asume que ql_ci ya existe",
                    file=sys.stderr,
                )

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
        engine,
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
