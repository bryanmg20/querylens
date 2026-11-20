"""Configuracion de las pruebas de la API REST.

app.config lee las variables de entorno al importarse, asi que todo se
define aqui, antes de que una prueba importe `app`:

- JWT_SECRET_KEY: clave desechable; las pruebas firman sus propios tokens
  con ella, como lo haria el auth service.
- La base: las pruebas que usan Postgres (marca `db`) corren contra una base
  temporal creada en el servidor de QL_TEST_PG_HOST/PORT/USER/PASSWORD con el
  DDL real de lo que lee GET /diagnostics (registered_databases, problemas y
  detecciones, estado_evaluacion y pipeline_health), y se borra al final.
  Sin esas variables, o si el servidor no responde, esas pruebas se saltan;
  con QL_TEST_DB_REQUIRED=1 (el CI) fallan en vez de saltarse.

  QL_TEST_PG_HOST=127.0.0.1 QL_TEST_PG_PORT=5432 QL_TEST_PG_USER=postgres \
  QL_TEST_PG_PASSWORD=postgres python -m pytest
"""

import os
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg2
import pytest

SQL_DIR = Path(__file__).resolve().parents[2] / "querylens_database"
DDL = ["registered_databases.sql", "hallazgos.sql", "estado_evaluacion.sql", "pipeline_health.sql"]

JWT_SECRET = "clave-de-prueba-" + uuid.uuid4().hex
os.environ["JWT_SECRET_KEY"] = JWT_SECRET

_PG = {
    "host": os.environ.get("QL_TEST_PG_HOST"),
    "port": int(os.environ.get("QL_TEST_PG_PORT", "5432")),
    "user": os.environ.get("QL_TEST_PG_USER", "postgres"),
    "password": os.environ.get("QL_TEST_PG_PASSWORD", ""),
}
_TEST_DB = f"ql_test_api_{uuid.uuid4().hex[:8]}"
_db_error: str | None = None


def _admin_connection():
    conn = psycopg2.connect(dbname="postgres", connect_timeout=5, **_PG)
    conn.autocommit = True
    return conn


def pytest_configure(config):
    """Crea la base temporal con el DDL real y apunta la API a ella."""

    global _db_error
    if not _PG["host"]:
        _db_error = "sin QL_TEST_PG_HOST"
        return
    try:
        with closing(_admin_connection()) as admin, admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{_TEST_DB}"')
        with closing(psycopg2.connect(dbname=_TEST_DB, connect_timeout=5, **_PG)) as conn:
            with conn, conn.cursor() as cur:
                for name in DDL:
                    cur.execute((SQL_DIR / name).read_text(encoding="utf-8"))
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
    # Las conexiones del pool de la API tienen que cerrarse antes del DROP.
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


def make_token(sub: str | None = "db_test0001", *, key: str = JWT_SECRET, expires_in: timedelta = timedelta(hours=1)) -> str:
    """Un access token como el que firma el auth service."""

    claims = {"exp": datetime.now(timezone.utc) + expires_in}
    if sub is not None:
        claims["sub"] = sub
    return jwt.encode(claims, key, algorithm="HS256")


@pytest.fixture
def db_conn():
    """Conexion directa a la base temporal, con las tablas vacias."""

    conn = psycopg2.connect(dbname=_TEST_DB, **_PG)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE registered_databases, public.problemas, public.detecciones, public.estado_evaluacion, "
            "pipeline_health.last_run, pipeline_health.issues RESTART IDENTITY CASCADE"
        )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(db_conn):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
