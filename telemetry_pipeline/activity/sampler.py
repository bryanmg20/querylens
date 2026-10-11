"""Hilo que muestrea pg_stat_activity y alimenta el ActivitySampleBuffer.

El runner arma el snapshot cada EXTRACT_INTERVAL_S (10 s); una query de pocos
ms casi nunca esta en pg_stat_activity justo en ese instante (ver
references/005: 0 % con una sola foto por ciclo). Este hilo mira cada
ACTIVITY_SAMPLE_INTERVAL_S (1 s) y deja lo que ve en el buffer, que el ciclo
consulta al hacer EXPLAIN.

Comportamiento:
  * un engine persistente por target (el mismo factory del ciclo); cada tick
    abre una transaccion corta con statement_timeout de 2 s, para que un
    servidor lento no frene el muestreo de los demas targets;
  * un target que falla no frena a los demas: el error queda en status() y el
    ciclo lo reporta como ACTIVITY_SAMPLER_FAILED; el tick siguiente reintenta;
  * los ticks se anclan al inicio (mismo patron que Runner.run_forever): si un
    tick dura mas que el intervalo no se acumulan atrasados;
  * solo Postgres: MySQL ya trae QUERY_SAMPLE_TEXT.

Solo se guardan textos que el EXPLAIN puede usar tal cual: con literales (sin
$n del protocolo extendido), completos, de solo lectura y de una sentencia.
"""
import re
import threading
import time

from sqlalchemy import text

from activity.buffer import _positive_from_env
from collectors.postgres.queries import ACTIVITY_SAMPLES_QUERY
from logger import get_logger
from stages.sql_text import is_explainable_command, is_single_statement, mask_sql

logger = get_logger(__name__)

DEFAULT_INTERVAL_S = 1.0
TICK_STATEMENT_TIMEOUT = "2s"
INSUFFICIENT_PRIVILEGE = "<insufficient privilege>"

# $n fuera de strings y comentarios (mask_sql ya los vacio). El lookbehind
# deja fuera identificadores que contienen $ (col$1).
_PARAM = re.compile(r"(?<![\w$])\$\d+")


def interval_from_env() -> float:
    return _positive_from_env("ACTIVITY_SAMPLE_INTERVAL_S", DEFAULT_INTERVAL_S)


USABLE = "usable"
PARAMETERIZED = "parameterized"
DISCARDED = "discarded"


def sample_text_status(query_text: str | None, truncated: bool = False) -> str:
    """USABLE: sirve para EXPLAIN tal como esta. PARAMETERIZED: seria
    explicable, pero trae $n (protocolo extendido / prepared statements: el
    cliente manda los valores aparte y pg_stat_activity no los muestra).
    DISCARDED: cualquier otro motivo (vacio, truncado, sin privilegio, DML,
    varias sentencias). Lo explicable se mira antes que los $n: un UPDATE con
    $1 no se habria usado de todos modos y no cuenta como PARAMETERIZED."""
    if not query_text or truncated or query_text == INSUFFICIENT_PRIVILEGE:
        return DISCARDED
    code = mask_sql(query_text)
    if code is None:
        return DISCARDED
    if not (is_explainable_command(query_text) and is_single_statement(query_text)):
        return DISCARDED
    if _PARAM.search(code):
        return PARAMETERIZED
    return USABLE


def usable_sample_text(query_text: str | None, truncated: bool = False) -> bool:
    """True si el texto sirve para EXPLAIN tal como esta."""
    return sample_text_status(query_text, truncated) == USABLE


class ActivitySampler:
    def __init__(self, buffer, interval=None, clock=time.monotonic, stop=None):
        self.buffer = buffer
        self.interval = interval if interval is not None else interval_from_env()
        self.clock = clock
        self.stop_event = stop if stop is not None else threading.Event()
        self._lock = threading.Lock()
        self._targets = {}   # db_id -> Target
        self._engines = {}   # db_id -> Engine
        self._errors = {}    # db_id -> exc_type del ultimo tick fallido
        # db_id -> {USABLE: n, PARAMETERIZED: n} desde la ultima lectura del
        # ciclo (drain_counts). Cuenta filas por tick: una conexion idle suma
        # una vez por segundo, asi que es una proporcion, no un numero de queries.
        self._counts = {}
        self._thread = None

    # ---------- interfaz con el runner (hilo principal) ----------

    def update_targets(self, targets) -> None:
        """Targets activos del ciclo. Los que salen se olvidan del buffer."""
        postgres = {t.db_id: t for t in (targets or []) if t.dialect == "postgres" and t.db_id}
        with self._lock:
            self._targets = postgres
            for db_id in [d for d in self._errors if d not in postgres]:
                del self._errors[db_id]
            for db_id in [d for d in self._counts if d not in postgres]:
                del self._counts[db_id]
        # Los engines de los targets que salieron los suelta el hilo del
        # sampler en su proximo tick: solo ese hilo toca _engines.
        self.buffer.retain_targets(postgres)

    def drain_counts(self, db_id) -> dict:
        """Filas usables y con $n vistas desde la lectura anterior; reinicia."""
        with self._lock:
            counts = self._counts.pop(db_id, None) or {}
        return {USABLE: counts.get(USABLE, 0), PARAMETERIZED: counts.get(PARAMETERIZED, 0)}

    def status(self, db_id) -> str | None:
        """exc_type del ultimo tick fallido de ese target, o None si va bien."""
        with self._lock:
            return self._errors.get(db_id)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="activity-sampler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # ---------- hilo del sampler ----------

    def run_forever(self) -> None:
        logger.info(f"activity_sampler | arrancado | intervalo {self.interval:g}s")
        try:
            while not self.stop_event.is_set():
                started = self.clock()
                self.tick()
                self.stop_event.wait(max(0.0, started + self.interval - self.clock()))
        finally:
            for db_id in list(self._engines):
                self._dispose(db_id)
            logger.info("activity_sampler | detenido")

    def tick(self) -> None:
        with self._lock:
            targets = list(self._targets.values())
        active = {t.db_id for t in targets}
        for db_id in [d for d in self._engines if d not in active]:
            self._dispose(db_id)
        for target in targets:
            if self.stop_event.is_set():
                return
            try:
                offered = self._sample(target)
            except Exception as e:
                self._record_error(target.db_id, e)
                continue
            with self._lock:
                self._errors.pop(target.db_id, None)
            logger.debug(f"activity_sampler | {target.db_id} | {offered} sample(s) ofrecidos")

    def _sample(self, target) -> int:
        engine = self._engines.get(target.db_id)
        if engine is None:
            engine = target.factory()
            self._engines[target.db_id] = engine
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = '{TICK_STATEMENT_TIMEOUT}'"))
            rows = conn.execute(text(ACTIVITY_SAMPLES_QUERY)).mappings().all()
        offered = 0
        parameterized = 0
        for row in rows:
            status = sample_text_status(row["query_text"], row["truncated"])
            if status == PARAMETERIZED:
                parameterized += 1
            if status != USABLE:
                continue
            self.buffer.offer(
                target.db_id,
                row["database_name"],
                row["query_id"],
                row["query_text"],
                row["duration_ms"],
            )
            offered += 1
        with self._lock:
            counts = self._counts.setdefault(target.db_id, {USABLE: 0, PARAMETERIZED: 0})
            counts[USABLE] += offered
            counts[PARAMETERIZED] += parameterized
        return offered

    def _record_error(self, db_id, error: Exception) -> None:
        exc_type = type(error).__name__
        with self._lock:
            previous = self._errors.get(db_id)
            self._errors[db_id] = exc_type
        # Log por transicion: un target caido loguearia cada segundo.
        if previous != exc_type:
            logger.error(f"activity_sampler | {db_id} | {exc_type}: {str(error)[:200]}")
        # Engine roto (credenciales nuevas, servidor reiniciado): se recrea.
        self._dispose(db_id)

    def _dispose(self, db_id) -> None:
        engine = self._engines.pop(db_id, None)
        if engine is None:
            return
        try:
            engine.dispose()
        except Exception as e:
            logger.debug(f"activity_sampler | dispose | {type(e).__name__}: {e}")
