"""Veredicto de sobrecosto (PrimerInforme: < 5%) por latencia p95 de la carga.

Sin datos => SIN_DATO. No se asume CUMPLE.
"""

import pytest

from measure_overhead import (
    UMBRAL_SOBRECOSTO_PCT,
    cpu_overhead_pct,
    latency_stats,
    mean_or_none,
    overall_verdict,
    percentile,
    qps_overhead_pct,
    relative_overhead_pct,
    verdict,
)

pytestmark = pytest.mark.unit


def test_umbral_es_el_del_primer_informe():
    assert UMBRAL_SOBRECOSTO_PCT == 5.0


@pytest.mark.parametrize(
    "vals,pct,esperado",
    [
        ([10.0], 50, 10.0),
        ([1.0, 2.0, 3.0, 4.0], 50, 2.5),
        ([1.0, 2.0, 3.0, 4.0], 100, 4.0),
        ([1.0, 2.0, 3.0, 4.0], 0, 1.0),
        ([5.0, 10.0, 15.0, 20.0, 25.0], 95, 24.0),
        ([], 50, None),
    ],
)
def test_percentile(vals, pct, esperado):
    result = percentile(vals, pct)
    if esperado is None:
        assert result is None
    else:
        assert result == pytest.approx(esperado)


def test_latency_stats_calcula_p50_p95_qps():
    rows = [
        {"duration_ms": 10.0, "status": 0},
        {"duration_ms": 20.0, "status": 0},
        {"duration_ms": 30.0, "status": 0},
        {"duration_ms": 40.0, "status": 0},
        {"duration_ms": 999.0, "status": 1},
    ]
    stats = latency_stats(rows, window_s=2.0)
    assert stats["n"] == 4
    assert stats["failures"] == 1
    assert stats["p50_ms"] == pytest.approx(25.0)
    assert stats["p95_ms"] == pytest.approx(38.5)
    assert stats["qps"] == pytest.approx(2.0)


def test_latency_stats_sin_ok_da_nulos():
    stats = latency_stats([{"duration_ms": 5.0, "status": 1}], window_s=10.0)
    assert stats["n"] == 0
    assert stats["p95_ms"] is None
    assert stats["qps"] is None


@pytest.mark.parametrize(
    "base,con,esperado",
    [
        (100.0, 105.0, 5.0),
        (100.0, 100.0, 0.0),
        (100.0, 95.0, -5.0),
        (None, 10.0, None),
        (10.0, None, None),
        (0.0, 10.0, None),
    ],
)
def test_relative_overhead_pct_latencia(base, con, esperado):
    result = relative_overhead_pct(base, con)
    if esperado is None:
        assert result is None
    else:
        assert result == pytest.approx(esperado)


@pytest.mark.parametrize(
    "base,con,esperado",
    [
        (50.0, 47.5, 5.0),
        (50.0, 50.0, 0.0),
        (50.0, 55.0, -10.0),
        (None, 10.0, None),
        (0.0, 10.0, None),
    ],
)
def test_qps_overhead_pct(base, con, esperado):
    result = qps_overhead_pct(base, con)
    if esperado is None:
        assert result is None
    else:
        assert result == pytest.approx(esperado)


@pytest.mark.parametrize(
    "over,esperado",
    [
        (0.0, "CUMPLE"),
        (4.9, "CUMPLE"),
        (4.999, "CUMPLE"),
        (5.0, "NO CUMPLE"),
        (5.1, "NO CUMPLE"),
        (None, "SIN_DATO"),
    ],
)
def test_verdict_es_inferior_al_5_puntos(over, esperado):
    assert verdict(over) == esperado


def test_verdict_sin_datos_es_sin_datno_no_cumple():
    assert verdict(None) == "SIN_DATO"


def test_verdict_basado_en_latencia_no_en_cpu():
    assert verdict(relative_overhead_pct(120.0, 126.0)) == "NO CUMPLE"
    assert verdict(relative_overhead_pct(120.0, 124.0)) == "CUMPLE"
    assert verdict(cpu_overhead_pct(80.0, 81.0)) == "CUMPLE"
    assert verdict(cpu_overhead_pct(80.0, 86.0)) == "NO CUMPLE"


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


def test_mean_or_none_promedia_ignorando_nulos():
    assert mean_or_none([1.0, 3.0]) == pytest.approx(2.0)
    assert mean_or_none([None, 2.0, 4.0]) == pytest.approx(3.0)
    assert mean_or_none([None, None]) is None
    assert mean_or_none([]) is None


def test_cpu_overhead_pct_diferencia_en_puntos():
    assert cpu_overhead_pct(60.6, 68.3) == pytest.approx(7.7)
    assert cpu_overhead_pct(None, 10.0) is None
    assert cpu_overhead_pct(10.0, None) is None
