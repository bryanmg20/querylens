"""Cifrado de credenciales, huella de la conexion y access token."""

import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.config import settings
from app.schemas import ConnectionParams
from app.security import (
    compute_connection_fingerprint,
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    generate_database_identifier,
    get_database_identifier,
)

CONN = {"engine": "postgresql", "host": "db.local", "port": 5432, "username": "monitor", "database_name": "ventas"}


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# --- cifrado -------------------------------------------------------------


def test_cifrar_y_descifrar():
    secreto = "p@ss:w/rd?ñ"
    cifrado = encrypt_secret(secreto)
    assert cifrado != secreto
    assert decrypt_secret(cifrado) == secreto


def test_el_cifrado_no_es_deterministico():
    # Por eso hace falta la huella para detectar duplicados.
    assert encrypt_secret("db.local") != encrypt_secret("db.local")


def test_descifrar_algo_invalido_da_value_error():
    with pytest.raises(ValueError, match="descifrar"):
        decrypt_secret("no-es-un-token-fernet")


# --- huella --------------------------------------------------------------


def test_la_huella_es_deterministica():
    assert compute_connection_fingerprint(**CONN) == compute_connection_fingerprint(**CONN)
    assert re.fullmatch(r"[0-9a-f]{64}", compute_connection_fingerprint(**CONN))


def test_la_huella_ignora_mayusculas_y_espacios():
    variante = {**CONN, "engine": " PostgreSQL ", "host": " DB.Local", "username": "Monitor ", "database_name": "VENTAS"}
    assert compute_connection_fingerprint(**variante) == compute_connection_fingerprint(**CONN)


@pytest.mark.parametrize(
    "cambio",
    [{"engine": "mysql"}, {"host": "otro.local"}, {"port": 5433}, {"username": "otro"}, {"database_name": "compras"}],
)
def test_la_huella_cambia_con_cada_dato_de_la_conexion(cambio):
    assert compute_connection_fingerprint(**{**CONN, **cambio}) != compute_connection_fingerprint(**CONN)


def test_la_huella_depende_de_la_clave():
    # HMAC con AUTH_ENCRYPTION_KEY: sin la clave no se puede recalcular.
    import hashlib
    import hmac

    plano = "|".join(["postgresql", "db.local", "5432", "monitor", "ventas"])
    sin_clave = hmac.new(b"otra-clave", plano.encode(), hashlib.sha256).hexdigest()
    assert compute_connection_fingerprint(**CONN) != sin_clave


def test_mysql_mismo_servidor_con_otra_base_misma_huella():
    # Issue #79: el schema normaliza la base de MySQL a "" antes de la huella.
    a = ConnectionParams(engine="mysql", host="db.local", port=3306, username="monitor", password="x", database_name="ventas")
    b = ConnectionParams(engine="mysql", host="db.local", port=3306, username="monitor", password="x", database_name="compras")
    huella = lambda p: compute_connection_fingerprint(  # noqa: E731
        engine=p.engine, host=p.host, port=p.port, username=p.username, database_name=p.database_name
    )
    assert huella(a) == huella(b)


# --- identificador y token -----------------------------------------------


def test_identificador_de_base():
    ident = generate_database_identifier()
    assert re.fullmatch(r"db_[0-9a-f]{8}", ident)
    assert generate_database_identifier() != ident


def test_el_token_lleva_el_identificador_y_vence():
    claims = jwt.decode(create_access_token("db_1234abcd"), settings.jwt_secret_key, algorithms=["HS256"])
    assert claims["sub"] == "db_1234abcd"
    duracion = claims["exp"] - claims["iat"]
    assert duracion == settings.jwt_expire_minutes * 60


def test_token_valido_devuelve_el_identificador():
    assert get_database_identifier(bearer(create_access_token("db_1234abcd"))) == "db_1234abcd"


def _token(claims: dict, key: str | None = None) -> str:
    return jwt.encode(claims, key or settings.jwt_secret_key, algorithm="HS256")


@pytest.mark.parametrize(
    "credencial",
    [
        None,
        "basura",
        # firmado con otra clave
        _token({"sub": "db_x", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, key="otra-clave"),
        # vencido
        _token({"sub": "db_x", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}),
        # sin exp
        _token({"sub": "db_x"}),
        # sin sub
        _token({"exp": datetime.now(timezone.utc) + timedelta(hours=1)}),
    ],
    ids=["sin-token", "basura", "otra-clave", "vencido", "sin-exp", "sin-sub"],
)
def test_token_invalido_da_401(credencial):
    with pytest.raises(HTTPException) as exc:
        get_database_identifier(bearer(credencial) if credencial else None)
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}
