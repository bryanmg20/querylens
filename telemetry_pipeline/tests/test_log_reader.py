import csv

import pytest

from stages import log_reader

pytestmark = pytest.mark.unit


def _pg_row(message="", query="", log_time="2026-09-16 10:00:00.000 UTC"):
    cols = [""] * 23
    cols[0] = log_time
    cols[13] = message
    cols[19] = query
    return cols


def _write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)


def test_pg_csvlog_extracts_duration_and_statement(tmp_path):
    path = tmp_path / "postgresql.log"
    _write_csv(
        path,
        [
            _pg_row(
                message="duration: 150.250 ms  statement: SELECT c FROM sbtest1 WHERE id = 1\n",
            )
        ],
    )

    entries = log_reader.parse_pg_csvlog(path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.duration_ms == 150.25
    assert entry.raw_text == "SELECT c FROM sbtest1 WHERE id = 1"
    assert entry.source == "postgres"
    assert entry.log_time == "2026-09-16 10:00:00.000 UTC"
    assert entry.canonical_text


def test_pg_csvlog_falls_back_to_statement_column(tmp_path):
    path = tmp_path / "postgresql.log"
    _write_csv(path, [_pg_row(query="SELECT c FROM sbtest1 WHERE id = 99")])

    entries = log_reader.parse_pg_csvlog(path)

    assert len(entries) == 1
    assert entries[0].raw_text == "SELECT c FROM sbtest1 WHERE id = 99"
    assert entries[0].duration_ms is None


def test_pg_csvlog_skips_non_explainable(tmp_path):
    path = tmp_path / "postgresql.log"
    _write_csv(
        path,
        [
            _pg_row(message="duration: 5.000 ms  statement: SET work_mem = '64MB'\n"),
            _pg_row(query="BEGIN"),
            _pg_row(log_time="2026-09-16 10:01:00.000 UTC"),
        ],
    )

    assert log_reader.parse_pg_csvlog(path) == []


def test_mysql_slow_log_parses_block(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# User@Host: ql_user[ql_user] @ localhost [127.0.0.1]  Id:    10",
                "# Query_time: 2.500000  Lock_time: 0.000000  Rows_sent: 5  Rows_examined: 100",
                "# Schema: ql_demo",
                "SET timestamp=1694712000;",
                "SELECT c FROM sbtest1 WHERE id = 1",
                ";",
                "",
            ]
        ),
        encoding="utf-8",
    )

    entries = log_reader.parse_mysql_slow_log(path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.duration_ms == 2500.0
    assert entry.log_time == "2026-09-16 10:00:00 -0300"
    assert entry.source == "mysql"
    assert entry.raw_text == "SELECT c FROM sbtest1 WHERE id = 1"
    assert entry.canonical_text


def test_mysql_slow_log_multiple_blocks(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# Query_time: 1.000000  Lock_time: 0.000000  Rows_sent: 1  Rows_examined: 10",
                "# Schema: ql_demo",
                "SELECT a FROM sbtest1 WHERE id = 1;",
                "# Time: 2026-09-16 10:01:00 -0300",
                "# Query_time: 0.500000  Lock_time: 0.000000  Rows_sent: 2  Rows_examined: 20",
                "# Schema: ql_demo",
                "SELECT b FROM sbtest1 WHERE id = 2;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    entries = log_reader.parse_mysql_slow_log(path)

    assert len(entries) == 2
    assert [e.duration_ms for e in entries] == [1000.0, 500.0]
    assert [e.raw_text for e in entries] == [
        "SELECT a FROM sbtest1 WHERE id = 1",
        "SELECT b FROM sbtest1 WHERE id = 2",
    ]


def test_mysql_slow_log_drops_consecutive_durations_kept(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# User@Host: ql_user[ql_user] @ localhost [127.0.0.1]  Id:    10",
                "# Query_time: 3.000000  Lock_time: 0.000000  Rows_sent: 1  Rows_examined: 10",
                "# Schema: ql_demo",
                "SELECT c FROM sbtest1 WHERE id = 1;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    entries = log_reader.parse_mysql_slow_log(path)

    assert len(entries) == 1
    assert entries[0].duration_ms == 3000.0


def _pg_log_with_entries(tmp_path, pairs):
    path = tmp_path / "postgresql.log"
    rows = []
    for duration_ms, statement in pairs:
        message = f"duration: {duration_ms} ms  statement: {statement}\n"
        rows.append(_pg_row(message=message))
    _write_csv(path, rows)
    return path


def test_mysql_slow_log_skips_use_and_timestamp_lines(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# Query_time: 3.000000  Lock_time: 0.000000  Rows_sent: 1  Rows_examined: 10",
                "use ql_demo;",
                "SET timestamp=1694712000;",
                "SELECT c FROM sbtest1 WHERE id = 1;",
                "",
            ]
        ),
        encoding="utf-8",
    )

    entries = log_reader.parse_mysql_slow_log(path)

    assert len(entries) == 1
    assert entries[0].raw_text == "SELECT c FROM sbtest1 WHERE id = 1"
    assert entries[0].duration_ms == 3000.0


def test_build_log_index_keeps_max_duration_per_canonical(tmp_path):
    path = _pg_log_with_entries(
        tmp_path,
        [
            (100.0, "SELECT c FROM sbtest1 WHERE id = 1"),
            (300.0, "SELECT c FROM sbtest1 WHERE id = 2"),
        ],
    )

    index = log_reader.build_log_index("postgres", path, min_duration_ms=100)

    assert len(index) == 1
    entry = next(iter(index.values()))
    assert entry.duration_ms == 300.0
    assert entry.raw_text == "SELECT c FROM sbtest1 WHERE id = 2"


def test_build_log_index_filters_by_min_duration(tmp_path):
    path = _pg_log_with_entries(
        tmp_path,
        [(50.0, "SELECT c FROM sbtest1 WHERE id = 1")],
    )

    index = log_reader.build_log_index("postgres", path, min_duration_ms=100)

    assert index == {}


def test_build_log_index_unknown_dialect_returns_empty():
    assert log_reader.build_log_index("oracle", "whatever") == {}


def test_is_explainable_prefixes():
    assert log_reader._is_explainable("SELECT 1")
    assert log_reader._is_explainable("with cte as (select 1) select * from cte")
    assert not log_reader._is_explainable("SET work_mem = '1MB'")
    assert not log_reader._is_explainable(None)