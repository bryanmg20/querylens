from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
import os
from pathlib import Path
from dotenv import load_dotenv

# Timeouts del pipeline real (ver PIPELINE_FLOW). VAN en el arranque de la
# sesion (options/init_command), no via event listener: CollectStage y
# ExplainStage hacen rollback() tras un error y un SET transaccional de
# Postgres se desharía.
CONNECT_TIMEOUT_S = 10      # libpq es ilimitado por defecto; cubre redes normales
STATEMENT_TIMEOUT_S = 30    # la extraccion mide ~0,3-0,6 s por target -> margen x50
READ_TIMEOUT_S = 40         # solo MySQL (socket): > statement_timeout, para que salte primero el del server


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _make_url(
    drivername: str,
    host: str,
    port: int | None,
    username: str,
    password: str,
    database: str | None = None,
) -> URL:
    """URL.create en vez de f-string: la password puede traer '@', ':', '?' o '/'
    y un template roto haria que el driver parseara mal los componentes (el
    password llega truncado). Cada componente viaja por separado y el driver
    recibe el valor integro. Mismo criterio que config/registered.py.
    """
    return URL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def postgres_connect_args() -> dict:
    """connect_args para psycopg2/libpq. options aplica statement_timeout al
    abrir la sesion (a salvo del rollback); connect_timeout tapa el 'infinito'
    por defecto de libpq."""
    return {
        "connect_timeout": CONNECT_TIMEOUT_S,
        "options": f"-c statement_timeout={STATEMENT_TIMEOUT_S * 1000}",
    }


def mysql_connect_args() -> dict:
    """connect_args para pymysql. El init_command fija la sesion a UTC
    (time_zone='+00:00': FIRST_SEEN/trx_started quedan comparables con el
    timestamptz de Postgres) y max_execution_time como tope de SELECT. Ese
    tope no cubre EXPLAIN/USE: read_timeout es el cinturon."""
    return {
        "connect_timeout": CONNECT_TIMEOUT_S,
        "read_timeout": READ_TIMEOUT_S,
        "init_command": (
            f"SET SESSION max_execution_time={STATEMENT_TIMEOUT_S * 1000}, "
            f"time_zone='+00:00'"
        ),
    }


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

    url = _make_url(
        drivername="postgresql+psycopg2",
        host=db_host,
        port=int(db_port) if db_port else None,
        username=db_user,
        password=db_password,
        database=db_name,
    )
    return create_engine(url, connect_args=postgres_connect_args())


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

    url = _make_url(
        drivername="mysql+pymysql",
        host=db_host,
        port=int(db_port) if db_port else None,
        username=db_user,
        password=db_password,
        database="",
    )

    return create_engine(
        url,
        pool_size=1,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
        connect_args=mysql_connect_args(),
    )


_env_path = Path(__file__).resolve().parents[2] / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def get_connection_mysql_app() -> Engine:
    """Motor de aplicacion MySQL: genera el trafico que el monitor observa.

    Mismo servidor que el motor de monitoreo (MONITOR_MY_HOST/PORT), pero con
    credenciales de rol de aplicacion (APP_MY_*). El rol app es distinto del
    monitor a proposito: ACTIVE_QUERIES_QUERY excluye el CURRENT_USER, asi que la
    carga tiene que venir de otro rol para que el collector la vea. En el sandbox
    es app_user (lo crea MYSQL_USER del compose); en CI lo crea el workflow
    ('rol de aplicacion MySQL' -> ql_mysql_app).

    Returns:
        sqlalchemy.engine.Engine: SQLAlchemy engine instance.
    """
    db_host = _env("MONITOR_MY_HOST", "localhost")
    db_port = _env("MONITOR_MY_PORT", "3307")
    db_user = _env("APP_MY_USER", "app_user")
    db_password = _env("APP_MY_PASSWORD", "app_pass")
    db_name = _env("APP_MY_DB", "ql_demo")

    url = _make_url(
        drivername="mysql+pymysql",
        host=db_host,
        port=int(db_port) if db_port else None,
        username=db_user,
        password=db_password,
        database=db_name,
    )

    return create_engine(
        url,
        pool_size=1,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
        connect_args=mysql_connect_args(),
    )


def get_connection_querylens_db() -> Engine:
    """Create a SQLAlchemy engine for the PostgreSQL database using environment variables."""
    db_host = _env("DB_HOST", "localhost")
    db_port = _env("DB_PORT", "5432")
    db_name = _env("QUERYLENS_DB", "ql_demo")
    db_user = _env("QUERYLENS_USER", "ql_user")
    db_password = _env("QUERYLENS_PASSWORD", "ql_pass")

    url = _make_url(
        drivername="postgresql+psycopg2",
        host=db_host,
        port=int(db_port) if db_port else None,
        username=db_user,
        password=db_password,
        database=db_name,
    )
    return create_engine(url, connect_args=postgres_connect_args())