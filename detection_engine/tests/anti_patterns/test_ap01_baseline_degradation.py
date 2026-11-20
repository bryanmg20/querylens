"""AP-01 degradacion respecto a la linea base (SegundoInforme, Tabla 2, con
ventanas de 10 s): la huella tiene >= 5 ejecuciones en la ventana, su latencia
media es >= 2,0 veces la mediana de sus 60 ventanas anteriores y al menos 10 ms
mayor en valor absoluto (calibracion del 2026-11-18, #60).

Cada prueba arma la ventana actual como "N ejecuciones de X ms" sobre una
fila anterior fija, y la linea base como la lista de medias de las ventanas
previas que guarda statement_samples.
"""
from datetime import datetime, timedelta, timezone

import pytest
from querylens_contract import SnapshotPayload

from anti_patterns.baseline_degradation import (
    DEFAULT_BASELINE_WINDOW,
    DEFAULT_MIN_CALLS,
    detect_baseline_degradation,
)
from models import StatementHistory

NOW = datetime(2026, 11, 16, 10, 0, 10, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(seconds=10)
EPOCH = "2026-11-16 09:00:00.000000"
EPOCH_AT = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
QUERY_ID = 77
QUERY = "SELECT pad FROM ql_scn WHERE k = $1"
# contadores acumulados de la fila anterior
PREVIOUS_CALLS = 1000
PREVIOUS_TOTAL_MS = 1000.0
HEALTHY_BASELINE = (10.0,) * DEFAULT_BASELINE_WINDOW


def _snapshot(window_calls, window_mean_ms, epoch=EPOCH):
    calls = PREVIOUS_CALLS + window_calls
    total_ms = PREVIOUS_TOTAL_MS + window_calls * window_mean_ms
    statement = {
        "query_id": QUERY_ID, "query_text": QUERY, "canonic_query": QUERY,
        "schema_name": "public", "database_name": "ql_demo",
        "execution_count": calls, "rows_returned": calls, "total_time_ms": total_ms,
        "mean_time_ms": total_ms / calls, "counters_epoch": epoch,
    }
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": [statement], "top_impact_queries": [statement],
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
    })


def _history(baseline_ms):
    means = list(baseline_ms)
    ends = [PREVIOUS - timedelta(seconds=10 * i) for i in range(len(means) - 1, -1, -1)]
    return {str(QUERY_ID): StatementHistory(
        query_id=str(QUERY_ID), captured_at=PREVIOUS, execution_count=PREVIOUS_CALLS,
        total_time_ms=PREVIOUS_TOTAL_MS, rows_returned=PREVIOUS_CALLS, counters_epoch=EPOCH_AT,
        window_means_ms=means, window_ends_at=ends,
    )}


def _detect(window_calls, window_mean_ms, baseline_ms=HEALTHY_BASELINE, epoch=EPOCH):
    return detect_baseline_degradation(_snapshot(window_calls, window_mean_ms, epoch), _history(baseline_ms), NOW)


# ------------------------------------------------------------------ positivo


def test_reports_a_query_twice_as_slow_as_its_baseline():
    finding, = _detect(window_calls=20, window_mean_ms=30.0)
    assert finding.antipatron == "baseline_degradation"
    assert finding.query_id == QUERY_ID
    assert finding.evidencia["baseline_median_ms"] == pytest.approx(10.0)
    assert finding.evidencia["window_mean_ms"] == pytest.approx(30.0)
    assert finding.evidencia["ratio"] == pytest.approx(3.0)
    assert finding.evidencia["increase_ms"] == pytest.approx(20.0)
    assert finding.evidencia["window_calls"] == 20
    # la linea de tiempo son las 60 ventanas anteriores mas la actual
    assert len(finding.evidencia["timeline"]) == DEFAULT_BASELINE_WINDOW + 1


# ------------------------------------------------------- umbrales de la regla


def test_ratio_of_exactly_two_is_reported_and_below_is_not():
    # media 20 ms contra 10 ms: ratio 2,0 y 10 ms mas, los dos justo en el limite
    assert len(_detect(window_calls=20, window_mean_ms=20.0)) == 1
    assert _detect(window_calls=20, window_mean_ms=19.9) == []


def test_requires_at_least_ten_ms_more_in_absolute_value():
    # 5 veces mas lenta, pero son 1,6 ms: el caso de las escrituras de sysbench
    # en MySQL que la calibracion vio en los controles
    assert _detect(window_calls=20, window_mean_ms=2.0, baseline_ms=(0.4,) * 60) == []
    # 3 veces mas lenta: justo 10 ms mas cuenta, un paso menos no
    assert len(_detect(window_calls=20, window_mean_ms=15.0, baseline_ms=(5.0,) * 60)) == 1
    assert _detect(window_calls=20, window_mean_ms=14.99, baseline_ms=(5.0,) * 60) == []


def test_requires_the_minimum_calls_in_the_window():
    assert DEFAULT_MIN_CALLS == 5
    assert _detect(window_calls=4, window_mean_ms=100.0) == []
    assert len(_detect(window_calls=5, window_mean_ms=100.0)) == 1


def test_requires_the_full_baseline_of_sixty_windows():
    assert DEFAULT_BASELINE_WINDOW == 60
    assert _detect(window_calls=20, window_mean_ms=100.0, baseline_ms=(10.0,) * 59) == []
    assert len(_detect(window_calls=20, window_mean_ms=100.0, baseline_ms=(10.0,) * 60)) == 1


def test_baseline_is_the_median_so_one_spike_does_not_move_it():
    finding, = _detect(window_calls=20, window_mean_ms=30.0, baseline_ms=(10.0,) * 59 + (500.0,))
    assert finding.evidencia["baseline_median_ms"] == pytest.approx(10.0)


def test_baseline_uses_only_the_last_sixty_windows():
    # 70 ventanas viejas de 10 ms y 60 recientes de 40 ms: con todas, la mediana
    # seria 10 ms y 50 ms seria una degradacion; con las 60 ultimas es 40 ms
    baseline = (10.0,) * 70 + (40.0,) * 60
    assert _detect(window_calls=20, window_mean_ms=50.0, baseline_ms=baseline) == []


# ------------------------------------------------------------------ controles


def test_stable_query_is_not_reported():
    assert _detect(window_calls=20, window_mean_ms=11.0) == []


def test_query_that_did_not_run_in_the_window_is_not_evaluated():
    assert _detect(window_calls=0, window_mean_ms=0.0) == []


def test_counters_reset_is_not_compared_with_the_previous_period():
    # counters_epoch distinto: reinicio, reset de estadisticas o entrada desalojada
    assert _detect(window_calls=20, window_mean_ms=100.0, epoch="2026-11-16 09:30:00.000000") == []


def test_without_history_there_is_no_baseline():
    assert detect_baseline_degradation(_snapshot(20, 10.0), {}, NOW) == []
    assert detect_baseline_degradation(_snapshot(20, 10.0), None, NOW) == []


# ------------------------------------------------------------------ severidad


@pytest.mark.parametrize(("window_calls", "window_mean_ms", "severity"), [
    (100, 110.0, "high"),   # 100 ejecuciones x 100 ms de mas = 10.000 ms
    (100, 20.0, "medium"),  # 100 x 10 ms = 1.000 ms
    (20, 30.0, "low"),      # 20 x 20 ms = 400 ms
])
def test_severity_follows_the_extra_time_in_the_window(window_calls, window_mean_ms, severity):
    finding, = _detect(window_calls=window_calls, window_mean_ms=window_mean_ms)
    assert finding.severidad == severity
