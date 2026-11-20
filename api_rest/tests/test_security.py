"""Verificacion del access token que firma el auth service."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.security import get_database_identifier
from conftest import make_token


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_token_valido_devuelve_el_identificador():
    assert get_database_identifier(bearer(make_token("db_1234abcd"))) == "db_1234abcd"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "basura",
        make_token(key="otra-clave"),
        make_token(expires_in=timedelta(seconds=-1)),
        make_token(sub=None),
    ],
    ids=["sin-token", "basura", "otra-clave", "vencido", "sin-sub"],
)
def test_token_invalido_da_401(token):
    with pytest.raises(HTTPException) as exc:
        get_database_identifier(bearer(token) if token else None)
    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}
