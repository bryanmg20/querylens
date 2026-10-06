"""Veredicto de sobrecosto (PrimerInforme: < 5%)."""

import pytest

from measure_overhead import (
    UMBRAL_SOBRECOSTO_PCT,
    impact_pct,
    overall_verdict,
    verdict,
)

pytestmark = pytest.mark.unit


def test_umbral_es_el_del_primer_informe():
    assert UMBRAL_SOBRECOSTO_PCT == 5.0


@pytest.mark.parametrize(
    "sola,con,esperado",
    [
        (20.0, 20.6, 3.0),
        (20.0, 21.0, 5.0),
        (20.0, 22.0, 10.0),
        (10.0, 10.0, 0.0),
        (10.0, 9.0, -10.0),
        (0.0, 1.0, None),
        (None, 1.0, None),
        (10.0, None, None),
    ],
)
def test_impact_pct(sola, con, esperado):
    result = impact_pct(sola, con)
    if esperado is None:
        assert result is None
    else:
        assert result == pytest.approx(esperado)


@pytest.mark.parametrize(
    "impacto,esperado",
    [
        (0.0, "CUMPLE"),
        (4.9, "CUMPLE"),
        (4.999, "CUMPLE"),
        (5.0, "NO CUMPLE"),
        (5.1, "NO CUMPLE"),
        (None, "SIN_DATO"),
    ],
)
def test_verdict_es_inferior_al_5(impacto, esperado):
    assert verdict(impacto) == esperado


def test_overall_verdict_exige_ambos_motores():
    assert overall_verdict({
        "postgres": {"verdict": "CUMPLE"},
        "mysql": {"verdict": "CUMPLE"},
    }) == "CUMPLE"
    assert overall_verdict({
        "postgres": {"verdict": "CUMPLE"},
        "mysql": {"verdict": "NO CUMPLE"},
    }) == "NO CUMPLE"
    assert overall_verdict({
        "postgres": {"verdict": "SIN_DATO"},
        "mysql": {"verdict": "CUMPLE"},
    }) == "SIN_DATO"
    assert overall_verdict({}) == "SIN_DATO"
