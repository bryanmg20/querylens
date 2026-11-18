"""Umbrales congelados (SegundoInforme 7.3 y Tabla 17, issue #60).

Los umbrales de deteccion se calibran con corridas distintas de las de
validacion y quedan congelados antes de la campana final; cualquier cambio
posterior obliga a repetir la campana completa. Esta prueba compara cada
umbral del codigo con detection_engine/umbrales_congelados.json: si alguien lo
cambia sin actualizar ese archivo (y sin repetir la campana), se cae.
"""
import importlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

FROZEN_PATH = Path(__file__).resolve().parents[1] / "umbrales_congelados.json"
FROZEN = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
THRESHOLDS = [
    (pathology, name, value)
    for pathology, values in FROZEN["umbrales"].items()
    for name, value in values.items()
]


def _current(name: str):
    module_name, attribute = name.rsplit(".", 1)
    value = getattr(importlib.import_module(module_name), attribute)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, tuple):
        return list(value)
    return value


@pytest.mark.parametrize(("pathology", "name", "frozen"), THRESHOLDS, ids=[name for _, name, _ in THRESHOLDS])
def test_threshold_matches_the_frozen_value(pathology, name, frozen):
    assert _current(name) == frozen, (
        f"{pathology}: {name} cambio respecto del valor congelado. Si el cambio es a proposito, "
        f"actualizar {FROZEN_PATH.name} (valor, version y motivo) y repetir la campana completa."
    )


def test_every_catalogue_rule_is_active():
    # la campana mide las 8 reglas de antipatrones: ninguna puede quedar fuera
    from anti_patterns.engine import ACTIVE_RULES
    from catalog import CODES
    antipatterns = {name for name, code in CODES.items() if code.startswith("AP")}
    assert set(ACTIVE_RULES) == antipatterns
