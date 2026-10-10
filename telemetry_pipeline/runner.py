"""Daemon de extraccion: cicla sobre registered_databases.

Cada EXTRACT_INTERVAL_S (10 s por defecto) vuelve a hacer el SELECT de
`config/registered.py` y extrae todas las filas activas, aislado por target.
No hay gate de encolado: se encola siempre; la retencion de los mensajes ya
leidos de PGMQ la hace el consumidor.

    python runner.py
    EXTRACT_INTERVAL_S=30 python runner.py

Comportamiento del ciclo:
  * sin solapes: un solo hilo; si un ciclo dura mas que el intervalo, el
    siguiente arranca en cuanto termine (no se acumulan ticks atrasados);
  * si el ciclo lanza, espera el backoff (intervalo doblando, tope 60 s) y
    sigue; al volver a terminar bien vuelve al intervalo normal;
  * el log por ciclo es DEBUG y el de "sin bases" solo se emite en cambios de
    estado, para no llenar el archivo con 8 640 avisos al dia;
  * cada HEALTH_PURGE_INTERVAL_S (1 h por defecto) purga pipeline_health:
    issues resueltos y last_run de mas de HEALTH_RETENTION_DAYS (15) dias.
"""
import logging
import math
import os
import signal
import threading
import time

from config.registered import _get_queue_engine, load_registered_targets
from health import writer as health_writer
from logger import get_logger
from main import run_targets

logger = get_logger(__name__)
# El detalle de cada ciclo (duracion y targets) baja a DEBUG para que el log
# del archivo cuente cuanto duro cada vuelta sin ensuciar la consola: el resto
# del pipeline se queda en INFO.
logger.setLevel(logging.DEBUG)

DEFAULT_INTERVAL_S = 10.0
MAX_BACKOFF_S = 60.0
DEFAULT_PURGE_INTERVAL_S = 3600.0


def interval_from_env() -> float:
    """EXTRACT_INTERVAL_S en segundos; cualquier valor no usable cae a 10 s."""
    raw = os.getenv("EXTRACT_INTERVAL_S", "")
    if raw == "":
        return DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            f"runner | EXTRACT_INTERVAL_S invalida: {raw!r} | uso {DEFAULT_INTERVAL_S:g}s"
        )
        return DEFAULT_INTERVAL_S
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            f"runner | EXTRACT_INTERVAL_S no usable: {raw!r} | uso {DEFAULT_INTERVAL_S:g}s"
        )
        return DEFAULT_INTERVAL_S
    return value


def purge_interval_from_env() -> float:
    """HEALTH_PURGE_INTERVAL_S en segundos; cualquier valor no usable cae a 1 h."""
    raw = os.getenv("HEALTH_PURGE_INTERVAL_S", "")
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PURGE_INTERVAL_S
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_PURGE_INTERVAL_S
    return value


def _purge_health() -> None:
    health_writer.purge(_get_queue_engine())


class Runner:
    """Un ciclo por intervalo, sin solapes ni ticks atrasados."""

    def __init__(
        self,
        interval=None,
        load=None,
        run=None,
        clock=time.monotonic,
        purge=None,
        purge_interval=None,
    ):
        self.interval = interval if interval is not None else interval_from_env()
        self.load = load if load is not None else load_registered_targets
        self.run = run if run is not None else run_targets
        self.clock = clock
        # None = sin purga (tests, usos embebidos); la entrada del daemon pasa
        # _purge_health.
        self.purge = purge
        self.purge_interval = (
            purge_interval if purge_interval is not None else purge_interval_from_env()
        )
        # None = todavia no purgo: la primera purga corre en el primer ciclo.
        self._last_purge = None
        self.stop = threading.Event()
        self.backoff = self.interval
        # Objetivo activo del ciclo anterior; None = todavia no corrio ninguno.
        self._active = None

    def cycle(self) -> int | None:
        """SELECT de las filas activas + extraccion. Devuelve los targets o None."""
        self._maybe_purge()
        targets = self.load()
        self._report_state(targets)
        if not targets:
            return None

        started = self.clock()
        self.run(targets)
        logger.debug(
            f"runner | ciclo ok | {len(targets)} target(s) | {self.clock() - started:.2f}s"
        )
        return len(targets)

    def _maybe_purge(self) -> None:
        """Purga de pipeline_health a su propio ritmo; si falla, el ciclo sigue."""
        if self.purge is None:
            return
        now = self.clock()
        if self._last_purge is not None and now - self._last_purge < self.purge_interval:
            return
        self._last_purge = now
        try:
            self.purge()
        except Exception as e:
            logger.error(f"runner | purge fallo ({type(e).__name__}: {e})")

    def _report_state(self, targets) -> None:
        active = len(targets) if targets else 0
        if active == self._active:
            return
        if active == 0:
            logger.warning("runner | sin bases registradas activas | sigo esperando")
        else:
            logger.info(
                f"runner | {active} base(s) activa(s) | extraccion cada {self.interval:g}s"
            )
        self._active = active

    def run_forever(self) -> None:
        logger.info(f"runner | arrancado | intervalo {self.interval:g}s")
        while not self.stop.is_set():
            started = self.clock()
            try:
                self.cycle()
                self.backoff = self.interval
            except Exception as e:
                wait_s = min(self.backoff, MAX_BACKOFF_S)
                self.backoff = min(self.backoff * 2, MAX_BACKOFF_S)
                logger.error(
                    f"runner | ciclo fallo ({type(e).__name__}: {e}) "
                    f"| reintento en {wait_s:g}s"
                )
                self.stop.wait(wait_s)
                continue

            # Se ancla al inicio del ciclo: si duro menos que el intervalo se
            # espera lo que falte, si duro mas no se acumulan ticks atrasados.
            self.stop.wait(max(0.0, started + self.interval - self.clock()))
        logger.info("runner | detenido")

    def request_stop(self, *_args) -> None:
        self.stop.set()


def _install_signal_handlers(runner: Runner) -> None:
    """SIGINT/SIGTERM terminan tras el ciclo en curso, sin cortarlo a medias."""
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        # Ctrl+Break en Windows llega como SIGBREAK y con el handler por
        # defecto saldria con traceback en vez de una parada limpia.
        signals.append(signal.SIGBREAK)
    for sig in signals:
        try:
            signal.signal(sig, runner.request_stop)
        except (ValueError, OSError):
            # Senal no soportada en esta plataforma o fuera del hilo principal.
            pass


if __name__ == "__main__":
    runner = Runner(purge=_purge_health)
    _install_signal_handlers(runner)
    runner.run_forever()
