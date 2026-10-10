import json
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_json(name):
    with open(GOLDEN_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def mysql_snapshot():
    return _load_json("mysql_snapshot.json")


@pytest.fixture(scope="session")
def postgres_snapshot():
    return _load_json("postgres_snapshot.json")


@pytest.fixture(autouse=True)
def _unit_tests_never_reach_querylens_db(request, monkeypatch):
    """Una prueba unit que llama main.run_engine sin parchear la base de
    QueryLens abriria una conexion real: run_engine vuelca la salud de la
    corrida a pipeline_health y dejaria un last_run 'failed' falso en la base
    del compose (o esperaria el connect_timeout si no hay base). Las pruebas
    que necesitan su propio engine lo parchean despues y este queda pisado."""
    if request.node.get_closest_marker("unit") is None:
        return
    from unittest import mock

    import main

    monkeypatch.setattr(main, "get_connection_querylens_db", mock.MagicMock())