import json

import pytest

from health.report import HealthReport
from stages import pg_log
from stages.auto_explain import AutoExplainStage
from stages.explain import ExplainStage
from stages.explain_normalizer import PostgresExplainNormalizer
from stages.normalize import redact_postgres_plan_predicate

pytestmark = pytest.mark.unit


def _plan(filter_text="(id < 2000)", total_cost=10.0):
    return {
        "Node Type": "Seq Scan",
        "Relation Name": "sbtest1",
        "Alias": "sbtest1",
        "Total Cost": total_cost,
        "Plan Rows": 5,
        "Filter": filter_text,
    }


def _log_line(query_id, plan=None, dbname="ql_demo", query_text="SELECT 1 WHERE id < 2000"):
    """Una linea jsonlog como la escribe Postgres con auto_explain.log_format=json."""
    logged = {"Query Text": query_text, "Plan": plan if plan is not None else _plan()}
    record = {
        "timestamp": "2026-10-11 22:27:31.899 UTC",
        "user": "ql_user",
        "dbname": dbname,
        "error_severity": "LOG",
        "message": "duration: 664.173 ms  plan:\n" + json.dumps(logged, indent=2, ensure_ascii=False),
        "backend_type": "client backend",
        "query_id": query_id,
    }
    # Postgres escribe el log en UTF-8 sin escapar.
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def _other_line():
    return json.dumps(
        {"user": "ql_user", "dbname": "ql_demo", "message": "checkpoint complete"},
        separators=(",", ":"),
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _Tx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, *exc):
        if exc_type is None:
            self._conn.commit_calls += 1
        else:
            self._conn.rollback_calls += 1


class _LogConn:
    """Sirve querylens.log_files() / querylens.read_log() desde archivos en memoria."""

    def __init__(self, files=None, fail=None):
        # files: [(name, bytes)] del mas nuevo al mas viejo, como ORDER BY modification DESC.
        self.files = files or []
        self.fail = fail
        self.sent = []
        self.reads = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def execute(self, stmt):
        sql = str(stmt)
        self.sent.append(sql)
        if self.fail is not None:
            raise self.fail
        if "querylens.log_files()" in sql:
            return _FakeResult([{"name": n, "size": len(c)} for n, c in self.files])
        if "querylens.read_log" in sql:
            params = stmt.compile().params
            self.reads.append(params)
            content = dict(self.files).get(params["name"])
            if content is None:
                return _FakeResult([{"chunk": None}])
            start = params["start_offset"]
            chunk = content[start:start + params["max_bytes"]]
            return _FakeResult([{"chunk": memoryview(chunk)}])
        raise AssertionError(f"SQL inesperado: {sql}")

    def begin(self):
        return _Tx(self)


class _PgCollector:
    source_dialect = "postgres"


class _MyCollector:
    source_dialect = "mysql"


def _stats(*candidates):
    return {
        "top_impact_queries": [
            {"query_id": qid, "ready_for_explain": True, "database_name": "ql_demo", **extra}
            for qid, extra in candidates
        ]
    }


def _file(*lines):
    return ("\n".join(lines) + "\n").encode()


def _run(conn, stats, collector=None, report=None):
    stage = AutoExplainStage(collector or _PgCollector(), report)
    stage.execute(stats, conn)
    return stats["auto_explain_plans"]


def test_reads_the_logged_plan_of_a_candidate():
    conn = _LogConn([("postgresql-Sun.json", _file(_other_line(), _log_line(111)))])
    plans = _run(conn, _stats((111, {})))
    assert plans[111]["Plan"]["Node Type"] == "Seq Scan"


def test_only_the_plan_is_kept_never_the_query_text():
    """Query Text / Query Parameters traen los valores reales de la query."""
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111, query_text="SELECT 'secreto'")))])
    plans = _run(conn, _stats((111, {})))
    assert list(plans[111]) == ["Plan"]
    assert "secreto" not in json.dumps(plans)


def test_the_most_recent_plan_wins():
    conn = _LogConn([
        ("postgresql-Sun.json", _file(
            _log_line(111, _plan(total_cost=1.0)),
            _log_line(111, _plan(total_cost=2.0)),
        )),
    ])
    plans = _run(conn, _stats((111, {})))
    assert plans[111]["Plan"]["Total Cost"] == 2.0


def test_newer_file_wins_over_older_file():
    conn = _LogConn([
        ("postgresql-Sun.json", _file(_log_line(111, _plan(total_cost=2.0)))),
        ("postgresql-Sat.json", _file(_log_line(111, _plan(total_cost=1.0)))),
    ])
    plans = _run(conn, _stats((111, {})))
    assert plans[111]["Plan"]["Total Cost"] == 2.0


def test_older_file_is_read_when_newest_lacks_the_candidate():
    conn = _LogConn([
        ("postgresql-Sun.json", _file(_log_line(222))),
        ("postgresql-Sat.json", _file(_log_line(111))),
    ])
    plans = _run(conn, _stats((111, {}), (222, {})))
    assert set(plans) == {111, 222}


def test_stops_reading_once_every_candidate_has_a_plan():
    conn = _LogConn([
        ("postgresql-Sun.json", _file(_log_line(111))),
        ("postgresql-Sat.json", _file(_log_line(111))),
    ])
    _run(conn, _stats((111, {})))
    assert [r["name"] for r in conn.reads] == ["postgresql-Sun.json"]


def test_query_id_that_is_not_a_candidate_is_ignored():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(999)))])
    assert _run(conn, _stats((111, {}))) == {}


def test_query_id_prefix_does_not_match_a_longer_id():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(1110)))])
    assert _run(conn, _stats((111, {}))) == {}


def test_negative_query_ids_match():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(-8797025180952568737)))])
    plans = _run(conn, _stats((-8797025180952568737, {})))
    assert -8797025180952568737 in plans


def test_plan_from_another_database_is_ignored():
    """queryid no incluye la base: la misma query en otra base no es este candidato."""
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111, dbname="ventas")))])
    assert _run(conn, _stats((111, {}))) == {}


def test_candidate_without_database_accepts_any_database():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111, dbname="ventas")))])
    assert 111 in _run(conn, _stats((111, {"database_name": None})))


def test_candidates_not_ready_for_explain_are_not_looked_up():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111)))])
    stats = _stats((111, {"ready_for_explain": False}))
    assert _run(conn, stats) == {}
    assert conn.sent == []


def test_no_candidates_sends_no_sql():
    conn = _LogConn()
    assert _run(conn, {"top_impact_queries": []}) == {}
    assert conn.sent == []


def test_mysql_never_reads_the_log():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111)))])
    assert _run(conn, _stats((111, {})), collector=_MyCollector()) == {}
    assert conn.sent == []


def test_text_format_plan_is_skipped():
    line = json.dumps(
        {"dbname": "ql_demo", "message": "duration: 60.1 ms  plan:\nQuery Text: SELECT 1\nResult", "query_id": 111},
        separators=(",", ":"),
    )
    conn = _LogConn([("postgresql-Sun.json", _file(line))])
    assert _run(conn, _stats((111, {}))) == {}


def test_partial_last_line_is_skipped():
    """La ultima linea puede estar a medio escribir cuando se lee el archivo."""
    full = _log_line(111, _plan(total_cost=1.0))
    partial = _log_line(111, _plan(total_cost=2.0))[:-40]
    content = (full + "\n" + partial).encode()
    conn = _LogConn([("postgresql-Sun.json", content)])
    plans = _run(conn, _stats((111, {})))
    assert plans[111]["Plan"]["Total Cost"] == 1.0


def test_window_reads_the_tail_and_drops_the_cut_first_line(monkeypatch):
    first = _log_line(111, _plan(total_cost=1.0))
    second = _log_line(111, _plan(total_cost=2.0))
    content = _file(first, second)
    # La ventana empieza en medio de `first`: solo `second` entra completa.
    monkeypatch.setattr(pg_log, "LOG_WINDOW_BYTES", len(second) + 50)
    conn = _LogConn([("postgresql-Sun.json", content)])
    plans = _run(conn, _stats((111, {})))
    assert conn.reads[0]["start_offset"] == len(content) - (len(second) + 50)
    assert plans[111]["Plan"]["Total Cost"] == 2.0


def test_window_cut_inside_a_multibyte_character_does_not_break(monkeypatch):
    first = _log_line(111, query_text="SELECT 'ññññññññ'")
    second = _log_line(222)
    content = _file(first, second)
    # Cortar dentro de una ñ (2 bytes) deja bytes que no son UTF-8 valido.
    cut = content.index("ñ".encode()) + 1
    monkeypatch.setattr(pg_log, "LOG_WINDOW_BYTES", len(content) - cut)
    conn = _LogConn([("postgresql-Sun.json", content)])
    plans = _run(conn, _stats((111, {}), (222, {})))
    assert set(plans) == {222}


def test_window_budget_is_shared_across_files(monkeypatch):
    newest = _file(_log_line(222))
    older = _file(_log_line(111))
    monkeypatch.setattr(pg_log, "LOG_WINDOW_BYTES", len(newest) + 10)
    conn = _LogConn([("postgresql-Sun.json", newest), ("postgresql-Sat.json", older)])
    _run(conn, _stats((111, {}), (222, {})))
    assert conn.reads[1]["max_bytes"] == 10


def test_file_rotated_between_list_and_read_is_skipped():
    """read_log devuelve NULL (missing_ok) si el archivo ya no existe."""

    class _RotatedConn(_LogConn):
        def execute(self, stmt):
            if "querylens.read_log" in str(stmt):
                self.reads.append(stmt.compile().params)
                return _FakeResult([{"chunk": None}])
            return super().execute(stmt)

    conn = _RotatedConn([
        ("postgresql-Sun.json", _file(_log_line(111))),
        ("postgresql-Sat.json", _file(_log_line(111))),
    ])
    assert _run(conn, _stats((111, {}))) == {}
    assert len(conn.reads) == 2


def test_missing_functions_fall_back_and_report_the_issue():
    report = HealthReport()
    conn = _LogConn(fail=RuntimeError("function querylens.log_files() does not exist"))
    plans = _run(conn, _stats((111, {})), report=report)
    assert plans == {}
    assert conn.rollback_calls == 1
    assert report.has("AUTO_EXPLAIN_LOG_UNAVAILABLE")


def test_successful_read_commits_its_short_transaction():
    conn = _LogConn([("postgresql-Sun.json", _file(_log_line(111)))])
    _run(conn, _stats((111, {})))
    assert conn.commit_calls == 1
    assert conn.rollback_calls == 0


# --- ExplainStage: auto_explain primero, GENERIC_PLAN si no hay ---


class _ExplainConn:
    def __init__(self):
        self.sent = []

    def execute(self, stmt):
        self.sent.append(str(stmt))
        return _FakeResult([{"QUERY PLAN": [{"Plan": _plan("(id < $1)")}]}])

    def begin(self):
        return _Tx(self)

    commit_calls = 0
    rollback_calls = 0


class _ExplainCollector:
    source_dialect = "postgres"
    engine = None


def _explain_stats(logged_plans):
    return {
        "top_impact_queries": [
            {"query_id": 111, "query_text": "SELECT * FROM sbtest1 WHERE id < $1", "ready_for_explain": True},
            {"query_id": 222, "query_text": "SELECT * FROM sbtest2 WHERE id < $1", "ready_for_explain": True},
        ],
        "auto_explain_plans": logged_plans,
    }


def test_explain_uses_the_logged_plan_and_skips_the_explain():
    conn = _ExplainConn()
    stats = _explain_stats({111: {"Plan": _plan("(id < 2000)")}})
    ExplainStage(_ExplainCollector()).execute(stats, conn)
    sources = {e["query_id"]: e["explain_source"] for e in stats["canonic_explains"]}
    assert sources == {111: "auto_explain", 222: "generic"}
    explains = [s for s in conn.sent if s.startswith("EXPLAIN")]
    assert len(explains) == 1
    assert "sbtest2" in explains[0]


def test_explain_falls_back_to_generic_without_logged_plans():
    conn = _ExplainConn()
    stats = _explain_stats({})
    ExplainStage(_ExplainCollector()).execute(stats, conn)
    assert {e["explain_source"] for e in stats["canonic_explains"]} == {"generic"}


def test_postgres_15_explains_only_with_logged_plans():
    # EXPLAIN (GENERIC_PLAN) es de PG 16: en 15 el candidato sin plan del log se
    # salta, sin EXPLAIN ni NO_EXPLAINABLE_CANDIDATES (ya avisa PG_VERSION_LIMITED)
    conn = _ExplainConn()
    report = HealthReport()
    report.facts["pg_version_num"] = 150019
    stats = _explain_stats({111: {"Plan": _plan("(id < 2000)")}})
    ExplainStage(_ExplainCollector(), report).execute(stats, conn)
    assert [e["explain_source"] for e in stats["canonic_explains"]] == ["auto_explain"]
    assert not [s for s in conn.sent if s.startswith("EXPLAIN")]

    report = HealthReport()
    report.facts["pg_version_num"] = 150019
    ExplainStage(_ExplainCollector(), report).execute(_explain_stats({}), _ExplainConn())
    assert not report.issues


def test_logged_plans_never_stay_in_stats():
    stats = _explain_stats({111: {"Plan": _plan("(c = 'secreto'::text)")}})
    ExplainStage(_ExplainCollector()).execute(stats, _ExplainConn())
    assert "auto_explain_plans" not in stats
    assert "secreto" not in json.dumps(stats, default=str)


def test_logged_plan_still_respects_the_read_only_gate():
    stats = {
        "top_impact_queries": [
            {"query_id": 111, "query_text": "UPDATE t SET a = $1", "ready_for_explain": True},
        ],
        "auto_explain_plans": {111: {"Plan": _plan()}},
    }
    ExplainStage(_ExplainCollector()).execute(stats, _ExplainConn())
    assert stats["canonic_explains"] == []


# --- Redaccion de los predicados del plan real ---


def _normalize(source, filter_text):
    stats = {"query_explain": [{"query_id": 1, "explain_source": source, "plan": {"Plan": _plan(filter_text)}}]}
    PostgresExplainNormalizer().normalize(stats)
    return stats["canonic_explains"][0]["canonical_plan"]["physical_operations"][0]["predicate"]


def test_auto_explain_predicates_are_redacted():
    assert _normalize("auto_explain", "(c <> 'secret-literal'::bpchar)") == "(c <> $1)"


def test_generic_predicates_are_untouched():
    assert _normalize("generic", "(id < 2000)") == "(id < 2000)"


def test_auto_explain_join_predicates_are_redacted():
    plan = {
        "Node Type": "Hash Join",
        "Join Filter": "(a.k > 10)",
        "Plans": [_plan("(id < 5)")],
    }
    stats = {"query_explain": [{"query_id": 1, "explain_source": "auto_explain", "plan": {"Plan": plan}}]}
    PostgresExplainNormalizer().normalize(stats)
    ops = stats["canonic_explains"][0]["canonical_plan"]["physical_operations"]
    assert [op["predicate"] for op in ops] == ["(a.k > $1)", "(id < $1)"]


@pytest.mark.parametrize(
    "raw, redacted",
    [
        ("(id < 2000)", "(id < $1)"),
        ("((c)::text ~~ '%abc%'::text)", "((c)::text ~~ $1)"),
        ("(name = 'O''Brien'::text)", "(name = $1)"),
        ("(k = ANY ('{1,2,3}'::integer[]))", "(k = ANY ($1))"),
        ("((id > 10) AND (price < 1.5))", "((id > $1) AND (price < $2))"),
        ("(x > 1.5e3)", "(x > $1)"),
        ("(s = E'a\\\\b'::text)", "(s = $1)"),
        ("(b = B'101'::bit(3))", "(b = $1)"),
        ("((v)::text = 'x'::character varying(20))", "((v)::text = $1)"),
        ("(ts > '2024-01-01 00:00:00'::timestamp without time zone)", "(ts > $1)"),
        ("(ts > '2024-01-01'::timestamp(3) with time zone)", "(ts > $1)"),
        ("(p > '1.5'::double precision)", "(p > $1)"),
        ("(n = '7'::numeric(10,2))", "(n = $1)"),
        ("(id = '5'::public.\"My Type\")", "(id = $1)"),
        ("(id = '5'::text::integer)", "(id = $1)"),
        ("(id = '2000'::bigint)", "(id = $1)"),
        ("((a = ($1)::text) AND (b = 5))", "((a = ($1)::text) AND (b = $2))"),
        ("(\"col 1\" = 7)", "(\"col 1\" = $1)"),
        ("(t.col2 = 3)", "(t.col2 = $1)"),
        ("(NOT (hashed SubPlan 1))", "(NOT (hashed SubPlan 1))"),
        ("(a = (InitPlan 1).col1)", "(a = (InitPlan 1).col1)"),
        ("((a = $1) AND (b = 5))", "((a = $1) AND (b = $2))"),
        ("(a.k = b.k)", "(a.k = b.k)"),
        ("(flag IS TRUE)", "(flag IS TRUE)"),
        (None, None),
    ],
)
def test_redact_postgres_plan_predicate(raw, redacted):
    assert redact_postgres_plan_predicate(raw) == redacted
