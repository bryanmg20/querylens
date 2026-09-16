from models.stats import Stats
from stages import selectors

class CandidatesStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats: Stats) -> Stats:
        stats = self.collector.preprocess_statements(stats)
        selectors.select_high_impact_time_statements(stats)
        selectors.select_unstable_statements(stats)
        selectors.select_disk_spill_indicator(stats)
        selectors.select_candidates_to_explain(stats)
        selectors.select_explain_ready(stats)
        return stats