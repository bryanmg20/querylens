"""DeadlocksStage: lectura de los informes de abrazo mortal en cada motor.

La conexion es falsa y sirve las sentencias del stage desde memoria; los
informes son los de golden/deadlocks/ (ver test_deadlock_reports.py).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from querylens_contract import DeadlockReport

from health.report import HealthReport
from stages import pg_log
from stages.auto_explain import AutoExplainStage
from stages.deadlocks import LOOKBACK_S, DeadlocksStage
from stages.pg_log import PgLogTail

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "golden" / "deadlocks"

# hora del servidor en el ciclo: 10 s despues del CT-01 de cada fixture
PG_NOW = datetime(2026, 10, 22, 0, 46, 11, tzinfo=timezone.utc)
MY_NOW = datetime(2026, 10, 22, 0, 59, 36)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Rows(self._rows)


class _Rows(list):
    def first(self):
        return self[0] if self else None


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, *exc):
        if exc_type is not None:
            self.conn.rollbacks += 1


class _Conn:
    def __init__(self, handlers):
        # [(fragmento del SQL, funcion(params) -> filas | Exception)]
        self.handlers = handlers
        self.sent = []
        self.rollbacks = 0

    def begin(self):
        return _Tx(self)

    def execute(self, stmt):
        sql = str(stmt)
        params = stmt.compile().params
        self.sent.append(sql)
        for fragment, handler in self.handlers:
            if fragment in sql:
                rows = handler(params)
                if isinstance(rows, Exception):
                    raise rows
                return _Result(rows)
        raise AssertionError(f"SQL inesperado: {sql}")


class _Collector:
    def __init__(self, dialect):
        self.source_dialect = dialect


def _run(dialect, conn, report=None):
    stats = {}
    DeadlocksStage(_Collector(dialect), report).execute(stats, conn)
    for item in stats["deadlocks"]:
        DeadlockReport.model_validate(item)
    return stats["deadlocks"]


# --------------------------------------------------------------- postgres

def _pg_log(*records, junk=True):
    lines = []
    if junk:
        lines.append(json.dumps({"message": "checkpoint complete", "error_severity": "LOG"}))
    lines += [json.dumps(record, separators=(",", ":")) for record in records]
    return ("\n".join(lines) + "\n").encode()


def _pg_records():
    return [json.loads(line) for line in (GOLDEN / "postgres_40p01.jsonl").read_text().splitlines()]


def _pg_conn(files, settings=None, relations=(), fail=None):
    """files: [(name, bytes, modification)] del mas nuevo al mas viejo."""
    settings = {
        "log_error_verbosity": "default",
        "log_min_messages": "warning",
        "database_name": "ql_demo",
        "now_utc": PG_NOW.replace(tzinfo=None),
        **(settings or {}),
    }
    contents = {name: content for name, content, _ in files}

    def read_log(params):
        content = contents[params["name"]]
        start = params["start_offset"]
        return [{"chunk": memoryview(content[start:start + params["max_bytes"]])}]

    def timestamps(params):
        # como lo haria el servidor: "... UTC" -> naive UTC
        return [
            {"raw": raw, "utc": datetime.strptime(raw.removesuffix(" UTC"), "%Y-%m-%d %H:%M:%S.%f")}
            for raw in params["raw"]
        ]

    return _Conn([
        ("current_setting('log_error_verbosity')", lambda p: fail or [settings]),
        ("querylens.log_files()", lambda p: [
            {"name": name, "size": len(content), "modification": modified} for name, content, modified in files
        ]),
        ("querylens.read_log", read_log),
        ("unnest(CAST(:raw", timestamps),
        ("pg_catalog.pg_class", lambda p: [row for row in relations if row["oid"] in p["oids"]]),
    ])


def test_postgres_reads_deadlocks_from_the_log_window():
    ct01, ct02 = _pg_records()
    conn = _pg_conn([("postgresql-Thu.json", _pg_log(ct01, ct02), PG_NOW)])

    reports = _run("postgres", conn)

    assert [r["detected_at"] for r in reports] == ["2026-10-22 00:46:01.669000", "2026-10-22 00:46:07.193000"]
    assert reports[0]["transactions"][0]["process_id"] == 790
    assert conn.rollbacks == 0


def test_postgres_drops_reports_older_than_the_lookback():
    ct01, _ = _pg_records()
    conn = _pg_conn(
        [("postgresql-Thu.json", _pg_log(ct01), PG_NOW)],
        settings={"now_utc": (PG_NOW + timedelta(seconds=LOOKBACK_S)).replace(tzinfo=None)},
    )
    assert _run("postgres", conn) == []


def test_postgres_reads_older_files_within_the_window():
    ct01, ct02 = _pg_records()
    conn = _pg_conn([
        ("postgresql-Thu.json", _pg_log(ct02), PG_NOW),
        ("postgresql-Wed.json", _pg_log(ct01), PG_NOW - timedelta(hours=1)),
    ])
    assert [r["transactions"][0]["process_id"] for r in _run("postgres", conn)] == [790, 812]


def test_postgres_window_starting_mid_line_ignores_the_cut_line(monkeypatch):
    ct01, ct02 = _pg_records()
    content = _pg_log(ct01, ct02, junk=False)
    first_line_end = content.index(b"\n") + 1
    conn = _pg_conn([("postgresql-Thu.json", content, PG_NOW)])
    monkeypatch.setattr(pg_log, "LOG_WINDOW_BYTES", len(content) - first_line_end + 10)

    reports = _run("postgres", conn)

    assert [r["transactions"][0]["process_id"] for r in reports] == [812]


def test_postgres_log_is_read_once_per_cycle_with_auto_explain():
    """Deadlocks y auto_explain comparten la ventana: auto_explain recorre lo
    que ya leyo DeadlocksStage sin volver a consultar el log."""
    ct01, _ = _pg_records()
    plan_line = json.dumps({
        "dbname": "ql_demo", "query_id": 111,
        "message": "duration: 60.1 ms  plan:\n" + json.dumps({"Plan": {"Node Type": "Seq Scan"}}),
    }, separators=(",", ":"))
    content = _pg_log(ct01) + (plan_line + "\n").encode()
    conn = _pg_conn([("postgresql-Thu.json", content, PG_NOW)])
    log = PgLogTail()

    stats = {"top_impact_queries": [{"query_id": 111, "ready_for_explain": True, "database_name": "ql_demo"}]}
    DeadlocksStage(_Collector("postgres")).execute(stats, conn, log)
    reads_after_deadlocks = sum("querylens.read_log" in sql for sql in conn.sent)
    AutoExplainStage(_Collector("postgres")).execute(stats, conn, log)

    assert len(stats["deadlocks"]) == 1
    assert 111 in stats["auto_explain_plans"]
    assert reads_after_deadlocks == 1
    assert sum("querylens.read_log" in sql for sql in conn.sent) == 1
    assert sum("querylens.log_files()" in sql for sql in conn.sent) == 1


def test_postgres_resolves_relation_oids_of_the_connected_database():
    record = {
        "timestamp": "2026-10-22 00:46:05.000 UTC",
        "pid": 10,
        "dbname": "ql_demo",
        "state_code": "40P01",
        "detail": (
            "Process 10 waits for AccessExclusiveLock on relation 16390 of database 5; blocked by process 11.\n"
            "Process 11 waits for AccessExclusiveLock on relation 16384 of database 5; blocked by process 10.\n"
            "Process 10: LOCK TABLE b IN ACCESS EXCLUSIVE MODE;\n"
            "Process 11: LOCK TABLE a IN ACCESS EXCLUSIVE MODE;"
        ),
    }
    relations = [
        {"oid": 16384, "schema_name": "public", "table_name": "a"},
        {"oid": 16390, "schema_name": "public", "table_name": "b"},
    ]
    conn = _pg_conn([("postgresql-Thu.json", _pg_log(record), PG_NOW)], relations=relations)

    (report,) = _run("postgres", conn)

    first, second = report["transactions"]
    assert (first["waits_for"]["schema_name"], first["waits_for"]["table_name"]) == ("public", "b")
    assert (second["waits_for"]["schema_name"], second["waits_for"]["table_name"]) == ("public", "a")
    assert "relation_oid" not in first["waits_for"]


def test_postgres_does_not_name_relations_of_another_database():
    # el mismo OID en otra base del servidor es otra tabla
    record = {
        "timestamp": "2026-10-22 00:46:05.000 UTC",
        "pid": 10,
        "dbname": "otra_base",
        "state_code": "40P01",
        "detail": (
            "Process 10 waits for AccessExclusiveLock on relation 16390 of database 9; blocked by process 11.\n"
            "Process 11 waits for AccessExclusiveLock on relation 16384 of database 9; blocked by process 10."
        ),
    }
    relations = [{"oid": 16390, "schema_name": "public", "table_name": "b"}]
    conn = _pg_conn([("postgresql-Thu.json", _pg_log(record), PG_NOW)], relations=relations)

    (report,) = _run("postgres", conn)

    lock = report["transactions"][0]["waits_for"]
    assert lock.get("table_name") is None
    assert "relation_oid" not in lock
    assert not any("pg_catalog.pg_class" in sql for sql in conn.sent)


def test_postgres_terse_verbosity_is_reported_as_partial():
    report = HealthReport()
    conn = _pg_conn([], settings={"log_error_verbosity": "terse"})
    assert _run("postgres", conn, report) == []
    assert report.has("DEADLOCK_REPORTS_PARTIAL")


def test_postgres_without_log_functions_is_unavailable():
    report = HealthReport()
    conn = _pg_conn([])
    conn.handlers.insert(0, ("querylens.log_files()", lambda p: RuntimeError("function querylens.log_files() does not exist")))

    assert _run("postgres", conn, report) == []
    assert report.has("DEADLOCK_REPORTS_UNAVAILABLE")
    assert conn.rollbacks == 1


# ------------------------------------------------------------------ mysql

def _my_conn(status_file="innodb_status_ct01.txt", headers=(), print_all=1, verbosity=3, offset_s=0, error_log=None):
    status = (GOLDEN / status_file).read_text(encoding="utf-8") if status_file else "no deadlocks"
    settings = {
        "now_utc": MY_NOW,
        "system_offset_s": offset_s,
        "print_all_deadlocks": print_all,
        "log_error_verbosity": verbosity,
    }
    return _Conn([
        ("UTC_TIMESTAMP(6) AS now_utc", lambda p: [settings]),
        ("SHOW ENGINE INNODB STATUS", lambda p: [{"Type": "InnoDB", "Name": "", "Status": status}]),
        ("performance_schema.error_log", lambda p: error_log or [{"logged_at": h} for h in headers]),
    ])


def test_mysql_latest_report_takes_the_exact_time_of_its_error_log_header():
    header = datetime(2026, 10, 22, 0, 59, 26, 415843)
    reports = _run("mysql", _my_conn(headers=[header]))

    (report,) = reports
    assert report["source"] == "innodb_status"
    assert report["detected_at"] == "2026-10-22 00:59:26.415843"
    assert report["complete"] is True


def test_mysql_earlier_headers_in_the_window_come_without_detail():
    earlier = datetime(2026, 10, 22, 0, 59, 20, 100000)
    latest = datetime(2026, 10, 22, 0, 59, 26, 415843)

    reports = _run("mysql", _my_conn(headers=[earlier, latest]))

    assert [(r["source"], r["complete"], r["detected_at"]) for r in reports] == [
        ("mysql_error_log", False, "2026-10-22 00:59:20.100000"),
        ("innodb_status", True, "2026-10-22 00:59:26.415843"),
    ]


def test_mysql_status_in_system_time_is_converted_to_utc():
    # servidor en UTC-5: INNODB STATUS dice 00:59:26 local = 05:59:26 UTC
    header = datetime(2026, 10, 22, 5, 59, 26, 415843)
    conn = _my_conn(headers=[header], offset_s=-5 * 3600)
    conn.handlers[0] = ("UTC_TIMESTAMP(6) AS now_utc", lambda p: [{
        "now_utc": MY_NOW + timedelta(hours=5), "system_offset_s": -5 * 3600,
        "print_all_deadlocks": 1, "log_error_verbosity": 3,
    }])

    (report,) = _run("mysql", conn)
    assert report["detected_at"] == "2026-10-22 05:59:26.415843"


def test_mysql_old_latest_report_is_left_out():
    conn = _my_conn()
    conn.handlers[0] = ("UTC_TIMESTAMP(6) AS now_utc", lambda p: [{
        "now_utc": MY_NOW + timedelta(seconds=LOOKBACK_S), "system_offset_s": 0,
        "print_all_deadlocks": 1, "log_error_verbosity": 3,
    }])
    assert _run("mysql", conn) == []


def test_mysql_without_deadlocks_since_startup():
    assert _run("mysql", _my_conn(status_file=None)) == []


def test_mysql_without_print_all_deadlocks_keeps_only_the_latest():
    report = HealthReport()
    conn = _my_conn(print_all=0)

    reports = _run("mysql", conn, report)

    assert [r["detected_at"] for r in reports] == ["2026-10-22 00:59:26.000000"]
    assert report.has("DEADLOCK_REPORTS_PARTIAL")
    assert not any("performance_schema.error_log" in sql for sql in conn.sent)


def test_mysql_without_error_log_table_still_reads_the_status():
    report = HealthReport()
    conn = _my_conn(error_log=RuntimeError("Table 'performance_schema.error_log' doesn't exist"))

    reports = _run("mysql", conn, report)

    assert len(reports) == 1
    assert report.has("DEADLOCK_REPORTS_PARTIAL")


def test_mysql_without_process_privilege_is_unavailable():
    report = HealthReport()
    conn = _my_conn()
    conn.handlers[1] = ("SHOW ENGINE INNODB STATUS", lambda p: RuntimeError("(1227, 'Access denied; you need the PROCESS privilege')"))

    assert _run("mysql", conn, report) == []
    assert report.has("DEADLOCK_REPORTS_UNAVAILABLE")


def test_other_dialects_are_skipped():
    conn = _Conn([])
    assert _run("sqlserver", conn) == []
    assert conn.sent == []
