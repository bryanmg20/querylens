"""Targets de credenciales leidos de registered_databases.

Fernet es real (clave generada por test) pero la base esta falsificada: no se
abre ninguna conexion, para que estas pruebas corran en el job de CI, que no
crea la tabla. El contrato de la consulta y el fallback a ENGINES tambien se
cubren aqui.
"""
import logging
from unittest import mock

import pytest
from cryptography.fernet import Fernet

import main
from config import registered
from models.snapshot import SnapshotPayload

pytestmark = pytest.mark.unit


@pytest.fixture
def fernet(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AUTH_ENCRYPTION_KEY", key)
    return Fernet(key.encode())


def _row(fernet, **overrides):
    row = {
        "database_identifier": "db_ab12cd34",
        "engine": "postgresql",
        "host": fernet.encrypt(b"db.example.com").decode(),
        "port": fernet.encrypt(b"5433").decode(),
        "db_user": fernet.encrypt(b"monitor").decode(),
        "encrypted_password": fernet.encrypt(b"p@ss:w/ord").decode(),
        "database_name": "app_prod",
    }
    row.update(overrides)
    return row


def _patch_db(monkeypatch, rows=(), error=None):
    engine = mock.MagicMock()
    if error is not None:
        engine.connect.side_effect = error
    else:
        ctx = engine.connect.return_value.__enter__.return_value
        ctx.execute.return_value.mappings.return_value.all.return_value = list(rows)
    monkeypatch.setattr(registered, "get_connection_querylens_db", lambda: engine)
    return engine


# ---------- la fila activa se convierte en un Target ----------


def test_fila_postgres_develop_url_e_identifier(fernet, monkeypatch):
    _patch_db(monkeypatch, [_row(fernet)])

    targets = registered.load_registered_targets()

    assert len(targets) == 1
    target = targets[0]
    assert target.dialect == "postgres"
    assert target.db_id == "db_ab12cd34"
    url = target.factory().url
    assert url.drivername == "postgresql+psycopg2"
    assert url.host == "db.example.com"
    assert url.port == 5433
    assert url.username == "monitor"
    assert url.password == "p@ss:w/ord", "URL.create debe preservar la password con @ y :"
    assert url.database == "app_prod"


def test_fila_mysql_va_sin_base(fernet, monkeypatch):
    _patch_db(monkeypatch, [_row(fernet, engine="mysql", database_name="tienda")])

    target = registered.load_registered_targets()[0]

    assert target.dialect == "mysql"
    assert target.db_id == "db_ab12cd34"
    assert target.factory().url.database is None, (
        "MySQL se conecta sin base: el USE lo emite ExplainStage"
    )


def test_engine_no_soportado_se_omite(fernet, monkeypatch, caplog):
    _patch_db(monkeypatch, [_row(fernet, engine="oracle")])

    with caplog.at_level(logging.ERROR):
        targets = registered.load_registered_targets()

    assert targets is None
    assert any("engine no soportado" in r.getMessage() for r in caplog.records)


def test_credencial_corrupta_no_tumba_las_demas(fernet, monkeypatch, caplog):
    mala = _row(fernet, database_identifier="db_mala0000", host="esto-no-es-fernet")
    buena = _row(fernet, database_identifier="db_buena00")
    _patch_db(monkeypatch, [mala, buena])

    with caplog.at_level(logging.ERROR):
        targets = registered.load_registered_targets()

    assert [t.db_id for t in targets] == ["db_buena00"]
    assert any("db_mala0000" in r.getMessage() for r in caplog.records)


def test_puerto_invalido_se_omite(fernet, monkeypatch):
    _patch_db(monkeypatch, [_row(fernet, port=fernet.encrypt(b"no-numerico").decode())])

    assert registered.load_registered_targets() is None


# ---------- sin tabla, sin clave: fallback a ENGINES ----------


def test_sin_tabla_devuelve_none(fernet, monkeypatch, caplog):
    _patch_db(monkeypatch, error=Exception('relation "registered_databases" does not exist'))

    with caplog.at_level(logging.WARNING):
        targets = registered.load_registered_targets()

    assert targets is None
    assert any("fallback a ENGINES" in r.getMessage() for r in caplog.records)


def test_sin_clave_devuelve_none(monkeypatch, caplog):
    monkeypatch.delenv("AUTH_ENCRYPTION_KEY", raising=False)

    with caplog.at_level(logging.WARNING):
        assert registered.load_registered_targets() is None
    assert any("sin AUTH_ENCRYPTION_KEY" in r.getMessage() for r in caplog.records)


def test_clave_invalida_devuelve_none(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEY", "esto-no-es-una-clave-fernet")

    with caplog.at_level(logging.ERROR):
        assert registered.load_registered_targets() is None
    assert any("AUTH_ENCRYPTION_KEY invalida" in r.getMessage() for r in caplog.records)


def test_sin_filas_activas_devuelve_none(fernet, monkeypatch):
    _patch_db(monkeypatch, [])

    assert registered.load_registered_targets() is None


def test_consulta_solo_filas_activas():
    """El filtro is_active lo aplica el SQL, no el codigo: si alguien lo
    quita de SELECT_ACTIVE, esta prueba lo atrapa."""
    assert "WHERE is_active" in registered.SELECT_ACTIVE
    assert "registered_databases" in registered.SELECT_ACTIVE


# ---------- main() solo lee la tabla ----------


def test_main_sin_registrados_no_extrae_nada(monkeypatch, caplog):
    """Sin filas activas no se extrae nada y el motivo queda en el log: el par
    del sandbox vive en main_sandbox.py, no aqui."""
    monkeypatch.setattr(main, "load_registered_targets", lambda: None)
    called = []
    monkeypatch.setattr(main, "run_engine", lambda *a, **k: called.append(a))

    with caplog.at_level(logging.WARNING):
        main.main()

    assert called == []
    assert any("sin bases registradas" in r.getMessage() for r in caplog.records)


def test_main_ejecuta_solo_los_registrados(monkeypatch):
    registrados = [registered.Target("mysql", lambda: None, "db_12345678")]
    monkeypatch.setattr(main, "load_registered_targets", lambda: registrados)
    called = []
    monkeypatch.setattr(
        main,
        "run_engine",
        lambda dialect, factory, db_id=None: called.append((dialect, db_id)),
    )

    main.main()

    assert called == [("mysql", "db_12345678")]


def test_main_aisla_un_target_que_revienta(monkeypatch, caplog):
    """Una fila que no conecta se loguea como engine_failed y la siguiente
    sigue: el aislamiento es por target, no por corrida."""
    registrados = [
        registered.Target("postgres", lambda: None, "db_malo0000"),
        registered.Target("mysql", lambda: None, "db_bueno00"),
    ]
    monkeypatch.setattr(main, "load_registered_targets", lambda: registrados)
    ok = []

    def run_engine(dialect, factory, db_id=None):
        if db_id == "db_malo0000":
            raise RuntimeError("motor caido a proposito")
        ok.append(db_id)

    monkeypatch.setattr(main, "run_engine", run_engine)

    with caplog.at_level(logging.ERROR):
        main.main()

    assert ok == ["db_bueno00"]
    assert any(
        "db_malo0000" in r.getMessage() and "engine_failed" in r.getMessage()
        for r in caplog.records
    )


def test_run_engine_rechaza_snapshot_invalido_sin_encolar(monkeypatch):
    """Un snapshot que no valida se traduce en None: run_engine no encola basura
    y no levanta (quien orquesta decide seguir con el siguiente target)."""
    sent = []
    orchestrator = mock.MagicMock()
    orchestrator.run_pipeline.return_value = {"db_id": "x", "statements": {"not": "list"}}
    monkeypatch.setattr(main, "Orchestrator", mock.MagicMock(return_value=orchestrator))
    monkeypatch.setattr(main, "Engine_Factory", mock.MagicMock())
    monkeypatch.setattr(main, "send_to_queue", lambda *a, **k: sent.append(a))

    result = main.run_engine("postgres", lambda: mock.MagicMock())

    assert result is None
    assert sent == [], "nada debe llegar a la cola con un snapshot invalido"


def test_main_no_arrastra_el_sandbox():
    """main.py no decide contra que bases correr: el par postgres/mysql es de
    main_sandbox.py y aqui no debe existir."""
    import main_sandbox

    assert not hasattr(main, "ENGINES")
    assert [dialect for dialect, _ in main_sandbox.ENGINES] == ["postgres", "mysql"]


def test_run_engine_usa_el_identifier_de_la_fila(monkeypatch, postgres_snapshot):
    """El database_identifier de la fila manda sobre la constante de enrich."""
    sent = {}

    def fake_send(payload_json, engine):
        sent["json"] = payload_json
        return 1

    orchestrator = mock.MagicMock()
    orchestrator.run_pipeline.side_effect = lambda: dict(postgres_snapshot)
    monkeypatch.setattr(main, "Orchestrator", mock.MagicMock(return_value=orchestrator))
    monkeypatch.setattr(main, "Engine_Factory", mock.MagicMock())
    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock())
    monkeypatch.setattr(main, "send_to_queue", fake_send)

    main.run_engine("postgres", lambda: mock.MagicMock(), db_id="db_12345678")
    assert SnapshotPayload.model_validate_json(sent["json"]).db_id == "db_12345678"

    main.run_engine("postgres", lambda: mock.MagicMock())
    assert SnapshotPayload.model_validate_json(sent["json"]).db_id == "querylens-db-01"
