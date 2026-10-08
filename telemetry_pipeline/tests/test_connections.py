from pathlib import Path

import pytest

import config.connections as connections
from config.connections import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    STATEMENT_TIMEOUT_S,
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
    mysql_connect_args,
    postgres_connect_args,
)

pytestmark = pytest.mark.unit

_QUEUE_VARS = ("DB_HOST", "DB_PORT", "QUERYLENS_USER", "QUERYLENS_PASSWORD", "QUERYLENS_DB")

_MONITOR_PG_VARS = (
    "MONITOR_PG_HOST", "MONITOR_PG_PORT", "MONITOR_PG_DB",
    "MONITOR_PG_USER", "MONITOR_PG_PASSWORD",
)
_MONITOR_MY_VARS = (
    "MONITOR_MY_HOST", "MONITOR_MY_PORT",
    "MONITOR_MY_USER", "MONITOR_MY_PASSWORD",
)

_QUEUE_DEFAULTS = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "QUERYLENS_USER": "ql_user",
    "QUERYLENS_PASSWORD": "ql_pass",
    "QUERYLENS_DB": "ql_demo",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ninguna variable de conexion puede filtrarse desde el entorno del host."""
    for var in _QUEUE_VARS + _MONITOR_PG_VARS + _MONITOR_MY_VARS:
        monkeypatch.delenv(var, raising=False)


def _url(engine):
    return engine.url.render_as_string(hide_password=False)


def _url_with(monkeypatch, factory, *env_vars, **overrides):
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    return _url(factory())


def test_postgres_engine_points_at_the_sandbox_monitor_user():
    url = _url(get_connection_postgres())
    assert url.startswith("postgresql+psycopg2://querylens_monitor:")
    assert url.endswith("@localhost:5432/ql_demo")


def test_mysql_engine_uses_the_non_default_port():
    url = _url(get_connection_mysql())
    assert url.startswith("mysql+pymysql://querylens_monitor:")
    assert url.endswith("@localhost:3307/")


def test_postgres_monitor_is_parametrizable(monkeypatch):
    """Sin esto el job de integracion de CI se saltaria entero: los motores reales
    viven en otros host/puerto y las factorias tenian los valores fijos."""
    url = _url_with(
        monkeypatch,
        get_connection_postgres,
        *_MONITOR_PG_VARS,
        MONITOR_PG_HOST="pg.internal",
        MONITOR_PG_PORT="6543",
        MONITOR_PG_DB="telemetry",
        MONITOR_PG_USER="ro",
        MONITOR_PG_PASSWORD="s3cret",
    )
    assert url == "postgresql+psycopg2://ro:s3cret@pg.internal:6543/telemetry"


def test_mysql_monitor_is_parametrizable(monkeypatch):
    url = _url_with(
        monkeypatch,
        get_connection_mysql,
        *_MONITOR_MY_VARS,
        MONITOR_MY_HOST="my.internal",
        MONITOR_MY_PORT="3306",
        MONITOR_MY_USER="ro",
        MONITOR_MY_PASSWORD="s3cret",
    )
    assert url == "mysql+pymysql://ro:s3cret@my.internal:3306/"


@pytest.mark.parametrize(
    "var,sentinel",
    [
        ("MONITOR_PG_HOST", "pg.probe"),
        ("MONITOR_PG_PORT", "6543"),
        ("MONITOR_PG_DB", "telemetry"),
        ("MONITOR_PG_USER", "ro"),
        ("MONITOR_PG_PASSWORD", "s3cret"),
    ],
)
def test_each_postgres_monitor_variable_is_read(monkeypatch, var, sentinel):
    assert sentinel in _url_with(monkeypatch, get_connection_postgres, *_MONITOR_PG_VARS, **{var: sentinel})


@pytest.mark.parametrize(
    "var,sentinel",
    [
        ("MONITOR_MY_HOST", "my.probe"),
        ("MONITOR_MY_PORT", "3306"),
        ("MONITOR_MY_USER", "ro"),
        ("MONITOR_MY_PASSWORD", "s3cret"),
    ],
)
def test_each_mysql_monitor_variable_is_read(monkeypatch, var, sentinel):
    assert sentinel in _url_with(monkeypatch, get_connection_mysql, *_MONITOR_MY_VARS, **{var: sentinel})


@pytest.mark.parametrize("var", _MONITOR_PG_VARS)
def test_postgres_falls_back_to_sandbox_defaults(monkeypatch, var):
    assert _url_with(
        monkeypatch, get_connection_postgres, *_MONITOR_PG_VARS
    ) == "postgresql+psycopg2://querylens_monitor:monitor_pass@localhost:5432/ql_demo"


@pytest.mark.parametrize("var", _MONITOR_MY_VARS)
def test_mysql_falls_back_to_sandbox_defaults(monkeypatch, var):
    assert _url_with(
        monkeypatch, get_connection_mysql, *_MONITOR_MY_VARS
    ) == "mysql+pymysql://querylens_monitor:monitor_pass@localhost:3307/"


def test_mysql_engine_has_no_default_database():
    url = _url(get_connection_mysql())
    assert "ql_demo" not in url, (
        "MySQL entra sin base por defecto: el EXPLAIN depende del USE que emite ExplainStage"
    )


def test_password_with_special_characters_survives_round_trip(monkeypatch):
    """La construccion con f-string trunca passwords con '@', ':', '?' o '/'
    (el driver parsea mal); URL.create lleva cada componente por separado y el
    valor debe llegar integro al motor. Se compara el atributo url.password,
    no el render: asi el test tambien fallaria si el motor guardara la version
    codificada en vez de la original."""
    for var in _MONITOR_MY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MONITOR_MY_USER", "u")
    monkeypatch.setenv("MONITOR_MY_PASSWORD", "p@ss:word/x?q")
    url = get_connection_mysql().url
    assert url.username == "u"
    assert url.password == "p@ss:word/x?q"


def test_mysql_engine_pool_is_limited_to_one_connection():
    engine = get_connection_mysql()
    assert engine.pool.size() == 1
    assert engine.pool._max_overflow == 10
    assert engine.pool._pre_ping is True


def test_postgres_connect_args_apply_connect_and_statement_timeouts():
    args = postgres_connect_args()
    assert args["connect_timeout"] == CONNECT_TIMEOUT_S
    assert args["options"] == f"-c statement_timeout={STATEMENT_TIMEOUT_S * 1000}"


def test_mysql_connect_args_pin_session_to_utc_and_bound_selects():
    args = mysql_connect_args()
    assert args["connect_timeout"] == CONNECT_TIMEOUT_S
    assert args["read_timeout"] == READ_TIMEOUT_S
    assert args["init_command"] == (
        f"SET SESSION max_execution_time={STATEMENT_TIMEOUT_S * 1000}, time_zone='+00:00'"
    )


def test_engines_apply_the_shared_connect_args(monkeypatch):
    """La unica fuente de los timeouts es config.connections; las factorias solo
    la cablean. Se comprueba capturando la llamada, sin atributos privados."""
    seen = []
    monkeypatch.setattr(
        connections, "postgres_connect_args",
        lambda: seen.append("pg") or postgres_connect_args(),
    )
    monkeypatch.setattr(
        connections, "mysql_connect_args",
        lambda: seen.append("my") or mysql_connect_args(),
    )
    get_connection_postgres()
    get_connection_mysql()
    get_connection_querylens_db()
    assert seen == ["pg", "my", "pg"]


def test_registered_engine_uses_the_same_timeout_wiring(monkeypatch):
    import config.registered as registered
    from sqlalchemy.engine import URL

    seen = []
    monkeypatch.setattr(
        registered, "postgres_connect_args",
        lambda: seen.append("pg") or postgres_connect_args(),
    )
    monkeypatch.setattr(
        registered, "mysql_connect_args",
        lambda: seen.append("my") or mysql_connect_args(),
    )
    url = URL.create(
        drivername="postgresql+psycopg2", username="u", password="p",
        host="h", port=5432,
    )
    registered._engine("postgres", url)
    registered._engine("mysql", url)
    assert seen == ["pg", "my"]


def test_querylens_db_reads_credentials_from_environment(monkeypatch):
    url = _url_with(
        monkeypatch,
        get_connection_querylens_db,
        *_QUEUE_VARS,
        DB_HOST="db.internal",
        DB_PORT="7000",
        QUERYLENS_USER="svc",
        QUERYLENS_PASSWORD="s3cret",
        QUERYLENS_DB="queue_db",
    )
    assert url == "postgresql+psycopg2://svc:s3cret@db.internal:7000/queue_db"


def test_querylens_db_uses_local_defaults_when_nothing_is_set(monkeypatch):
    url = _url_with(monkeypatch, get_connection_querylens_db, *_QUEUE_VARS)
    assert url == "postgresql+psycopg2://ql_user:ql_pass@localhost:5432/ql_demo"


@pytest.mark.parametrize("var", _QUEUE_VARS)
def test_each_queue_variable_is_read_from_the_environment(monkeypatch, var):
    sentinel = "9999" if var == "DB_PORT" else f"probe-{var.lower()}"
    url = _url_with(monkeypatch, get_connection_querylens_db, *_QUEUE_VARS, **{var: sentinel})
    assert sentinel in url


_SENTINELS = {
    "DB_HOST": "otro-host",
    "DB_PORT": "7000",
    "QUERYLENS_USER": "otro-user",
    "QUERYLENS_PASSWORD": "otro-pass",
    "QUERYLENS_DB": "otro-db",
}


@pytest.mark.parametrize("var", _QUEUE_VARS)
def test_each_missing_queue_variable_falls_back_independently(monkeypatch, var):
    """Deja solo `var` sin definir y pone el resto en valores ajenos: si el codigo
    leyera una variable distinta a la que cree, la URL no tendria el default."""
    others = {
        name: sentinel for name, sentinel in _SENTINELS.items() if name != var
    }
    url = _url_with(monkeypatch, get_connection_querylens_db, var, **others)
    assert _QUEUE_DEFAULTS[var] in url, (
        f"sin {var} deberia usarse el default {_QUEUE_DEFAULTS[var]}, "
        f"pero la URL fue {url}"
    )


def test_dotenv_is_loaded_from_the_repository_root():
    config_path = Path(connections.__file__).resolve()
    candidates = [
        config_path.parents[2] / ".env",
        config_path.parents[1] / ".env",
    ]
    assert connections._env_path in candidates
    if not candidates[0].exists():
        pytest.skip("sin .env en el repo: solo aplica al sandbox de desarrollo")


def test_env_file_is_not_committed():
    gitignore = (Path(connections.__file__).resolve().parents[2] / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert ".env" in gitignore.splitlines()
