"""PreflightStage: umbrales por motor y cache con TTL."""
import pytest

from health import preflight
from health.preflight import PreflightStage
from health.report import HealthReport

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_cache():
    preflight.reset_cache()
    yield
    preflight.reset_cache()


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Conn:
    """Responde por substring del SQL; un valor Exception se lanza."""

    def __init__(self, answers):
        self.answers = answers
        self.sent = []
        self.rollback_calls = 0

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        for needle, rows in self.answers.items():
            if needle in sql:
                if isinstance(rows, Exception):
                    raise rows
                return _Result(rows)
        return _Result([])

    def rollback(self):
        self.rollback_calls += 1


class _Collector:
    def __init__(self, dialect):
        self.source_dialect = dialect
        self.engine = None


class _Clock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _pg_conn(version_num=170002, track_counts="on", dealloc=0, recently_reset=False):
    return _Conn({
        "server_version_num": [
            {"version_num": version_num, "version": "17.2", "track_counts": track_counts}
        ],
        "pg_stat_statements_info": [{"dealloc": dealloc, "recently_reset": recently_reset}],
    })


def _run(conn, dialect="postgres", report=None, clock=None, ttl=300):
    report = report or HealthReport()
    stage = PreflightStage(_Collector(dialect), report, ttl=ttl, clock=clock or _Clock(0))
    stage.execute(conn)
    return report


def _codes(report):
    return {code for code, _ in report.issues}


# ---------- Postgres ----------


def test_healthy_postgres_reports_nothing_and_marks_scope():
    report = _run(_pg_conn())
    assert _codes(report) == set()
    assert "preflight" in report.scopes
    assert report.engine_version == "17.2"


def test_postgres_thresholds():
    report = _run(_pg_conn(version_num=160004, track_counts="off", recently_reset=True))
    assert _codes(report) == {"PG_VERSION_UNSUPPORTED", "TRACK_COUNTS_OFF", "STATS_RECENTLY_RESET"}
    assert report.issues[("PG_VERSION_UNSUPPORTED", "")]["server_version_num"] == 160004


# ---------- STATEMENTS_EVICTING: delta de dealloc entre preflights ----------


def test_first_preflight_only_records_dealloc_baseline():
    # dealloc es acumulado: 12 puede ser un descarte viejo ya corregido.
    report = _run(_pg_conn(dealloc=12))
    assert "STATEMENTS_EVICTING" not in _codes(report)
    assert report.facts["pg_dealloc"] == 12


def test_growing_dealloc_reports_eviction_with_delta():
    clock = _Clock(0, 301)
    _run(_pg_conn(dealloc=12), clock=clock)
    report = _run(_pg_conn(dealloc=20), clock=clock)
    assert report.issues[("STATEMENTS_EVICTING", "")]["evicted"] == 8


def test_stable_dealloc_resolves_eviction():
    # El cliente subio pg_stat_statements.max: dealloc deja de crecer y el
    # siguiente preflight (que si marca el scope) ya no lo reporta.
    clock = _Clock(0, 301, 602)
    _run(_pg_conn(dealloc=12), clock=clock)
    _run(_pg_conn(dealloc=20), clock=clock)
    report = _run(_pg_conn(dealloc=20), clock=clock)
    assert "STATEMENTS_EVICTING" not in _codes(report)
    assert "preflight" in report.scopes


def test_dealloc_reset_is_a_new_baseline():
    clock = _Clock(0, 301)
    _run(_pg_conn(dealloc=500), clock=clock)
    report = _run(_pg_conn(dealloc=3), clock=clock)  # pg_stat_statements_reset()
    assert "STATEMENTS_EVICTING" not in _codes(report)


def test_missing_extension_is_left_to_collect():
    conn = _pg_conn()
    conn.answers["pg_stat_statements_info"] = RuntimeError("relation does not exist")
    report = _run(conn)
    assert _codes(report) == set()
    # La transaccion abortada se descarta antes del collect.
    assert conn.rollback_calls == 1


# ---------- MySQL ----------


def _my_conn(ps=1, consumers=None, limit=1024):
    consumers = consumers or {
        "global_instrumentation": "YES",
        "thread_instrumentation": "YES",
        "statements_digest": "YES",
        "events_statements_current": "YES",
    }
    return _Conn({
        "@@performance_schema": [{"performance_schema": ps, "version": "8.4.0", "sql_text_limit": limit}],
        "setup_consumers": [{"name": n, "enabled": e} for n, e in consumers.items()],
    })


def test_healthy_mysql_reports_nothing_and_keeps_text_limit():
    report = _run(_my_conn(), dialect="mysql")
    assert _codes(report) == set()
    assert report.facts == {"sql_text_limit": 1024}


def test_mysql_performance_schema_off_stops_there():
    conn = _my_conn(ps=0)
    report = _run(conn, dialect="mysql")
    assert _codes(report) == {"PERFORMANCE_SCHEMA_OFF"}
    assert not [s for s in conn.sent if "setup_consumers" in s]


def test_mysql_disabled_consumers():
    conn = _my_conn(consumers={"statements_digest": "NO", "events_statements_current": "YES"})
    report = _run(conn, dialect="mysql")
    assert _codes(report) == {"PS_CONSUMER_DISABLED"}
    assert report.issues[("PS_CONSUMER_DISABLED", "")]["consumers"] == ["statements_digest"]


def test_mysql_preflight_never_reads_grants():
    # PROCESS se detecta por el resultado de active_queries (classify), no por
    # USER_PRIVILEGES, que no ve lo concedido via ROLE.
    conn = _my_conn()
    _run(conn, dialect="mysql")
    assert not [s for s in conn.sent if "PRIVILEGES" in s.upper() or "GRANTS" in s.upper()]


# ---------- TTL ----------


def test_within_ttl_reuses_result_without_marking_scope():
    clock = _Clock(0, 100)
    first = _run(_pg_conn(track_counts="off"), clock=clock)
    assert "preflight" in first.scopes

    conn = _pg_conn()  # ya arreglado, pero el TTL no vencio
    second = _run(conn, clock=clock)
    assert conn.sent == []
    assert _codes(second) == {"TRACK_COUNTS_OFF"}
    assert "preflight" not in second.scopes, "sin re-evaluar no se puede resolver nada"


def test_after_ttl_checks_again():
    clock = _Clock(0, 301)
    _run(_pg_conn(track_counts="off"), clock=clock)
    second = _run(_pg_conn(), clock=clock)
    assert _codes(second) == set()
    assert "preflight" in second.scopes


@pytest.mark.parametrize("raw, ttl", [("", 300.0), ("60", 60.0), ("0", 0.0), ("-5", 300.0), ("x", 300.0)])
def test_ttl_from_env(monkeypatch, raw, ttl):
    monkeypatch.setenv("HEALTH_PREFLIGHT_TTL_S", raw)
    assert preflight.ttl_from_env() == ttl


def test_unknown_dialect_is_a_noop():
    conn = _Conn({})
    report = _run(conn, dialect="oracle")
    assert conn.sent == [] and report.scopes == set()
