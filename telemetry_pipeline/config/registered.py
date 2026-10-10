"""Targets de extraccion leidos de registered_databases.

El front y el auth service registran las bases del cliente en
``registered_databases`` (querylens_database/registered_databases.sql) con
host, puerto, usuario y password cifrados con Fernet; solo las filas con
``is_active`` son candidatas. Este modulo hace ese select con la conexion de
la cola (ql_user es dueño de la tabla), descifra con AUTH_ENCRYPTION_KEY y
devuelve un Target por fila, para que main() ejecute el pipeline contra cada
una.

Si la tabla no existe (CI), no hay filas activas o falta la clave, devuelve
None y main() no extrae nada (el motivo queda en el log). No hay fallback a
los ENGINES fijos del sandbox: son rutas separadas (main vs main_sandbox);
implementar un fallback real queda pendiente, ver CODE_REVIEW M-6.
"""
import os
from dataclasses import dataclass
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import OperationalError, ProgrammingError

from config.connections import (
    _make_url,
    get_connection_querylens_db,
    mysql_connect_args,
    postgres_connect_args,
)
from health import writer as health_writer
from logger import get_logger

logger = get_logger(__name__)

_QUEUE_ENGINE: Engine | None = None


def _get_queue_engine() -> Engine:
    """Singleton engine para la base de la cola (ql_demo).
    Evita recrear pools cada 10s en runner.py."""
    global _QUEUE_ENGINE
    if _QUEUE_ENGINE is None:
        _QUEUE_ENGINE = get_connection_querylens_db()
    return _QUEUE_ENGINE


def _reset_queue_engine() -> None:
    """Solo para tests: permite reinyectar un mock en el proximo ciclo."""
    global _QUEUE_ENGINE
    if _QUEUE_ENGINE is not None:
        _QUEUE_ENGINE.dispose()
    _QUEUE_ENGINE = None


SELECT_ACTIVE = """
    SELECT database_identifier, engine, host, port, db_user,
           encrypted_password, database_name
    FROM registered_databases
    WHERE is_active
"""

# El auth service solo admite Literal["postgresql", "mysql"]; el pipeline
# habla en dialectos de Engine_Factory, que espera "postgres" y "mysql".
DIALECTS = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
}


@dataclass(frozen=True)
class Target:
    """Una base a la que apuntar el pipeline."""

    dialect: str
    factory: Callable[[], Engine]
    # database_identifier de la fila; None => constante DB_ID (ENGINES fijos).
    db_id: str | None = None


def _decipher(fernet: Fernet, token: str) -> str:
    return fernet.decrypt((token or "").encode()).decode()


def _record(identifier: str, code: str, **params) -> None:
    """La fila no llega a correr: el cliente igual tiene que ver por que.
    Sin database_identifier no hay a quien atribuirlo."""
    if identifier != "?":
        health_writer.record_target_issue(_get_queue_engine(), identifier, code, **params)


def _build_target(row: dict, fernet: Fernet) -> Target | None:
    identifier = row.get("database_identifier") or "?"

    dialect = DIALECTS.get((row.get("engine") or "").strip().lower())
    if dialect is None:
        logger.error(
            f"registered_targets | {identifier} | engine no soportado: {row.get('engine')!r}"
        )
        _record(identifier, "TARGET_CONFIG_INVALID", field="engine")
        return None

    try:
        host = _decipher(fernet, row.get("host"))
        port_raw = _decipher(fernet, row.get("port")).strip()
        user = _decipher(fernet, row.get("db_user"))
        password = _decipher(fernet, row.get("encrypted_password"))
    except (InvalidToken, TypeError, ValueError) as exc:
        logger.error(f"registered_targets | {identifier} | credencial indecifrable: {exc}")
        _record(identifier, "CREDENTIALS_UNREADABLE", exc_type=type(exc).__name__)
        return None

    try:
        port = int(port_raw) if port_raw else None
    except ValueError:
        logger.error(f"registered_targets | {identifier} | puerto invalido: {port_raw!r}")
        _record(identifier, "TARGET_CONFIG_INVALID", field="port")
        return None

    url = _target_url(dialect, user, password, host, port, row.get("database_name"))
    return Target(dialect=dialect, factory=lambda url=url: _engine(dialect, url), db_id=identifier)


def _target_url(
    dialect: str, user: str, password: str, host: str, port: int | None, database_name: str
) -> URL:
    # La password viene de Fernet y puede contener '@', ':', '?' o '/': el
    # helper de connections usa URL.create y cada componente viaja por separado.
    driver = "postgresql+psycopg2" if dialect == "postgres" else "mysql+pymysql"
    # MySQL se conecta sin base a proposito: ExplainStage emite el USE
    # (mismo contrato que get_connection_mysql, ver test_connections.py).
    database = database_name if dialect == "postgres" else ""
    return _make_url(
        drivername=driver,
        host=host,
        port=port,
        username=user,
        password=password,
        database=database,
    )


def _engine(dialect: str, url: URL) -> Engine:
    if dialect == "mysql":
        return create_engine(
            url,
            pool_size=1,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
            connect_args=mysql_connect_args(),
        )
    return create_engine(url, connect_args=postgres_connect_args())


def load_registered_targets() -> list[Target] | None:
    """Targets activos de registered_databases, o None cuando no hay con que
    trabajar (entonces main() no extrae nada y el motivo queda en el log).

    Devuelve None (no una lista vacia) cuando no hay con que trabajar: tabla
    inexistente, sin filas activas, sin AUTH_ENCRYPTION_KEY o con la clave
    ilegible. El motivo queda en el log.
    """
    key = os.getenv("AUTH_ENCRYPTION_KEY")
    if not key:
        logger.warning("registered_targets | sin AUTH_ENCRYPTION_KEY | no se extrae nada")
        return None

    try:
        fernet = Fernet(key.encode())
    except ValueError as exc:
        logger.error(
            f"registered_targets | AUTH_ENCRYPTION_KEY invalida: {exc} | no se extrae nada"
        )
        return None

    try:
        with _get_queue_engine().connect() as conn:
            rows = conn.execute(text(SELECT_ACTIVE)).mappings().all()
    except ProgrammingError as exc:
        # Tabla no existe (CI/sandbox limpio): no es error del pipeline.
        logger.warning(
            f"registered_targets | tabla registered_databases no existe "
            f"({type(exc).__name__}) | no se extrae nada"
        )
        return None
    except OperationalError as exc:
        # Falla real de conexion (red, auth, timeout, etc.): esto ES un error.
        logger.error(
            f"registered_targets | fallo de conexion a la cola "
            f"({type(exc).__name__}: {str(exc)[:120]}) | no se extrae nada"
        )
        return None

    targets = [t for t in (_build_target(dict(r), fernet) for r in rows) if t]
    if not targets:
        logger.info("registered_targets | sin filas activas utilizables | no se extrae nada")
        return None

    # Debug, no INFO: runner.py repite este SELECT cada 10 s y un resumen por
    # ciclo son 8 640 lineas al dia. El estado lo reporta el runner (una sola
    # vez, por transicion) y main.py, que corre una sola vez.
    logger.debug(f"registered_targets | {len(targets)} base(s) activa(s)")
    return targets
