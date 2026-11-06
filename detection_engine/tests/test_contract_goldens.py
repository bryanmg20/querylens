"""El detector entiende lo que produce el pipeline.

Los goldens se regeneran con telemetry_pipeline/ci/regenerate_goldens.py sobre
motores reales: si el pipeline cambia la forma del snapshot y el detector deja
de entenderla, este test se cae.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from querylens_contract import SnapshotPayload

from anti_patterns.engine import detect_all
from anti_patterns.statement_history import next_statement_histories

GOLDEN = Path(__file__).resolve().parents[2] / "telemetry_pipeline" / "tests" / "golden"
GOLDENS = ["postgres_snapshot.json", "mysql_snapshot.json"]

# todas las reglas, no solo ACTIVE_RULES: las que estan apagadas tambien tienen
# que leer el snapshot sin romperse
RULES = [
    "disk_spill",
    "avoidable_full_scan",
    "missing_index",
    "non_sargable_predicate",
    "unused_index",
    "baseline_degradation",
    "n_plus_one",
    "cardinality_misestimate",
]


def _load(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", GOLDENS)
def test_golden_validates_against_contract(name):
    snapshot = SnapshotPayload.model_validate(_load(name))
    assert snapshot.statements
    assert snapshot.server_start_timestamp


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("name", GOLDENS)
def test_rules_run_on_golden(name, rule):
    snapshot = SnapshotPayload.model_validate(_load(name))
    detect_all(snapshot, rule=rule, history={}, captured_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("name", GOLDENS)
def test_statement_history_runs_on_golden(name):
    snapshot = SnapshotPayload.model_validate(_load(name))
    next_statement_histories(snapshot, {}, datetime.now(timezone.utc))


def test_payload_missing_collect_key_is_rejected():
    payload = _load("postgres_snapshot.json")
    del payload["indexes"]
    with pytest.raises(ValidationError):
        SnapshotPayload.model_validate(payload)


def test_unused_index_reads_server_start_time():
    # antes leia stats_reset_timestamp[0]["stats_reset"], que el pipeline no
    # envia, y la ventana nunca se consideraba recien reiniciada
    from datetime import timedelta

    from anti_patterns.unused_index import _stats_window_is_fresh

    payload = _load("postgres_snapshot.json")
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    payload["server_start_timestamp"] = [{"server_start_time": started.strftime("%Y-%m-%d %H:%M:%S.%f")}]
    snapshot = SnapshotPayload.model_validate(payload)
    assert _stats_window_is_fresh(snapshot, timedelta(days=7))
    assert not _stats_window_is_fresh(snapshot, timedelta(minutes=30))
