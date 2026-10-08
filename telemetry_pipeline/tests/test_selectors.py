import pytest

from stages.selectors import (
    select_candidates_to_explain,
    select_disk_spill_indicator,
    init_ready_for_explain,
    select_high_impact_time_statements,
    select_unstable_statements,
)

pytestmark = pytest.mark.unit


def _statement(query_id, query_text="SELECT 1", coeff=None, mean_ms=10, spill=0):
    return {
        "query_id": query_id,
        "query_text": query_text,
        "coeff_of_variation": coeff,
        "mean_time_ms": mean_ms,
        "disk_spill_indicator": spill,
    }


def test_high_impact_takes_top_ten():
    stats = {"statements": [_statement(i) for i in range(12)]}
    select_high_impact_time_statements(stats)
    assert len(stats["high_impact_statements"]) == 10
    assert stats["high_impact_statements"][0]["query_id"] == 0


def test_unstable_filters_by_coeff_and_mean():
    stats = {
        "statements": [
            _statement(1, coeff=3, mean_ms=50),
            _statement(2, coeff=3, mean_ms=5),
            _statement(3, coeff=1, mean_ms=50),
        ]
    }
    select_unstable_statements(stats)
    assert [s["query_id"] for s in stats["unstable_statements"]] == [1]


def test_disk_spill_signal():
    stats = {
        "statements": [
            _statement(1, spill=0),
            _statement(2, spill=5),
        ]
    }
    select_disk_spill_indicator(stats)
    assert [s["query_id"] for s in stats["disk_spill_statements"]] == [2]


def test_candidates_dedupe_and_collect_reasons():
    stats = {
        "high_impact_statements": [_statement(1), _statement(2)],
        "unstable_statements": [_statement(1)],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    top = stats["top_impact_queries"]
    assert len(top) == 2
    by_id = {c["query_id"]: c for c in top}
    assert set(by_id[1]["selected_by"]) == {"time_high_impact", "unstable"}
    assert stats["non_explainable_candidates"] == []
    assert "high_impact_statements" not in stats


def test_non_explainable_command_skipped():
    stats = {
        "high_impact_statements": [_statement(1, query_text="SHOW STATUS")],
        "unstable_statements": [],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    assert stats["top_impact_queries"] == []
    assert stats["non_explainable_candidates"][0]["query_id"] == 1


def test_candidate_without_text_skipped():
    stats = {
        "high_impact_statements": [_statement(1, query_text=None)],
        "unstable_statements": [],
        "disk_spill_statements": [],
    }
    select_candidates_to_explain(stats)
    assert stats["top_impact_queries"] == []
    assert stats["non_explainable_candidates"] == []


def test_all_three_transient_keys_are_dropped():
    stats = {
        "high_impact_statements": [_statement(1)],
        "unstable_statements": [_statement(1)],
        "disk_spill_statements": [_statement(1)],
    }
    select_candidates_to_explain(stats)
    assert "high_impact_statements" not in stats
    assert "unstable_statements" not in stats
    assert "disk_spill_statements" not in stats


def test_explain_ready_starts_unresolved():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "old text"},
            {"query_id": 2, "query_text": "x"},
        ],
        "active_queries": [
            {"query_id": 1, "query_text": "live text"},
            {"query_id": 99, "query_text": "other"},
        ],
    }
    init_ready_for_explain(stats)
    for ready in stats["top_impact_queries"]:
        assert ready["ready_for_explain"] is False
    assert stats["top_impact_queries"][0]["query_text"] == "old text"


def test_explain_ready_default_false():
    stats = {
        "top_impact_queries": [{"query_id": 2, "query_text": "x"}],
        "active_queries": [],
    }
    init_ready_for_explain(stats)
    assert stats["top_impact_queries"][0]["ready_for_explain"] is False


def test_explain_ready_ignores_active_queries():
    """Documenta un gap: la doc dice que marca los ids de active_queries, pero
    el codigo los ignora y deja todo en False. Ver references/004."""
    stats = {
        "top_impact_queries": [{"query_id": 1, "query_text": "a"}],
        "active_queries": [{"query_id": 1, "query_text": "a"}],
    }
    init_ready_for_explain(stats)
    assert stats["top_impact_queries"][0]["ready_for_explain"] is False


def test_explain_ready_dedups_by_query_id():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "a"},
            {"query_id": 1, "query_text": "a"},
        ],
        "active_queries": [],
    }
    init_ready_for_explain(stats)
    assert len(stats["top_impact_queries"]) == 1


def test_explain_ready_skips_candidates_without_query_id():
    stats = {
        "top_impact_queries": [{"query_id": None, "query_text": "a"}],
        "active_queries": [],
    }
    init_ready_for_explain(stats)
    assert stats["top_impact_queries"] == []


# --- C-1 regression: selectors must not crash when statements=None ---


def test_select_high_impact_handles_none_statements():
    """C-1: select_high_impact_time_statements must not crash on stats['statements']=None."""
    stats = {"statements": None}
    select_high_impact_time_statements(stats)
    assert stats["high_impact_statements"] == []


def test_select_unstable_handles_none_statements():
    """C-1: select_unstable_statements must not crash on stats['statements']=None."""
    stats = {"statements": None}
    select_unstable_statements(stats)
    assert stats["unstable_statements"] == []


def test_select_disk_spill_handles_none_statements():
    """C-1: select_disk_spill_indicator must not crash on stats['statements']=None."""
    stats = {"statements": None}
    select_disk_spill_indicator(stats)
    assert stats["disk_spill_statements"] == []