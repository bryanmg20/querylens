import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_file_handler = None


def _build_file_handler():
    """Handler de archivo propio del pipeline.

    Se construye una sola vez. Si el directorio no es escribible se degrada a
    stderr en lugar de abortar el pipeline: perder el log no puede ser motivo
    para no entregar el snapshot.
    """
    global _file_handler
    if _file_handler is not None:
        return _file_handler

    try:
        LOGS_DIR.mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            LOGS_DIR / "pipeline.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name("pipeline-fallback")
        print(
            f"querylens | logger | no se pudo abrir {LOGS_DIR / 'pipeline.log'} ({exc}) "
            "| los logs van a stderr",
            file=sys.stderr,
        )

    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    _file_handler = handler
    return handler


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger con salida propia del pipeline.

    No se usa logging.basicConfig a proposito: es no-op cuando el root logger
    ya tiene handlers (pytest, o cualquier host que importe el pipeline), y eso
    dejaba al pipeline sin log sin avisar. El handler se engancha al logger del
    modulo y se desactiva propagate para que el pipeline escriba una sola vez en
    su archivo, sin duplicar en la configuracion del host.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = str(LOGS_DIR / "pipeline.log")
    already_writing = any(
        getattr(existing, "baseFilename", None) == target
        for existing in logger.handlers
    )
    if not already_writing:
        logger.addHandler(_build_file_handler())

    return logger
