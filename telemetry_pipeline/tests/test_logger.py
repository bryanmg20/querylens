import logging

import pytest

import logger as logger_module
from logger import get_logger

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Apunta LOGS_DIR a un temporal y restaura el handler global al final."""
    original_handler = logger_module._file_handler
    monkeypatch.setattr(logger_module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(logger_module, "_file_handler", None)
    yield tmp_path / "logs"
    logger_module._file_handler = original_handler


def _fresh(name):
    log = logging.getLogger(name)
    log.handlers = []
    return log


def test_writes_to_pipeline_log_when_directory_is_creatable(isolated, tmp_path):
    log = _fresh("test.logger.file")
    get_logger(log.name)
    log.info("mensaje de prueba")

    log_file = isolated / "pipeline.log"
    assert log_file.exists()
    assert "mensaje de prueba" in log_file.read_text(encoding="utf-8")


def test_falls_back_to_stderr_when_directory_cannot_be_created(isolated, capsys):
    isolated.write_text("soy un archivo, no un directorio", encoding="utf-8")
    log = _fresh("test.logger.fallback")
    get_logger(log.name)
    log.info("mensaje de prueba")

    captured = capsys.readouterr()
    assert "mensaje de prueba" in captured.err
    assert "no se pudo abrir" in captured.err


def test_fallback_handler_is_named_and_writes_to_stderr(isolated):
    isolated.write_text("bloqueado", encoding="utf-8")
    log = _fresh("test.stderr.handler")
    get_logger(log.name)
    assert [h.get_name() for h in log.handlers] == ["pipeline-fallback"]
    assert isinstance(log.handlers[0], logging.StreamHandler)


def test_file_handler_is_built_only_once(isolated):
    isolated.mkdir(parents=True, exist_ok=True)
    first = logger_module._build_file_handler()
    second = logger_module._build_file_handler()
    assert first is second


def test_repeated_calls_do_not_duplicate_handlers(isolated):
    isolated.mkdir(parents=True, exist_ok=True)
    log = _fresh("test.logger.dup")
    get_logger(log.name)
    get_logger(log.name)
    get_logger(log.name)
    assert len(log.handlers) == 1


def test_pipeline_does_not_propagate_to_root_logger(isolated):
    log = _fresh("test.logger.propagate")
    get_logger(log.name)
    assert log.propagate is False


def test_level_is_info(isolated):
    log = _fresh("test.logger.level")
    get_logger(log.name)
    assert log.level == logging.INFO


def test_level_follows_ql_log_level_env(isolated, monkeypatch):
    monkeypatch.setenv("QL_LOG_LEVEL", "DEBUG")
    log = _fresh("test.logger.env")
    get_logger(log.name)
    assert log.level == logging.DEBUG


def test_invalid_ql_log_level_falls_back_to_info(isolated, monkeypatch):
    monkeypatch.setenv("QL_LOG_LEVEL", "GARBAGE")
    log = _fresh("test.logger.bad_env")
    get_logger(log.name)
    assert log.level == logging.INFO


def test_log_file_is_rotated_not_grown_forever(isolated):
    log = _fresh("test.logger.rotate")
    get_logger(log.name)
    handler = log.handlers[0]
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == logger_module.MAX_BYTES
    assert handler.backupCount == logger_module.BACKUP_COUNT
