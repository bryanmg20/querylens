from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
import os
from pathlib import Path
from dotenv import load_dotenv

def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def get_connection_postgres() -> Engine:
    """Motor de telemetry Postgres. Solo lee, por eso usa el rol monitor.

    Returns:
        sqlalchemy.engine.Engine: SQLAlchemy engine instance.
    """
    db_host = _env("MONITOR_PG_HOST", "localhost")
    db_port = _env("MONITOR_PG_PORT", "5432")
    db_name = _env("MONITOR_PG_DB", "ql_demo")
    db_user = _env("MONITOR_PG_USER", "querylens_monitor")
    db_password = _env("MONITOR_PG_PASSWORD", "monitor_pass")

    url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    return create_engine(url)


def get_connection_mysql() -> Engine:
    """Motor de telemetry MySQL.

    Sin base por defecto a proposito: el EXPLAIN depende del USE que emite
    ExplainStage a partir del schema_name resuelto.

    Returns:
        sqlalchemy.engine.Engine: SQLAlchemy engine instance.
    """
    db_host = _env("MONITOR_MY_HOST", "localhost")
    db_port = _env("MONITOR_MY_PORT", "3307")
    db_user = _env("MONITOR_MY_USER", "querylens_monitor")
    db_password = _env("MONITOR_MY_PASSWORD", "monitor_pass")

    url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/"

    return create_engine(
        url,
        pool_size=1,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False
    )


_env_path = Path(__file__).resolve().parents[2] / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def get_connection_querylens_db() -> Engine:
    """Create a SQLAlchemy engine for the PostgreSQL database using environment variables."""
    db_host = _env("DB_HOST", "localhost")
    db_port = _env("DB_PORT", "5432")
    db_name = _env("QUERYLENS_DB", "ql_demo")
    db_user = _env("QUERYLENS_USER", "ql_user")
    db_password = _env("QUERYLENS_PASSWORD", "ql_pass")

    url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    return create_engine(url)