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