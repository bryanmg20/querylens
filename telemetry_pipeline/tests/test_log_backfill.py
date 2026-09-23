import csv

import pytest

from stages.log_backfill import LogsBackfillStage

pytestmark = pytest.mark.unit


class _FakeCollector:
    def __init__(self, dialect):
        self.source_dialect = dialect


def _pg_log(path, statement, duration_ms=150.0):
    cols = [""] * 23
    cols[0] = "2026-09-16 10:00:00.000 UTC"
    cols[13] = f"duration: {duration_ms} ms  statement: {statement}\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(cols)


def _candidate(query_id=1, query_text="SELECT c FROM sbtest1 WHERE id = 1"):
    return {"query_id": query_id, "query_text": query_text, "real_query_found": False}


def _stats(*candidates):
    return {"top_impact_queries": list(candidates)}


def test_missing_log_file_is_noop(tmp_path):
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(tmp_path / "nope.log"), "min_duration_ms": 100}},
    )
    stats = _stats(_candidate())

    result = stage.execute(stats)

    assert result is stats
    assert stats["top_impact_queries"][0]["real_query_found"] is False
    assert stats["top_impact_queries"][0]["query_text"] == "SELECT c FROM sbtest1 WHERE id = 1"


def test_disabled_source_is_noop(tmp_path):
    path = tmp_path / "postgresql.log"
    _pg_log(path, "SELECT c FROM sbtest1 WHERE id = 1")
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": False, "path": str(path), "min_duration_ms": 100}},
    )
    stats = _stats(_candidate())

    stage.execute(stats)

    assert stats["top_impact_queries"][0]["real_query_found"] is False


def test_unknown_dialect_is_noop():
    stage = LogsBackfillStage(_FakeCollector("oracle"), log_sources={})
    stats = _stats(_candidate())

    stage.execute(stats)

    assert stats["top_impact_queries"][0]["real_query_found"] is False


def test_match_replaces_query_text_with_real_log(tmp_path):
    path = tmp_path / "postgresql.log"
    _pg_log(path, "SELECT c FROM sbtest1 WHERE id = 1")
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(path), "min_duration_ms": 100}},
    )
    candidate = _candidate(query_text="SELECT c FROM sbtest1 WHERE id = 1")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is True
    assert candidate["query_text"] == "SELECT c FROM sbtest1 WHERE id = 1"


def test_no_match_leaves_candidate_untouched(tmp_path):
    path = tmp_path / "postgresql.log"
    _pg_log(path, "SELECT c FROM sbtest1 WHERE id = 9876")
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(path), "min_duration_ms": 100}},
    )
    candidate = _candidate(query_text="SELECT z FROM sbtest9 WHERE id = 42")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is False
    assert candidate["query_text"] == "SELECT z FROM sbtest9 WHERE id = 42"


def test_already_real_candidate_is_not_reprocessed(tmp_path):
    path = tmp_path / "postgresql.log"
    _pg_log(path, "SELECT c FROM sbtest1 WHERE id = 1", duration_ms=200.0)
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(path), "min_duration_ms": 100}},
    )
    candidate = _candidate()
    candidate["real_query_found"] = True
    original_text = candidate["query_text"]
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is True
    assert candidate["query_text"] == original_text


def test_mysql_uses_min_duration_seconds(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# Query_time: 2.500000  Lock_time: 0.000000  Rows_sent: 1  Rows_examined: 10",
                "# Schema: ql_demo",
                "SELECT c FROM sbtest1 WHERE id = 1;",
                "",
            ]
        ),
        encoding="utf-8",
    )
    stage = LogsBackfillStage(
        _FakeCollector("mysql"),
        log_sources={"mysql": {"enabled": True, "path": str(path), "min_duration_seconds": 1}},
    )
    candidate = _candidate(query_text="SELECT c FROM sbtest1 WHERE id = 1")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is True


def test_postgres_placeholder_without_params_left_not_found(tmp_path):
    path = tmp_path / "postgresql.log"
    cols = [""] * 23
    cols[0] = "2026-09-16 10:00:00.000 UTC"
    cols[13] = "duration: 150.000 ms  execute sbstmt-1: UPDATE sbtest1 SET k=k+1 WHERE id=$1\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(cols)
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(path), "min_duration_ms": 100}},
    )
    candidate = _candidate(query_text="UPDATE sbtest1 SET k=k+1 WHERE id=$1")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is False
    assert candidate["query_text"] == "UPDATE sbtest1 SET k=k+1 WHERE id=$1"


def test_postgres_placeholder_with_params_matches(tmp_path):
    path = tmp_path / "postgresql.log"
    cols = [""] * 23
    cols[0] = "2026-09-16 10:00:00.000 UTC"
    cols[13] = "duration: 150.000 ms  execute sbstmt-1: UPDATE sbtest1 SET k=k+1 WHERE id=$1\n"
    cols[14] = "parameters: $1 = '4983'"
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(cols)
    stage = LogsBackfillStage(
        _FakeCollector("postgres"),
        log_sources={"postgres": {"enabled": True, "path": str(path), "min_duration_ms": 100}},
    )
    candidate = _candidate(query_text="UPDATE sbtest1 SET k=k+1 WHERE id=$1")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is True
    assert candidate["query_params"] == {1: "4983"}


def test_mysql_entry_below_threshold_not_matched(tmp_path):
    path = tmp_path / "ql-slow.log"
    path.write_text(
        "\n".join(
            [
                "# Time: 2026-09-16 10:00:00 -0300",
                "# Query_time: 0.250000  Lock_time: 0.000000  Rows_sent: 1  Rows_examined: 10",
                "# Schema: ql_demo",
                "SELECT c FROM sbtest1 WHERE id = 1;",
                "",
            ]
        ),
        encoding="utf-8",
    )
    stage = LogsBackfillStage(
        _FakeCollector("mysql"),
        log_sources={"mysql": {"enabled": True, "path": str(path), "min_duration_seconds": 1}},
    )
    candidate = _candidate(query_text="SELECT c FROM sbtest1 WHERE id = 1")
    stats = _stats(candidate)

    stage.execute(stats)

    assert candidate["real_query_found"] is False