"""Samples de queries reales de Postgres, guardados entre ciclos del runner.

pg_stat_statements guarda la query normalizada ($1, $2): el EXPLAIN de
Postgres solo puede pedir el plan generico. MySQL tiene QUERY_SAMPLE_TEXT, una
ejecucion real con literales. Este buffer es el equivalente para Postgres: el
sampler (activity/sampler.py) lee pg_stat_activity cada segundo y deja aqui
una query real por (db_id, database_name, query_id); el ciclo del snapshot la
consulta al hacer EXPLAIN.

Es el unico estado que el pipeline guarda entre ciclos, y es descartable: si
el proceso reinicia el buffer arranca vacio y el EXPLAIN cae al generico, como
antes. Vive solo en memoria: los literales no se escriben en disco, en la
cola ni en los logs (mismo trato que query_sample_text en MySQL).

Reglas:
  * reemplazo (la de performance_schema para QUERY_SAMPLE_TEXT): un sample
    nuevo pisa al guardado si tardo mas o si el guardado tiene mas de
    max_age_s. Asi se prioriza la ejecucion lenta sin quedarse con una vieja;
  * vida: una entrada vive mientras su query_id se siga viendo; caduca a los
    ttl_s sin verse. Leer no borra: el mismo sample sirve a varios ciclos;
  * tope: max_entries por target; al pasarlo sale la vista hace mas tiempo.

Lo usan dos hilos (sampler escribe, ciclo lee): todo pasa por un Lock.
"""
import math
import os
import threading
import time
from dataclasses import dataclass

DEFAULT_MAX_AGE_S = 60.0
DEFAULT_TTL_S = 300.0
DEFAULT_MAX_ENTRIES = 1000


@dataclass(frozen=True)
class Sample:
    query_text: str
    # idle: lo que tardo la query que termino; active: lo que lleva corriendo.
    duration_ms: float
    # Reloj monotono: cuando se guardo ESTE texto (para max_age_s).
    captured_at: float


@dataclass
class _Entry:
    sample: Sample
    # Ultima vez que se vio el query_id, con o sin reemplazo (para ttl_s).
    last_seen: float


def _positive_from_env(name: str, default: float) -> float:
    """Valor del env en segundos/entradas; cualquier valor no usable cae al default."""
    raw = os.getenv(name, "")
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


class ActivitySampleBuffer:
    def __init__(
        self,
        max_age_s: float | None = None,
        ttl_s: float | None = None,
        max_entries: int | None = None,
        clock=time.monotonic,
    ):
        self.max_age_s = (
            max_age_s
            if max_age_s is not None
            else _positive_from_env("ACTIVITY_SAMPLE_MAX_AGE_S", DEFAULT_MAX_AGE_S)
        )
        self.ttl_s = (
            ttl_s if ttl_s is not None
            else _positive_from_env("ACTIVITY_SAMPLE_TTL_S", DEFAULT_TTL_S)
        )
        self.max_entries = int(
            max_entries
            if max_entries is not None
            else _positive_from_env("ACTIVITY_SAMPLE_MAX_ENTRIES", DEFAULT_MAX_ENTRIES)
        )
        self.clock = clock
        self._lock = threading.Lock()
        # db_id -> {(database_name, query_id): _Entry}
        self._targets: dict[str, dict[tuple, _Entry]] = {}

    def offer(self, db_id, database_name, query_id, query_text, duration_ms) -> None:
        """Registra que el query_id se vio con este texto; decide si lo guarda."""
        now = self.clock()
        key = (database_name, query_id)
        duration_ms = float(duration_ms or 0.0)
        with self._lock:
            entries = self._targets.setdefault(db_id, {})
            entry = entries.get(key)
            if entry is None:
                self._expire(entries, now)
                if len(entries) >= self.max_entries:
                    oldest = min(entries, key=lambda k: entries[k].last_seen)
                    del entries[oldest]
                entries[key] = _Entry(Sample(query_text, duration_ms, now), now)
                return
            entry.last_seen = now
            stored = entry.sample
            if duration_ms > stored.duration_ms or now - stored.captured_at > self.max_age_s:
                entry.sample = Sample(query_text, duration_ms, now)

    def lookup(self, db_id, database_name, query_id) -> Sample | None:
        """Sample vigente o None. No borra: otro ciclo puede volver a usarlo."""
        now = self.clock()
        with self._lock:
            entry = self._targets.get(db_id, {}).get((database_name, query_id))
            if entry is None or now - entry.last_seen > self.ttl_s:
                return None
            return entry.sample

    def retain_targets(self, db_ids) -> None:
        """Olvida los targets que ya no estan activos (fila desactivada o borrada)."""
        keep = set(db_ids)
        with self._lock:
            for db_id in list(self._targets):
                if db_id not in keep:
                    del self._targets[db_id]

    def size(self, db_id) -> int:
        with self._lock:
            return len(self._targets.get(db_id, {}))

    def _expire(self, entries: dict, now: float) -> None:
        for key in [k for k, e in entries.items() if now - e.last_seen > self.ttl_s]:
            del entries[key]
