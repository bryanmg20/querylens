"""ActivitySampler: filtros de texto, aislamiento por target y ciclo de vida.

Sin base real: el engine es un fake que devuelve las filas de pg_stat_activity
que cada prueba arma.
"""
import pytest

from activity.buffer import ActivitySampleBuffer
from activity.sampler import ActivitySampler, usable_sample_text
from config.registered import Target

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Conn:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt):
        sql = str(stmt)
        self.engine.statements.append(sql)
        if self.engine.error is not None:
            raise self.engine.error
        if sql.startswith("SET LOCAL"):
            return _Result([])
        return _Result(self.engine.rows)


class _Engine:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.statements = []
        self.disposed = False

    def begin(self):
        return _Conn(self)

    def dispose(self):
        self.disposed = True


def _row(query_text, query_id=1, database_name="ql_demo", duration_ms=5.0, truncated=False):
    return {
        "database_name": database_name,
        "query_id": query_id,
        "query_text": query_text,
        "duration_ms": duration_ms,
        "truncated": truncated,
    }


def _target(db_id, engine, dialect="postgres", created=None):
    def factory():
        if created is not None:
            created.append(db_id)
        return engine
    return Target(dialect=dialect, factory=factory, db_id=db_id)


def _sampler(**kwargs):
    buffer = ActivitySampleBuffer(max_age_s=60, ttl_s=300, max_entries=100, clock=lambda: 0.0)
    return ActivitySampler(buffer, interval=1.0, **kwargs), buffer


# ---------- usable_sample_text ----------

@pytest.mark.parametrize("text", [
    "SELECT c FROM sbtest1 WHERE id = 797",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT '$1 en un string' FROM t WHERE id = 3",
    "SELECT col$1 FROM t",
    "SELECT 1 -- comentario con $1",
])
def test_usable_sample_text_accepts_literal_read_only_queries(text):
    assert usable_sample_text(text)


@pytest.mark.parametrize("text, truncated", [
    ("SELECT c FROM sbtest1 WHERE id = $1", False),           # protocolo extendido
    ("SELECT * FROM t WHERE a = $1 AND b = $2", False),
    ("SELECT c FROM sbtest1 WHERE id = 7", True),             # cortado por track_activity_query_size
    ("<insufficient privilege>", False),
    ("UPDATE t SET a = 1 WHERE id = 2", False),
    ("SELECT * FROM t FOR UPDATE", False),
    ("SELECT 1; SELECT 2", False),
    ("SELECT 'sin cerrar", False),
    ("", False),
    (None, False),
])
def test_usable_sample_text_rejects_what_explain_cannot_use(text, truncated):
    assert not usable_sample_text(text, truncated)


# ---------- tick ----------

def test_tick_offers_usable_rows_to_the_buffer():
    engine = _Engine([
        _row("SELECT c FROM sbtest1 WHERE id = 797", query_id=1, duration_ms=3.0),
        _row("SELECT c FROM sbtest1 WHERE id = $1", query_id=2),
        _row("UPDATE sbtest1 SET k = 1 WHERE id = 3", query_id=3),
        _row("SELECT 1 FROM t WHERE x = 'y'", query_id=4, truncated=True),
    ])
    sampler, buffer = _sampler()
    sampler.update_targets([_target("db", engine)])

    sampler.tick()

    sample = buffer.lookup("db", "ql_demo", 1)
    assert sample.query_text == "SELECT c FROM sbtest1 WHERE id = 797"
    assert sample.duration_ms == 3.0
    assert buffer.size("db") == 1


def test_tick_bounds_its_own_statement_time():
    engine = _Engine([])
    sampler, _ = _sampler()
    sampler.update_targets([_target("db", engine)])

    sampler.tick()

    assert engine.statements[0] == "SET LOCAL statement_timeout = '2s'"


def test_engine_is_created_once_and_reused():
    created = []
    engine = _Engine([])
    sampler, _ = _sampler()
    sampler.update_targets([_target("db", engine, created=created)])

    sampler.tick()
    sampler.tick()

    assert created == ["db"]


def test_mysql_and_unidentified_targets_are_ignored():
    created = []
    sampler, _ = _sampler()
    sampler.update_targets([
        _target("my", _Engine([]), dialect="mysql", created=created),
        _target(None, _Engine([]), created=created),
    ])

    sampler.tick()

    assert created == []


def test_failing_target_does_not_stop_the_others():
    broken = _Engine(error=RuntimeError("boom"))
    healthy = _Engine([_row("SELECT 1 FROM t WHERE id = 1")])
    sampler, buffer = _sampler()
    sampler.update_targets([_target("broken", broken), _target("ok", healthy)])

    sampler.tick()

    assert sampler.status("broken") == "RuntimeError"
    assert sampler.status("ok") is None
    assert buffer.lookup("ok", "ql_demo", 1) is not None


def test_failed_engine_is_recreated_and_error_clears_on_success():
    created = []
    engine = _Engine(error=RuntimeError("down"))
    sampler, _ = _sampler()
    sampler.update_targets([_target("db", engine, created=created)])

    sampler.tick()
    assert engine.disposed
    assert sampler.status("db") == "RuntimeError"

    engine.error = None
    sampler.tick()
    assert created == ["db", "db"]
    assert sampler.status("db") is None


def test_error_is_logged_once_per_transition(caplog):
    engine = _Engine(error=RuntimeError("down"))
    sampler, _ = _sampler()
    sampler.update_targets([_target("db", engine)])

    with caplog.at_level("ERROR", logger="activity.sampler"):
        sampler.tick()
        sampler.tick()
        sampler.tick()

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1


def test_removed_target_is_forgotten_and_its_engine_released():
    engine = _Engine([_row("SELECT 1 FROM t WHERE id = 1")])
    sampler, buffer = _sampler()
    sampler.update_targets([_target("db", engine)])
    sampler.tick()
    assert buffer.lookup("db", "ql_demo", 1) is not None

    sampler.update_targets([])
    assert buffer.lookup("db", "ql_demo", 1) is None
    assert not engine.disposed        # el hilo del runner no toca engines

    sampler.tick()
    assert engine.disposed


def test_removed_target_error_is_cleared():
    sampler, _ = _sampler()
    sampler.update_targets([_target("db", _Engine(error=RuntimeError("x")))])
    sampler.tick()

    sampler.update_targets([])

    assert sampler.status("db") is None


# ---------- ciclo de vida ----------

class _FakeStop:
    def __init__(self, stop_after):
        self.stop_after = stop_after
        self.checks = 0
        self.waits = []

    def is_set(self):
        self.checks += 1
        return self.checks > self.stop_after

    def wait(self, seconds):
        self.waits.append(seconds)

    def set(self):
        self.stop_after = 0


def test_run_forever_anchors_ticks_and_disposes_on_exit():
    engine = _Engine([])
    times = iter([0.0, 0.25, 1.0, 2.5])
    # is_set se consulta en el while y una vez por target dentro de tick: 2 ticks = 4.
    stop = _FakeStop(stop_after=4)
    buffer = ActivitySampleBuffer(clock=lambda: 0.0)
    sampler = ActivitySampler(buffer, interval=1.0, clock=lambda: next(times), stop=stop)
    sampler.update_targets([_target("db", engine)])

    sampler.run_forever()

    # tick 1: empezo en 0, termino en 0.25 -> espera 0.75; tick 2 duro 1.5 -> 0.
    assert stop.waits == [0.75, 0.0]
    assert engine.disposed


def test_start_and_stop_run_a_real_thread():
    sampler, _ = _sampler()
    sampler.start()
    sampler.stop(timeout=2.0)

    assert not sampler._thread.is_alive()


@pytest.mark.parametrize("raw, expected", [("", 1.0), ("0", 1.0), ("x", 1.0), ("2.5", 2.5)])
def test_interval_from_env(monkeypatch, raw, expected):
    from activity.sampler import interval_from_env
    monkeypatch.setenv("ACTIVITY_SAMPLE_INTERVAL_S", raw)
    assert interval_from_env() == expected
