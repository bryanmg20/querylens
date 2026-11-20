"""Configuracion de las pruebas del auth service.

app.config lee las variables de entorno al importarse, asi que todo se
define aqui, antes de que una prueba importe `app`:

- AUTH_ENCRYPTION_KEY y JWT_SECRET_KEY: claves desechables de la corrida.
- La base: las pruebas que usan Postgres (marca `db`) corren contra una base
  temporal creada en el servidor de QL_TEST_PG_HOST/PORT/USER/PASSWORD con el
  DDL real (querylens_database/registered_databases.sql), y se borra al final.
  Sin esas variables, o si el servidor no responde, esas pruebas se saltan;
  con QL_TEST_DB_REQUIRED=1 (el CI) fallan en vez de saltarse.

  QL_TEST_PG_HOST=127.0.0.1 QL_TEST_PG_PORT=5432 QL_TEST_PG_USER=postgres \
  QL_TEST_PG_PASSWORD=postgres python -m pytest
"""

import os
import uuid
from contextlib import closing
from pathlib import Path

import psycopg2
import pytest
from cryptography.fernet import Fernet

DDL = Path(__file__).resolve().parents[2] / "querylens_database" / "registered_databases.sql"

os.environ["AUTH_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["JWT_SECRET_KEY"] = "clave-de-prueba-" + uuid.uuid4().hex

_PG = {
    "host": os.environ.get("QL_TEST_PG_HOST"),
    "port": int(os.environ.get("QL_TEST_PG_PORT", "5432")),
    "user": os.environ.get("QL_TEST_PG_USER", "postgres"),
    "password": os.environ.get("QL_TEST_PG_PASSWORD", ""),
}
_TEST_DB = f"ql_test_auth_{uuid.uuid4().hex[:8]}"
_db_error: str | None = None


def _admin_connection():
    conn = psycopg2.connect(dbname="postgres", connect_timeout=5, **_PG)
    conn.autocommit = True
    return conn


def pytest_configure(config):
    """Crea la base temporal con el DDL real y apunta el servicio a ella."""

    global _db_error
    if not _PG["host"]:
        _db_error = "sin QL_TEST_PG_HOST"
        return
    try:
        with closing(_admin_connection()) as admin, admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{_TEST_DB}"')
        with closing(psycopg2.connect(dbname=_TEST_DB, connect_timeout=5, **_PG)) as conn:
            with conn, conn.cursor() as cur:
                cur.execute(DDL.read_text(encoding="utf-8"))
    except psycopg2.Error as exc:
        _db_error = f"Postgres de prueba no disponible: {exc}".strip()
        return

    os.environ.update(
        DB_HOST=_PG["host"],
        DB_PORT=str(_PG["port"]),
        QUERYLENS_DB=_TEST_DB,
        QUERYLENS_USER=_PG["user"],
        QUERYLENS_PASSWORD=_PG["password"],
    )


def pytest_unconfigure(config):
    if _db_error is not None:
        return
    # Las conexiones del pool del servicio tienen que cerrarse antes del DROP.
    try:
        from app.database import engine

        engine.dispose()
    except Exception:
        pass
    with closing(_admin_connection()) as admin, admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)')


def pytest_collection_modifyitems(config, items):
    if _db_error is None:
        return
    if os.environ.get("QL_TEST_DB_REQUIRED") == "1":
        raise pytest.UsageError(f"QL_TEST_DB_REQUIRED=1 pero {_db_error}")
    skip = pytest.mark.skip(reason=_db_error)
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def db_conn():
    """Conexion directa a la base temporal, para preparar y revisar filas."""

    conn = psycopg2.connect(dbname=_TEST_DB, **_PG)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(db_conn):
    """Cliente HTTP del servicio con la tabla vacia.

    Sin `with TestClient(...)`: asi no corre el lifespan, que arranca el
    chequeo periodico de disponibilidad contra las bases registradas.
    """

    from fastapi.testclient import TestClient

    from app.main import app

    with db_conn.cursor() as cur:
        cur.execute("TRUNCATE registered_databases")
    return TestClient(app)
