"""runner: cadencia de 10 s, aislamiento de fallos y log por transicion.

Ninguna prueba duerme de verdad: el reloj, el stop y la carga de targets se
inyectan, asi que la aritmetica de "sin ticks atrasados" se verifica exacta.
"""
import logging
from unittest import mock

import pytest

import runner as runner_mod

pytestmark = pytest.mark.unit


class _Clock:
    """Reloj fijo: devuelve los valores en orden y se queda en el ultimo."""

    def __init__(self, *values):
        self.values = list(values)
        self.i = 0

    def __call__(self):
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


class _FakeStop:
    """Sustituye a threading.Event para ver cuanto espera cada ciclo."""

    def __init__(self, stop_after):
        self.stop_after = stop_after
        self.checks = 0
        self.waits = []

    def is_set(self):
        self.checks += 1
        return self.checks > self.stop_after

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return False

    def set(self):
        pass


# ---------- el intervalo viene de la env ----------


def test_intervalo_por_defecto_es_10_s(monkeypatch):
    monkeypatch.delenv("EXTRACT_INTERVAL_S", raising=False)

    assert runner_mod.interval_from_env() == 10.0


def test_intervalo_lectura_desde_env(monkeypatch):
    monkeypatch.setenv("EXTRACT_INTERVAL_S", "25")

    assert runner_mod.interval_from_env() == 25.0


@pytest.mark.parametrize("crudo", ["", "abc", "0", "-3", "1e9999"])
def test_intervalo_no_usable_vuelve_a_10_s(monkeypatch, crudo):
    monkeypatch.setenv("EXTRACT_INTERVAL_S", crudo)

    assert runner_mod.interval_from_env() == 10.0


# ---------- un ciclo: SELECT + extraccion ----------


def test_ciclo_sin_targets_no_extrae_y_loguea_una_sola_vez(caplog):
    """Sin filas activas no se llama a run; el aviso es por transicion, no un
    aviso por ciclo (8 640 al dia a 10 s serian solo ruido)."""
    run = mock.MagicMock()
    ciclo = runner_mod.Runner(interval=10, load=lambda: None, run=run)

    with caplog.at_level(logging.WARNING):
        assert ciclo.cycle() is None
        assert ciclo.cycle() is None

    assert run.call_count == 0
    avisos = [r for r in caplog.records if "sin bases registradas" in r.getMessage()]
    assert len(avisos) == 1, "el estado se loguea al cambiar, no en cada ciclo"


def test_ciclo_con_targets_los_corre_y_devuelve_cuanto(caplog):
    targets = [mock.sentinel.uno, mock.sentinel.dos]
    run = mock.MagicMock()
    ciclo = runner_mod.Runner(interval=10, load=lambda: targets, run=run)

    with caplog.at_level(logging.INFO):
        assert ciclo.cycle() == 2
        assert ciclo.cycle() == 2

    assert run.call_args_list == [mock.call(targets), mock.call(targets)]
    infos = [r for r in caplog.records if "2 base(s) activa(s)" in r.getMessage()]
    assert len(infos) == 1


def test_ciclo_registra_la_duracion_en_debug(caplog):
    run = mock.MagicMock()
    ciclo = runner_mod.Runner(
        interval=10, load=lambda: [1], run=run, clock=_Clock(100, 103)
    )

    with caplog.at_level(logging.DEBUG):
        ciclo.cycle()

    assert any("ciclo ok" in r.getMessage() and "3.00s" in r.getMessage()
               for r in caplog.records)


# ---------- run_forever: ancla al inicio, sin ticks atrasados ----------


def test_espera_lo_que_falta_por_ciclo_sin_acumular():
    """Ancla al INICIO del ciclo: ciclo de 5 s con intervalo 10 -> espera 5;
    el arranque del siguiente sigue siendo start+intervalo."""
    objetivo = []
    runner = runner_mod.Runner(
        interval=10, load=lambda: None, run=mock.MagicMock(), clock=_Clock(0, 5, 10, 11)
    )
    runner.stop = _FakeStop(stop_after=2)

    runner.run_forever()

    assert runner.stop.waits == [5, 9]


def test_ciclo_mas_largo_que_el_intervalo_no_acumula_ticks():
    """Si el ciclo dura 25 s con intervalo 10, no se disparan 2 ciclos
    atrasados: se espera 0 y el siguiente arranca apenas termine."""
    runner = runner_mod.Runner(
        interval=10, load=lambda: None, run=mock.MagicMock(), clock=_Clock(0, 25, 25, 50)
    )
    runner.stop = _FakeStop(stop_after=1)

    runner.run_forever()

    assert runner.stop.waits == [0]


def test_run_forever_pasa_los_targets_al_run():
    objetivos = [mock.sentinel.a, mock.sentinel.b]
    run = mock.MagicMock(side_effect=lambda targets: runner.request_stop())
    runner = runner_mod.Runner(interval=10, load=lambda: objetivos, run=run)

    runner.run_forever()

    assert run.call_args_list == [mock.call(objetivos)]


def test_un_ciclo_que_revienta_no_tumba_el_daemon_y_hace_backoff(caplog):
    """Backoff = intervalo doblando (tope 60 s); al volver a correr bien vuelve
    al intervalo normal. Aqui el primer ciclo falla y el segundo para."""
    llamadas = []

    def run(_targets):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise RuntimeError("ciclo caido a proposito")
        runner.request_stop()

    runner = runner_mod.Runner(interval=0.01, load=lambda: [1], run=run)

    with caplog.at_level(logging.ERROR):
        runner.run_forever()

    assert len(llamadas) == 2, "el fallo no mata el loop: reintenta y sigue"
    assert any("ciclo fallo" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records)
    assert runner.backoff == 0.01, "tras un ciclo bueno el backoff vuelve al intervalo"


def test_backoff_dobra_hasta_el_tope(caplog):
    """Fallo tras fallo: 10 -> 20 -> 40 -> 60 (tope). Recien un ciclo bueno
    vuelve al intervalo normal."""
    llamadas = []

    def run(_targets):
        llamadas.append(1)
        raise RuntimeError("siempre caido")

    runner = runner_mod.Runner(interval=10, load=lambda: [1], run=run)
    runner.stop = _FakeStop(stop_after=4)

    with caplog.at_level(logging.ERROR):
        runner.run_forever()

    assert runner.stop.waits == [10, 20, 40, 60]
    assert len(llamadas) == 4
    runner.backoff = min(runner.backoff * 2, runner_mod.MAX_BACKOFF_S)
    assert runner.backoff == 60, "el tope evita esperas larguisimas tras un corte largo"
