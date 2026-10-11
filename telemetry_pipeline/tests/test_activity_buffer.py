"""ActivitySampleBuffer: regla de reemplazo de MySQL, TTL entre ciclos y tope.

El reloj se inyecta: ninguna prueba duerme.
"""
import threading

import pytest

from activity.buffer import ActivitySampleBuffer

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def _buffer(clock, **kwargs):
    defaults = dict(max_age_s=60.0, ttl_s=300.0, max_entries=1000)
    defaults.update(kwargs)
    return ActivitySampleBuffer(clock=clock, **defaults)


def test_lookup_returns_the_offered_sample():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "ql_demo", 1, "SELECT 1 FROM t WHERE id = 7", 3.0)

    sample = buf.lookup("db", "ql_demo", 1)

    assert sample.query_text == "SELECT 1 FROM t WHERE id = 7"
    assert sample.duration_ms == 3.0


def test_key_separates_targets_and_databases():
    """query_id colisiona entre bases: la clave lleva db_id y database_name."""
    buf = _buffer(_Clock())
    buf.offer("db", "a", 1, "SELECT 'a'", 1.0)

    assert buf.lookup("db", "b", 1) is None
    assert buf.lookup("other", "a", 1) is None


def test_slower_sample_replaces_the_stored_one():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "fast", 1.0)
    clock.now = 5
    buf.offer("db", "d", 1, "slow", 50.0)

    assert buf.lookup("db", "d", 1).query_text == "slow"


def test_faster_sample_does_not_replace_a_fresh_one():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "slow", 50.0)
    clock.now = 59
    buf.offer("db", "d", 1, "fast", 1.0)

    assert buf.lookup("db", "d", 1).query_text == "slow"


def test_equal_duration_does_not_replace():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "first", 5.0)
    clock.now = 10
    buf.offer("db", "d", 1, "second", 5.0)

    assert buf.lookup("db", "d", 1).query_text == "first"


def test_sample_older_than_max_age_is_replaced_even_by_a_faster_one():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "old slow", 50.0)
    clock.now = 61
    buf.offer("db", "d", 1, "new fast", 1.0)

    sample = buf.lookup("db", "d", 1)
    assert sample.query_text == "new fast"
    assert sample.captured_at == 61


def test_max_age_counts_from_capture_not_from_last_seen():
    """Ver el query_id sin reemplazar no rejuvenece el sample guardado."""
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "slow", 50.0)
    for t in (20, 40, 60):
        clock.now = t
        buf.offer("db", "d", 1, "fast", 1.0)
    assert buf.lookup("db", "d", 1).query_text == "slow"

    clock.now = 61
    buf.offer("db", "d", 1, "fresh", 1.0)
    assert buf.lookup("db", "d", 1).query_text == "fresh"


def test_lookup_does_not_consume_the_sample():
    buf = _buffer(_Clock())
    buf.offer("db", "d", 1, "q", 1.0)

    assert buf.lookup("db", "d", 1) is not None
    assert buf.lookup("db", "d", 1) is not None


def test_entry_survives_cycles_until_ttl_without_being_seen():
    clock = _Clock()
    buf = _buffer(clock, ttl_s=300.0)
    buf.offer("db", "d", 1, "q", 1.0)

    clock.now = 300
    assert buf.lookup("db", "d", 1) is not None
    clock.now = 300.5
    assert buf.lookup("db", "d", 1) is None


def test_seeing_the_query_id_again_extends_its_life():
    clock = _Clock()
    buf = _buffer(clock, ttl_s=300.0)
    buf.offer("db", "d", 1, "slow", 50.0)
    clock.now = 250
    buf.offer("db", "d", 1, "fast", 1.0)

    clock.now = 500
    assert buf.lookup("db", "d", 1) is not None


def test_expired_entries_are_purged_when_a_new_key_arrives():
    clock = _Clock()
    buf = _buffer(clock, ttl_s=10.0)
    buf.offer("db", "d", 1, "q1", 1.0)
    buf.offer("db", "d", 2, "q2", 1.0)
    clock.now = 11
    buf.offer("db", "d", 3, "q3", 1.0)

    assert buf.size("db") == 1


def test_cap_evicts_the_least_recently_seen_entry():
    clock = _Clock()
    buf = _buffer(clock, max_entries=2)
    buf.offer("db", "d", 1, "q1", 1.0)
    clock.now = 1
    buf.offer("db", "d", 2, "q2", 1.0)
    clock.now = 2
    buf.offer("db", "d", 1, "q1 again", 1.0)
    clock.now = 3
    buf.offer("db", "d", 3, "q3", 1.0)

    assert buf.size("db") == 2
    assert buf.lookup("db", "d", 2) is None
    assert buf.lookup("db", "d", 1) is not None
    assert buf.lookup("db", "d", 3) is not None


def test_cap_is_per_target():
    buf = _buffer(_Clock(), max_entries=1)
    buf.offer("a", "d", 1, "q", 1.0)
    buf.offer("b", "d", 1, "q", 1.0)

    assert buf.lookup("a", "d", 1) is not None
    assert buf.lookup("b", "d", 1) is not None


def test_retain_targets_drops_inactive_targets():
    buf = _buffer(_Clock())
    buf.offer("keep", "d", 1, "q", 1.0)
    buf.offer("gone", "d", 1, "q", 1.0)

    buf.retain_targets({"keep"})

    assert buf.lookup("keep", "d", 1) is not None
    assert buf.lookup("gone", "d", 1) is None
    assert buf.size("gone") == 0


def test_none_duration_counts_as_zero():
    clock = _Clock()
    buf = _buffer(clock)
    buf.offer("db", "d", 1, "unknown", None)
    clock.now = 1
    buf.offer("db", "d", 1, "measured", 0.5)

    assert buf.lookup("db", "d", 1).query_text == "measured"


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "nan", "inf"])
def test_unusable_env_values_fall_back_to_defaults(monkeypatch, raw):
    monkeypatch.setenv("ACTIVITY_SAMPLE_MAX_AGE_S", raw)
    monkeypatch.setenv("ACTIVITY_SAMPLE_TTL_S", raw)
    monkeypatch.setenv("ACTIVITY_SAMPLE_MAX_ENTRIES", raw)

    buf = ActivitySampleBuffer()

    assert (buf.max_age_s, buf.ttl_s, buf.max_entries) == (60.0, 300.0, 1000)


def test_env_values_are_used(monkeypatch):
    monkeypatch.setenv("ACTIVITY_SAMPLE_MAX_AGE_S", "30")
    monkeypatch.setenv("ACTIVITY_SAMPLE_TTL_S", "120")
    monkeypatch.setenv("ACTIVITY_SAMPLE_MAX_ENTRIES", "50")

    buf = ActivitySampleBuffer()

    assert (buf.max_age_s, buf.ttl_s, buf.max_entries) == (30.0, 120.0, 50)


def test_concurrent_offers_and_lookups_keep_the_cap():
    """Sampler y ciclo en hilos distintos: el tope se respeta y nada lanza."""
    buf = ActivitySampleBuffer(max_age_s=60, ttl_s=300, max_entries=50)
    errors = []

    def writer(base):
        try:
            for i in range(2000):
                buf.offer("db", "d", base + i % 200, f"q{i}", float(i % 7))
        except Exception as e:  # pragma: no cover - solo si hay carrera
            errors.append(e)

    def reader():
        try:
            for i in range(2000):
                buf.lookup("db", "d", i % 200)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n * 1000,)) for n in range(3)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert buf.size("db") <= 50
