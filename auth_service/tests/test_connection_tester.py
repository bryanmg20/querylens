"""Prueba de conexion: a que se conecta en cada motor (sin red, con drivers simulados)."""

import psycopg2
import pymysql
import pytest

from app import connection_tester
from app.schemas import ConnectionParams


class _Conn:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def capturas(monkeypatch):
    llamadas = {}

    def fake(driver):
        def connect(**kwargs):
            llamadas[driver] = kwargs
            return _Conn()

        return connect

    monkeypatch.setattr(connection_tester.psycopg2, "connect", fake("postgresql"))
    monkeypatch.setattr(connection_tester.pymysql, "connect", fake("mysql"))
    return llamadas


def test_postgres_se_conecta_a_su_base(capturas):
    params = ConnectionParams(engine="postgresql", host="h", port=5432, username="u", password="p", database_name="ventas")
    resultado = connection_tester.test_connection(params)
    assert resultado.success
    assert resultado.message == "Conexión exitosa"
    assert resultado.latency_ms is not None and resultado.latency_ms >= 0
    assert capturas["postgresql"]["dbname"] == "ventas"
    assert "mysql" not in capturas


def test_mysql_se_conecta_sin_base(capturas):
    # Issue #79: aunque el formulario mande una base, MySQL se prueba contra el servidor.
    params = ConnectionParams(engine="mysql", host="h", port=3306, username="u", password="p", database_name="ventas")
    assert connection_tester.test_connection(params).success
    assert capturas["mysql"]["database"] is None
    assert "postgresql" not in capturas


@pytest.mark.parametrize(
    ("engine", "driver", "error"),
    [
        ("postgresql", "psycopg2", psycopg2.OperationalError("password authentication failed\n")),
        ("mysql", "pymysql", pymysql.err.OperationalError(1045, "Access denied")),
    ],
)
def test_un_error_del_driver_vuelve_como_fallo(monkeypatch, engine, driver, error):
    def falla(**_):
        raise error

    monkeypatch.setattr(getattr(connection_tester, driver), "connect", falla)
    params = ConnectionParams(engine=engine, host="h", port=1, username="u", password="p", database_name="x")
    resultado = connection_tester.test_connection(params)
    assert not resultado.success
    assert resultado.message == str(error).strip()
    assert resultado.latency_ms is None
