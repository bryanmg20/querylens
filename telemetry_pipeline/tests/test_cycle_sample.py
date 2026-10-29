"""cycle_sample: diferencias entre la muestra de un ciclo y la anterior."""
from datetime import datetime, timedelta, timezone

import pytest

from health.cycle_sample import deadlocks_since, derive, log_bytes_since
from stages.pg_log import LOG_WINDOW_BYTES

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def _sample(taken_at=T0, deadlocks=10, epoch="r|s", files=None):
    return {
        "taken_at": taken_at,
        "engine_deadlocks": deadlocks,
        "counter_epoch": epoch,
        "log_files": files,
    }


# ------------------------------------------------------------ contador

def test_deadlocks_since_is_the_counter_difference():
    assert deadlocks_since(10, "r|s", 13, "r|s") == (3, False)


def test_counter_that_goes_down_was_reset():
    # MySQL no tiene epoca: el reinicio se ve porque el contador baja
    assert deadlocks_since(10, None, 2, None) == (2, True)


def test_new_epoch_is_a_reset_even_if_the_counter_grew():
    assert deadlocks_since(10, "r1", 15, "r2") == (15, True)


def test_clean_postgres_restart_is_not_a_reset():
    # desde PG 15 las estadisticas sobreviven al apagado limpio: el contador
    # sigue donde estaba y no hay abrazos nuevos
    assert deadlocks_since(336, "", 336, "") == (0, False)


def test_sample_saved_with_the_postmaster_start_compares_only_stats_reset():
    # muestras de antes: 'stats_reset|arranque'; la nueva trae solo stats_reset
    assert deadlocks_since(336, "|2026-10-26 20:18:15+00", 336, "") == (0, False)
    assert deadlocks_since(336, "r1|2026-10-26 20:18:15+00", 340, "r2") == (340, True)


def test_crash_discards_the_stats_and_the_counter_goes_down():
    assert deadlocks_since(336, "", 0, "") == (0, True)


@pytest.mark.parametrize("prev,value", [(None, 3), (3, None), (None, None)])
def test_without_both_values_there_is_no_difference(prev, value):
    assert deadlocks_since(prev, "e", value, "e") == (None, False)


# ------------------------------------------------------------ log

def test_log_bytes_is_the_growth_of_each_file():
    prev = [{"name": "postgresql-Sat.json", "size": 1000}, {"name": "postgresql-Fri.json", "size": 500}]
    now = [{"name": "postgresql-Sat.json", "size": 1600}, {"name": "postgresql-Fri.json", "size": 500}]
    assert log_bytes_since(prev, now) == 600


def test_a_new_file_counts_whole():
    # rotacion: el viejo termino de crecer y el nuevo empezo de cero
    prev = [{"name": "postgresql-Sat.json", "size": 1000}]
    now = [{"name": "postgresql-Sun.json", "size": 300}, {"name": "postgresql-Sat.json", "size": 1200}]
    assert log_bytes_since(prev, now) == 500


def test_a_truncated_and_reused_file_counts_whole():
    # postgresql-%a con log_truncate_on_rotation: el de la semana pasada se pisa
    prev = [{"name": "postgresql-Sun.json", "size": 9000}]
    now = [{"name": "postgresql-Sun.json", "size": 400}]
    assert log_bytes_since(prev, now) == 400


def test_a_removed_file_does_not_count():
    prev = [{"name": "a.json", "size": 100}, {"name": "b.json", "size": 100}]
    now = [{"name": "a.json", "size": 150}]
    assert log_bytes_since(prev, now) == 50


@pytest.mark.parametrize("prev,now", [(None, []), ([], None)])
def test_without_both_listings_there_is_no_difference(prev, now):
    assert log_bytes_since(prev, now) is None


# ------------------------------------------------------------ derive

def test_first_sample_has_no_differences():
    derived = derive(None, _sample())
    assert derived == {
        "deadlocks_since_prev": None,
        "counter_reset": False,
        "log_bytes_since_prev": None,
        "seconds_since_prev": None,
        "log_window_overrun": None,
    }


def test_derive_against_the_previous_sample():
    prev = _sample(deadlocks=10, files=[{"name": "a.json", "size": 1000}])
    now = _sample(T0 + timedelta(seconds=10), deadlocks=12, files=[{"name": "a.json", "size": 5000}])
    assert derive(prev, now) == {
        "deadlocks_since_prev": 2,
        "counter_reset": False,
        "log_bytes_since_prev": 4000,
        "seconds_since_prev": 10.0,
        "log_window_overrun": False,
    }


def test_more_bytes_than_the_window_is_an_overrun():
    prev = _sample(files=[{"name": "a.json", "size": 0}])
    exact = _sample(T0 + timedelta(seconds=10), files=[{"name": "a.json", "size": LOG_WINDOW_BYTES}])
    over = _sample(T0 + timedelta(seconds=10), files=[{"name": "a.json", "size": LOG_WINDOW_BYTES + 1}])
    # la lectura de este ciclo cubre LOG_WINDOW_BYTES: justo eso todavia se lee
    assert derive(prev, exact)["log_window_overrun"] is False
    assert derive(prev, over)["log_window_overrun"] is True


def test_mysql_sample_has_no_log():
    prev = _sample(epoch=None)
    now = _sample(T0 + timedelta(seconds=10), deadlocks=11, epoch=None)
    derived = derive(prev, now)
    assert derived["deadlocks_since_prev"] == 1
    assert derived["log_bytes_since_prev"] is None
    assert derived["log_window_overrun"] is None
