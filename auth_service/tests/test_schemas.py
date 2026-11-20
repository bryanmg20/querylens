"""database_name por motor (issue #79): obligatorio en PostgreSQL, ignorado en MySQL."""

import pytest
from pydantic import ValidationError

from app.schemas import ConnectionParams, DatabaseCreateRequest

BASE = {"host": "db.local", "port": 5432, "username": "monitor", "password": "secreto"}


def test_postgres_conserva_la_base():
    params = ConnectionParams(engine="postgresql", database_name="ventas", **BASE)
    assert params.database_name == "ventas"


@pytest.mark.parametrize("database_name", ["", "   "])
def test_postgres_sin_base_es_invalido(database_name):
    with pytest.raises(ValidationError, match="obligatorio en PostgreSQL"):
        ConnectionParams(engine="postgresql", database_name=database_name, **BASE)


def test_postgres_sin_el_campo_es_invalido():
    with pytest.raises(ValidationError, match="obligatorio en PostgreSQL"):
        ConnectionParams(engine="postgresql", **BASE)


def test_el_motor_por_defecto_es_postgres():
    with pytest.raises(ValidationError, match="obligatorio en PostgreSQL"):
        ConnectionParams(**BASE)


def test_mysql_sin_base_es_valido():
    params = ConnectionParams(engine="mysql", **{**BASE, "port": 3306})
    assert params.database_name == ""


def test_mysql_con_base_la_normaliza_a_vacia():
    # El pipeline observa el servidor completo: la base no identifica la conexion.
    params = ConnectionParams(engine="mysql", database_name="ventas", **{**BASE, "port": 3306})
    assert params.database_name == ""


def test_motor_desconocido_es_invalido():
    with pytest.raises(ValidationError):
        ConnectionParams(engine="oracle", database_name="x", **BASE)


@pytest.mark.parametrize("campo", ["host", "username", "password"])
def test_campos_de_conexion_no_pueden_ir_vacios(campo):
    with pytest.raises(ValidationError):
        ConnectionParams(engine="postgresql", database_name="x", **{**BASE, campo: ""})


@pytest.mark.parametrize("nombre", ["", "x" * 256])
def test_nombre_de_conexion_entre_1_y_255(nombre):
    with pytest.raises(ValidationError):
        DatabaseCreateRequest(connection_name=nombre, engine="postgresql", database_name="x", **BASE)
