import pytest

from collectors.base import DB_Engine_Collector
from collectors.mysql.collector import Mysql_Collector
from collectors.postgres.collector import Postgres_Collector

pytestmark = pytest.mark.unit


def _candidates_with_samples(samples):
    return {
        "top_impact_queries": [
            {
                "query_id": i,
                "query_text": f"SELECT {i}",
                "query_sample_text": sample,
            }
            for i, sample in enumerate(samples)
        ]
    }


class TestBaseDefault:
    def test_mark_explainable_is_noop(self):
        """El default de la base no marca nada; cada dialecto lo sobrescribe."""
        stats = {"top_impact_queries": [{"query_id": 1}]}
        assert DB_Engine_Collector().mark_explainable(stats) is stats
        assert "ready_for_explain" not in stats["top_impact_queries"][0]


class TestPostgresMarkExplainable:
    def test_marks_all_candidates_ready(self):
        collector = Postgres_Collector(engine=None)
        stats = _candidates_with_samples([None, None, None])
        collector.mark_explainable(stats)
        assert all(c["ready_for_explain"] is True for c in stats["top_impact_queries"])

    def test_marks_ready_without_requiring_active_queries(self):
        collector = Postgres_Collector(engine=None)
        stats = _candidates_with_samples([None])
        stats["active_queries"] = []
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is True

    def test_tolerates_empty_candidate_list(self):
        collector = Postgres_Collector(engine=None)
        stats = {"top_impact_queries": []}
        assert collector.mark_explainable(stats) is stats


class TestMysqlMarkExplainable:
    def test_marks_ready_when_sample_present(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples(["select 1"])
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is True

    def test_not_ready_when_sample_missing(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples([None])
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False

    def test_not_ready_when_sample_blank(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples(["   \n\t "])
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False

    def test_not_ready_when_sample_empty_string(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples([""])
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False

    def test_handles_candidate_without_sample_key(self):
        collector = Mysql_Collector(engine=None)
        stats = {"top_impact_queries": [{"query_id": 1, "query_text": "SELECT ?"}]}
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False

    def test_mixed_readiness_across_candidates(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples(["select 1", None, "select 3"])
        collector.mark_explainable(stats)
        assert [c["ready_for_explain"] for c in stats["top_impact_queries"]] == [
            True,
            False,
            True,
        ]


    def test_history_execution_is_attached_to_its_candidate(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples(["select 1", "select 2"])
        stats["statement_history"] = [
            {"query_id": 1, "history_text": "select 2 /* ejecucion */", "history_rows_sent": 7},
        ]
        collector.mark_explainable(stats)
        first, second = stats["top_impact_queries"]
        assert "history_text" not in first
        assert second["history_text"] == "select 2 /* ejecucion */"
        assert second["history_rows_sent"] == 7

    def test_history_alone_makes_the_candidate_ready(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples([None])
        stats["statement_history"] = [{"query_id": 0, "history_text": "select 0", "history_rows_sent": 1}]
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is True

    def test_blank_history_text_is_ignored(self):
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples([None])
        stats["statement_history"] = [{"query_id": 0, "history_text": "  ", "history_rows_sent": 1}]
        collector.mark_explainable(stats)
        assert stats["top_impact_queries"][0]["ready_for_explain"] is False
        assert "history_text" not in stats["top_impact_queries"][0]

    def test_history_with_literals_does_not_stay_in_stats(self):
        """Trae literales reales, como el sample: no puede llegar al snapshot."""
        collector = Mysql_Collector(engine=None)
        stats = _candidates_with_samples(["select 1"])
        stats["statement_history"] = [{"query_id": 0, "history_text": "select 1", "history_rows_sent": 1}]
        collector.mark_explainable(stats)
        assert "statement_history" not in stats


class TestMysqlCalculateStddevCoeff:
    def test_handles_none_statements(self):
        """C-1: calculate_stddev_coeff must not crash when stats['statements']=None."""
        collector = Mysql_Collector(engine=None)
        stats = {"statements": None}
        result = collector.calculate_stddev_coeff(stats)
        assert result is stats  # returns same dict
        assert stats["statements"] is None  # unchanged


class TestStatementsQueryContract:
    def test_mysql_selects_query_sample_text(self):
        from collectors.mysql.queries import STATEMENTS_QUERY

        assert "QUERY_SAMPLE_TEXT" in STATEMENTS_QUERY
        assert "AS query_sample_text" in STATEMENTS_QUERY

    def test_mysql_history_query_keeps_one_finished_execution_per_digest(self):
        from collectors.mysql.queries import STATEMENT_HISTORY_QUERY

        assert "events_statements_history" in STATEMENT_HISTORY_QUERY
        assert "AS history_text" in STATEMENT_HISTORY_QUERY
        assert "AS history_rows_sent" in STATEMENT_HISTORY_QUERY
        # la mas lenta de cada digest, y solo las que terminaron sin error
        assert "ORDER BY TIMER_WAIT DESC" in STATEMENT_HISTORY_QUERY
        assert "END_EVENT_ID IS NOT NULL" in STATEMENT_HISTORY_QUERY
        assert "ERRORS = 0" in STATEMENT_HISTORY_QUERY

    def test_mysql_excludes_server_internal_digests(self):
        from collectors.mysql.queries import STATEMENTS_QUERY

        for pattern in ("SELECT @@%", "SELECT `VERSION`%", "SET @@%", "SHOW %"):
            assert pattern in STATEMENTS_QUERY
