"""Observabilidad del tamaño del payload (main._log_payload_size).

No es un gate: solo informa. Lo que se protege aqui es que el warning no se
repita cada ciclo mientras el estado persiste (~8 640 lineas al dia), que es
el patron que ya resolvio runner._report_state.
"""
import pytest

import main

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_warned():
    main._payload_warned.clear()
    yield
    main._payload_warned.clear()


def test_payload_size_is_logged_as_debug(caplog):
    with caplog.at_level("DEBUG", logger="main"):
        main._log_payload_size("postgres", "db-a", "x" * 42)
    debug = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert any("db_id=db-a" in r.getMessage() and "42 bytes" in r.getMessage() for r in debug)


def test_payload_warns_only_once_per_db_id(caplog):
    with caplog.at_level("WARNING", logger="main"):
        main._log_payload_size("mysql", "db-big", "y" * (main.PAYLOAD_WARN_BYTES + 1))
        main._log_payload_size("mysql", "db-big", "z" * (main.PAYLOAD_WARN_BYTES + 1))
        main._log_payload_size("mysql", "db-otra", "w" * (main.PAYLOAD_WARN_BYTES + 1))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2
    assert "db_id=db-big" in warnings[0].getMessage()
    assert "db_id=db-otra" in warnings[1].getMessage()


def test_payload_size_is_returned_in_utf8_bytes():
    assert main._log_payload_size("postgres", "db-a", "ñ" * 10) == 20


def test_below_threshold_never_warns(caplog):
    with caplog.at_level("WARNING", logger="main"):
        main._log_payload_size("postgres", "db-small", "x" * (main.PAYLOAD_WARN_BYTES - 1))
    assert not [r for r in caplog.records if r.levelname == "WARNING"]