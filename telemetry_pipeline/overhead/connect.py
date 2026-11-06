"""Conexiones DBAPI de la medicion: carga y admin del sandbox.

No usan config.connections a proposito: ese modulo arma las conexiones del
rol monitor (las del pipeline). La carga corre con el rol de la aplicacion y
los toggles de instrumentacion necesitan superusuario/root. Defaults = sandbox
(ql_sandbox/docker-compose.yml); se pisan por entorno.
"""
import os

import psycopg2
import pymysql


def _env(name, default):
    value = os.getenv(name)
    return default if value is None or value == "" else value


# toggles.PgAutoExplain termina las sesiones de la carga por este nombre.
LOAD_APPLICATION_NAME = "querylens_overhead_load"


def pg_user(role="LOAD"):
    return _env(f"OVERHEAD_PG_{role}_USER", "ql_user")


def pg_connect(role="LOAD"):
    """role = LOAD (ql_user, la app) o ADMIN (superusuario para ALTER ROLE)."""
    conn = psycopg2.connect(
        host=_env(f"OVERHEAD_PG_{role}_HOST", "localhost"),
        port=int(_env(f"OVERHEAD_PG_{role}_PORT", "5432")),
        user=pg_user(role),
        password=_env(f"OVERHEAD_PG_{role}_PASSWORD", "ql_pass"),
        dbname=_env(f"OVERHEAD_PG_{role}_DB", "ql_demo"),
        connect_timeout=10,
        application_name=f"querylens_overhead_{role.lower()}",
    )
    conn.autocommit = True
    return conn


_MY_DEFAULTS = {
    "LOAD": ("app_user", "app_pass", "ql_demo"),
    "ADMIN": ("root", "ql_root", None),
}


def my_connect(role="LOAD"):
    user, password, db = _MY_DEFAULTS[role]
    return pymysql.connect(
        host=_env(f"OVERHEAD_MY_{role}_HOST", "127.0.0.1"),
        port=int(_env(f"OVERHEAD_MY_{role}_PORT", "3307")),
        user=_env(f"OVERHEAD_MY_{role}_USER", user),
        password=_env(f"OVERHEAD_MY_{role}_PASSWORD", password),
        database=_env(f"OVERHEAD_MY_{role}_DB", db or "") or None,
        connect_timeout=10,
        autocommit=True,
    )


CONNECT = {"postgres": pg_connect, "mysql": my_connect}
