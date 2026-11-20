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
    report = _run(_pg_conn(version_num=140011, track_counts="off", recently_reset=True))
    assert _codes(report) == {"PG_VERSION_UNSUPPORTED", "TRACK_COUNTS_OFF", "STATS_RECENTLY_RESET"}
    assert report.issues[("PG_VERSION_UNSUPPORTED", "")]["server_version_num"] == 140011


@pytest.mark.parametrize("version_num, missing", [
    (160004, ["pg_stat_statements.stats_since"]),
    (150019, ["pg_stat_statements.stats_since", "EXPLAIN (GENERIC_PLAN)", "pg_stat_user_indexes.last_idx_scan"]),
])
def test_postgres_15_and_16_are_limited_not_blocking(version_num, missing):
    report = _run(_pg_conn(version_num=version_num))
    assert _codes(report) == {"PG_VERSION_LIMITED"}
    params = report.issues[("PG_VERSION_LIMITED", "")]
    assert (params["server_version_num"], params["missing"]) == (version_num, missing)
    assert report.facts["pg_version_num"] == version_num


@pytest.mark.parametrize("version_num", [170002, 180001, 190000])
def test_postgres_17_and_later_report_no_version_issue(version_num):
    report = _run(_pg_conn(version_num=version_num))
    assert _codes(report) == set()
    assert report.facts["pg_version_num"] == version_num


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


def test_tables_with_lost_stats_report_the_crash():
    conn = _pg_conn()
    conn.answers["n_live_tup = 0"] = [
        {"tables": 4, "examples": ["public.items_pedido", "public.pedidos", "public.clientes"]}
    ]
    report = _run(conn)
    assert _codes(report) == {"TABLE_STATS_LOST"}
    params = report.issues[("TABLE_STATS_LOST", "")]
    assert params["tables"] == 4
    assert params["examples"] == ["public.items_pedido", "public.pedidos", "public.clientes"]


def test_no_table_with_lost_stats_reports_nothing():
    conn = _pg_conn()
    conn.answers["n_live_tup = 0"] = [{"tables": 0, "examples": None}]
    assert _codes(_run(conn)) == set()


def test_lost_stats_query_error_is_discarded():
    conn = _pg_conn()
    conn.answers["n_live_tup = 0"] = RuntimeError("permission denied")
    report = _run(conn)
    assert _codes(report) == set()
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
    # STATS_RECENTLY_RESET no se arregla con una accion: espera al TTL
    clock = _Clock(0, 100)
    first = _run(_pg_conn(recently_reset=True), clock=clock)
    assert "preflight" in first.scopes

    conn = _pg_conn()  # ya no aplica, pero el TTL no vencio
    second = _run(conn, clock=clock)
    assert conn.sent == []
    assert _codes(second) == {"STATS_RECENTLY_RESET"}
    assert "preflight" not in second.scopes, "sin re-evaluar no se puede resolver nada"


def test_after_ttl_checks_again():
    clock = _Clock(0, 301)
    _run(_pg_conn(track_counts="off"), clock=clock)
    second = _run(_pg_conn(), clock=clock)
    assert _codes(second) == set()
    assert "preflight" in second.scopes


def test_refresh_evicts_entries_of_bases_no_longer_monitored():
    # una base desregistrada deja de refrescarse; una activa se refresca cada TTL
    preflight._CACHE["postgresql://gone@h/db"] = (0.0, preflight.PreflightResult())
    preflight._CACHE["postgresql://active@h/db"] = (3000.0, preflight.PreflightResult())

    _run(_pg_conn(), clock=_Clock(3700.0))

    assert "postgresql://gone@h/db" not in preflight._CACHE
    assert "postgresql://active@h/db" in preflight._CACHE
    assert "postgres" in preflight._CACHE


def test_eviction_horizon_scales_with_a_long_ttl():
    preflight._CACHE["postgresql://slow@h/db"] = (0.0, preflight.PreflightResult())
    # TTL 1800 s: el horizonte es 3 x 1800 = 5400 s, no la hora minima
    _run(_pg_conn(), clock=_Clock(5000.0), ttl=1800)
    assert "postgresql://slow@h/db" in preflight._CACHE


@pytest.mark.parametrize("raw, ttl", [("", 300.0), ("60", 60.0), ("0", 0.0), ("-5", 300.0), ("x", 300.0)])
def test_ttl_from_env(monkeypatch, raw, ttl):
    monkeypatch.setenv("HEALTH_PREFLIGHT_TTL_S", raw)
    assert preflight.ttl_from_env() == ttl


def test_unknown_dialect_is_a_noop():
    conn = _Conn({})
    report = _run(conn, dialect="oracle")
    assert conn.sent == [] and report.scopes == set()


# ---------- rechequeo de avisos arreglables dentro del TTL ----------


def _lost(conn, tables):
    conn.answers["n_live_tup = 0"] = [{"tables": tables, "examples": ["public.t"] if tables else None}]
    return conn


def test_analyze_resolves_table_stats_lost_on_the_next_recheck():
    clock = _Clock(0, 30)
    first = _run(_lost(_pg_conn(), 3), clock=clock)
    assert _codes(first) == {"TABLE_STATS_LOST"}

    conn = _lost(_Conn({}), 0)  # el usuario corrio ANALYZE
    second = _run(conn, clock=clock)
    assert _codes(second) == set()
    assert "preflight" in second.scopes, "el aviso tiene que resolverse ya, no al vencer el TTL"
    assert conn.sent == [preflight.PG_TABLE_STATS_LOST], "solo se repite la consulta del aviso abierto"


def test_table_stats_lost_still_open_is_kept_with_fresh_params():
    clock = _Clock(0, 30)
    _run(_lost(_pg_conn(), 3), clock=clock)
    second = _run(_lost(_Conn({}), 1), clock=clock)
    assert _codes(second) == {"TABLE_STATS_LOST"}
    assert second.issues[("TABLE_STATS_LOST", "")]["tables"] == 1


def test_recheck_query_error_keeps_the_cached_issue():
    clock = _Clock(0, 30)
    _run(_lost(_pg_conn(), 3), clock=clock)
    conn = _Conn({"n_live_tup = 0": RuntimeError("timeout")})
    second = _run(conn, clock=clock)
    assert _codes(second) == {"TABLE_STATS_LOST"}, "sin evaluar no se da por resuelto"
    assert conn.rollback_calls == 1


def test_track_counts_on_resolves_on_the_next_recheck():
    clock = _Clock(0, 30)
    _run(_pg_conn(track_counts="off"), clock=clock)
    conn = _Conn({"current_setting('track_counts')": [{"track_counts": "on"}]})
    second = _run(conn, clock=clock)
    assert _codes(second) == set()
    assert conn.sent == [preflight.PG_TRACK_COUNTS]


def test_enabled_consumers_resolve_on_the_next_recheck():
    clock = _Clock(0, 30)
    _run(_my_conn(consumers={"statements_digest": "NO"}), dialect="mysql", clock=clock)
    conn = _Conn({"setup_consumers": [{"name": "statements_digest", "enabled": "YES"}]})
    second = _run(conn, dialect="mysql", clock=clock)
    assert _codes(second) == set()
    assert "preflight" in second.scopes


def test_recheck_keeps_the_other_cached_issues_and_the_ttl_cadence():
    clock = _Clock(0, 30, 290, 301)
    _run(_lost(_pg_conn(recently_reset=True), 2), clock=clock)

    second = _run(_lost(_Conn({}), 0), clock=clock)  # t=30: ANALYZE hecho
    assert _codes(second) == {"STATS_RECENTLY_RESET"}, "lo no arreglable sigue cacheado"

    third = _run(_Conn({}), clock=clock)  # t=290: sin avisos arreglables, no consulta nada
    assert _codes(third) == {"STATS_RECENTLY_RESET"}
    assert "preflight" not in third.scopes

    conn = _pg_conn()
    fourth = _run(conn, clock=clock)  # t=301: vence el TTL del chequeo de t=0
    assert "preflight" in fourth.scopes
    assert any("server_version_num" in sql for sql in conn.sent), "chequeo completo al vencer el TTL"


def test_recheck_waits_for_the_interval():
    clock = _Clock(0, 10, 29, 30)
    _run(_lost(_pg_conn(), 3), clock=clock)
    for _ in range(2):  # t=10 y t=29: antes del intervalo, ni una consulta
        conn = _lost(_Conn({}), 0)
        report = _run(conn, clock=clock)
        assert conn.sent == []
        assert _codes(report) == {"TABLE_STATS_LOST"}
        assert "preflight" not in report.scopes
    conn = _lost(_Conn({}), 0)
    assert _codes(_run(conn, clock=clock)) == set()  # t=30
    assert conn.sent == [preflight.PG_TABLE_STATS_LOST]


def test_interval_counts_from_the_last_recheck():
    clock = _Clock(0, 30, 50, 60)
    _run(_lost(_pg_conn(), 3), clock=clock)
    _run(_lost(_Conn({}), 2), clock=clock)  # t=30: rechequeo, sigue abierto
    conn = _lost(_Conn({}), 0)
    _run(conn, clock=clock)  # t=50: 20 s desde el rechequeo
    assert conn.sent == []
    conn = _lost(_Conn({}), 0)
    assert _codes(_run(conn, clock=clock)) == set()  # t=60


# ---------- MySQL: performance_schema y estadisticas reiniciadas ----------


def _recent(conn, restarted=0, digests_reset=0):
    conn.answers["global_status"] = [{"restarted": restarted, "digests_reset": digests_reset}]
    return conn


def test_mysql_restart_reports_recently_reset_stats():
    report = _run(_recent(_my_conn(), restarted=1), dialect="mysql")
    assert _codes(report) == {"STATS_RECENTLY_RESET"}


def test_mysql_truncated_digests_report_recently_reset_stats():
    report = _run(_recent(_my_conn(), digests_reset=1), dialect="mysql")
    assert _codes(report) == {"STATS_RECENTLY_RESET"}


def test_mysql_old_stats_report_nothing():
    assert _codes(_run(_recent(_my_conn()), dialect="mysql")) == set()


def test_mysql_recently_reset_query_error_is_discarded():
    conn = _my_conn()
    conn.answers["global_status"] = RuntimeError("denied")
    assert _codes(_run(conn, dialect="mysql")) == set()


def test_performance_schema_on_after_restart_resolves_on_the_next_recheck():
    clock = _Clock(0, 30)
    _run(_my_conn(ps=0), dialect="mysql", clock=clock)
    conn = _recent(_my_conn(ps=1), restarted=1)  # reinicio con performance_schema=ON
    report = _run(conn, dialect="mysql", clock=clock)
    assert _codes(report) == {"STATS_RECENTLY_RESET"}, "el reinicio que lo encendio se informa"
    assert "preflight" in report.scopes
    assert not any("@@version" in sql for sql in conn.sent), "no es el chequeo completo"


def test_performance_schema_still_off_stays():
    clock = _Clock(0, 30)
    _run(_my_conn(ps=0), dialect="mysql", clock=clock)
    conn = _my_conn(ps=0)
    report = _run(conn, dialect="mysql", clock=clock)
    assert _codes(report) == {"PERFORMANCE_SCHEMA_OFF"}
    assert not [sql for sql in conn.sent if "setup_consumers" in sql]
