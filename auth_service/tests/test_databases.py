"""Endpoints del auth service contra Postgres (tabla de querylens_database/registered_databases.sql)."""

from datetime import timedelta

import pytest

from app import connection_tester
from app.routers import databases as router_module
# Con otro nombre: pytest intentaria recolectar una clase que empieza con "Test".
from app.schemas import TestConnectionResponse as ConnectionResult
from app.security import create_access_token, decrypt_secret

pytestmark = pytest.mark.db

PG = {
    "connection_name": "ventas prod",
    "engine": "postgresql",
    "host": "db.local",
    "port": 5432,
    "username": "querylens_monitor",
    "password": "secreto-1",
    "database_name": "ventas",
}
MY = {**PG, "engine": "mysql", "port": 3306, "connection_name": "mysql prod"}


def register(client, payload):
    return client.post("/databases", json=payload)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def fila(db_conn, identifier: str) -> dict:
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM registered_databases WHERE database_identifier = %s", (identifier,))
        cols = [c.name for c in cur.description]
        row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def filas(db_conn) -> int:
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM registered_databases")
        return cur.fetchone()[0]


# --- salud ---------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- registro ------------------------------------------------------------


def test_registrar_postgres_crea_la_fila(client, db_conn):
    r = register(client, PG)
    assert r.status_code == 201
    body = r.json()
    assert body["engine"] == "postgresql"
    assert body["host"] == "db.local"
    assert body["port"] == 5432
    assert body["database_name"] == "ventas"
    assert body["is_active"] is True
    # Una base nueva nace con el monitoreo apagado.
    assert body["monitoring_enabled"] is False
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    row = fila(db_conn, body["database_identifier"])
    # host, puerto, usuario y contrasena no quedan en texto plano.
    for columna, plano in [("host", "db.local"), ("port", "5432"), ("db_user", "querylens_monitor"), ("encrypted_password", "secreto-1")]:
        assert row[columna] != plano
        assert decrypt_secret(row[columna]) == plano
    assert len(row["connection_fingerprint"]) == 64


def test_el_token_del_registro_sirve_para_su_base(client):
    body = register(client, PG).json()
    r = client.patch("/databases/monitoring", json={"enabled": True}, headers=auth(body["access_token"]))
    assert r.status_code == 200
    assert r.json()["database_identifier"] == body["database_identifier"]


def test_registrar_dos_veces_devuelve_la_misma_fila_con_200(client, db_conn):
    primero = register(client, PG).json()
    r = register(client, {**PG, "connection_name": "otro nombre", "password": "secreto-2"})
    assert r.status_code == 200
    segundo = r.json()
    assert segundo["database_identifier"] == primero["database_identifier"]
    assert segundo["id"] == primero["id"]
    assert filas(db_conn) == 1
    # Actualiza nombre y contrasena de la misma fila.
    row = fila(db_conn, primero["database_identifier"])
    assert row["connection_name"] == "otro nombre"
    assert decrypt_secret(row["encrypted_password"]) == "secreto-2"


def test_mayusculas_y_espacios_no_crean_otra_fila(client, db_conn):
    primero = register(client, PG).json()
    r = register(client, {**PG, "host": "  DB.LOCAL ", "username": "QUERYLENS_MONITOR"})
    assert r.status_code == 200
    assert r.json()["database_identifier"] == primero["database_identifier"]
    assert filas(db_conn) == 1


@pytest.mark.parametrize("cambio", [{"host": "otro.local"}, {"port": 5433}, {"username": "otro"}, {"database_name": "compras"}])
def test_otra_conexion_postgres_es_otra_fila(client, db_conn, cambio):
    primero = register(client, PG).json()
    r = register(client, {**PG, **cambio})
    assert r.status_code == 201
    assert r.json()["database_identifier"] != primero["database_identifier"]
    assert filas(db_conn) == 2


def test_registrar_reactiva_una_base_marcada_no_disponible(client, db_conn):
    body = register(client, PG).json()
    with db_conn.cursor() as cur:
        cur.execute("UPDATE registered_databases SET is_active = false")
    r = register(client, PG)
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert fila(db_conn, body["database_identifier"])["is_active"] is True


def test_postgres_sin_base_responde_422(client, db_conn):
    # Issue #79, criterio 3.
    for payload in ({**PG, "database_name": ""}, {k: v for k, v in PG.items() if k != "database_name"}):
        r = register(client, payload)
        assert r.status_code == 422
        assert "obligatorio en PostgreSQL" in r.text
    assert filas(db_conn) == 0


@pytest.mark.parametrize("cambio", [{"engine": "oracle"}, {"port": "no-es-numero"}, {"host": ""}, {"connection_name": ""}])
def test_datos_invalidos_responden_422(client, cambio):
    assert register(client, {**PG, **cambio}).status_code == 422


# --- MySQL sin base (issue #79) -------------------------------------------


def test_mysql_sin_base_se_registra(client, db_conn):
    # Criterio 1: el registro acepta MySQL sin base y guarda "".
    r = register(client, {k: v for k, v in MY.items() if k != "database_name"})
    assert r.status_code == 201
    body = r.json()
    assert body["engine"] == "mysql"
    assert body["database_name"] == ""
    assert fila(db_conn, body["database_identifier"])["database_name"] == ""


def test_mysql_mismo_servidor_con_otra_base_es_duplicado(client, db_conn):
    # Criterio 2: el mismo servidor MySQL responde duplicado (200, misma fila) aunque cambie la base.
    primero = register(client, {**MY, "database_name": "ventas"})
    assert primero.status_code == 201
    for base in ("compras", "", "VENTAS"):
        r = register(client, {**MY, "database_name": base})
        assert r.status_code == 200
        assert r.json()["database_identifier"] == primero.json()["database_identifier"]
    assert filas(db_conn) == 1


def test_mysql_otro_servidor_es_otra_fila(client, db_conn):
    register(client, MY)
    assert register(client, {**MY, "port": 3307}).status_code == 201
    assert filas(db_conn) == 2


def test_mysql_y_postgres_del_mismo_host_son_filas_distintas(client, db_conn):
    register(client, MY)
    assert register(client, {**PG, "port": 3306}).status_code == 201
    assert filas(db_conn) == 2


def test_choque_de_identificador_reintenta(client, db_conn, monkeypatch):
    existente = register(client, PG).json()["database_identifier"]
    nuevos = iter([existente, "db_00000002"])
    monkeypatch.setattr(router_module, "generate_database_identifier", lambda: next(nuevos))
    r = register(client, {**PG, "database_name": "compras"})
    assert r.status_code == 201
    assert r.json()["database_identifier"] == "db_00000002"


def test_sin_identificador_libre_responde_500(client, monkeypatch):
    existente = register(client, PG).json()["database_identifier"]
    monkeypatch.setattr(router_module, "generate_database_identifier", lambda: existente)
    r = register(client, {**PG, "database_name": "compras"})
    assert r.status_code == 500


# --- prueba de conexion ----------------------------------------------------


def test_test_connection_pasa_los_datos_normalizados(client, monkeypatch):
    recibido = {}

    def fake(params):
        recibido["params"] = params
        return ConnectionResult(success=True, message="Conexión exitosa", latency_ms=1.0)

    monkeypatch.setattr(router_module, "run_connection_test", fake)
    r = client.post("/databases/test-connection", json={**MY, "database_name": "ventas"})
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Conexión exitosa", "latency_ms": 1.0}
    assert recibido["params"].engine == "mysql"
    assert recibido["params"].database_name == ""


def test_test_connection_postgres_sin_base_responde_422(client):
    r = client.post("/databases/test-connection", json={**PG, "database_name": ""})
    assert r.status_code == 422


def test_test_connection_informa_el_error_del_motor(client, monkeypatch):
    import psycopg2

    def falla(**_):
        raise psycopg2.OperationalError("could not translate host name")

    monkeypatch.setattr(connection_tester.psycopg2, "connect", falla)
    r = client.post("/databases/test-connection", json=PG)
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "could not translate host name" in r.json()["message"]


# --- monitoreo ------------------------------------------------------------


def set_monitoring(client, token, **body):
    return client.patch("/databases/monitoring", json=body, headers=auth(token))


def test_monitoreo_sin_token_responde_401(client):
    r = client.patch("/databases/monitoring", json={"enabled": True})
    assert r.status_code == 401


def test_monitoreo_con_token_invalido_responde_401(client):
    assert set_monitoring(client, "basura", enabled=True).status_code == 401


def test_monitoreo_de_una_base_que_ya_no_existe_responde_404(client):
    r = set_monitoring(client, create_access_token("db_noexiste"), enabled=True)
    assert r.status_code == 404


def test_iniciar_y_pausar_el_monitoreo(client, db_conn):
    body = register(client, PG).json()
    token = body["access_token"]
    r = set_monitoring(client, token, enabled=True)
    assert r.status_code == 200
    assert r.json()["monitoring_enabled"] is True
    assert fila(db_conn, body["database_identifier"])["monitoring_enabled"] is True
    assert set_monitoring(client, token, enabled=False).json()["monitoring_enabled"] is False


def test_registrar_de_nuevo_conserva_el_estado_del_monitoreo(client):
    token = register(client, PG).json()["access_token"]
    set_monitoring(client, token, enabled=True)
    assert register(client, PG).json()["monitoring_enabled"] is True
    set_monitoring(client, token, enabled=False)
    assert register(client, PG).json()["monitoring_enabled"] is False


def test_sesion_nueva_al_iniciar_mueve_el_inicio(client, db_conn):
    body = register(client, PG).json()
    with db_conn.cursor() as cur:
        cur.execute("UPDATE registered_databases SET detection_session_started_at = now() - interval '1 day'")
    antes = fila(db_conn, body["database_identifier"])["detection_session_started_at"]

    r = set_monitoring(client, body["access_token"], enabled=True, new_session=True)
    assert r.status_code == 200
    despues = fila(db_conn, body["database_identifier"])["detection_session_started_at"]
    assert despues - antes > timedelta(hours=23)


def test_iniciar_sin_sesion_nueva_no_mueve_el_inicio(client, db_conn):
    body = register(client, PG).json()
    antes = fila(db_conn, body["database_identifier"])["detection_session_started_at"]
    set_monitoring(client, body["access_token"], enabled=True)
    assert fila(db_conn, body["database_identifier"])["detection_session_started_at"] == antes


def test_sesion_nueva_al_pausar_responde_400(client):
    token = register(client, PG).json()["access_token"]
    r = set_monitoring(client, token, enabled=False, new_session=True)
    assert r.status_code == 400


def test_sesion_nueva_con_el_monitoreo_activo_responde_409(client):
    token = register(client, PG).json()["access_token"]
    set_monitoring(client, token, enabled=True)
    r = set_monitoring(client, token, enabled=True, new_session=True)
    assert r.status_code == 409


def test_el_token_solo_actua_sobre_su_base(client, db_conn):
    a = register(client, PG).json()
    b = register(client, {**PG, "database_name": "compras"}).json()
    set_monitoring(client, a["access_token"], enabled=True)
    assert fila(db_conn, a["database_identifier"])["monitoring_enabled"] is True
    assert fila(db_conn, b["database_identifier"])["monitoring_enabled"] is False
