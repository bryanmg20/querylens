from abc import ABC, abstractmethod

from stages import canonicalizers, explain_normalizer as explain_norm, normalize, selectors


class DB_Engine_Collector(ABC):
    @abstractmethod
    def collect_telemetry(self):
        pass

    def select_high_impact_time_statements(self):
        selectors.select_high_impact_time_statements(self.stats)

    def select_unstable_statements(self):
        selectors.select_unstable_statements(self.stats)

    def select_disk_spill_indicator(self):
        selectors.select_disk_spill_indicator(self.stats)

    def select_candidates_to_explain(self):
        selectors.select_candidates_to_explain(self.stats)

    def select_explain_ready(self):
        selectors.select_explain_ready(self.stats)

    def canonicalize_query(self, query_text):
        return canonicalizers.canonicalize_query(
            query_text, getattr(self, "source_dialect", "postgres")
        )

    def normalize_querytext_active(self):
        canonicalizers.normalize_querytext_active(
            self.stats, getattr(self, "source_dialect", "postgres")
        )

    def anonimize_query_text(self):
        canonicalizers.anonimize_query_text(self.stats)

    def normalize_active_query_timestamps(self):
        normalize.normalize_active_query_timestamps(self.stats)

    def normalize_locks(self):
        normalize.normalize_locks(self.stats)

    def normalize_predicate(self):
        normalize.normalize_predicate(self.stats)

    def normalize_blocking_pids(self):
        normalize.normalize_blocking_pids(self.stats)

    def normalize_explain(self):
        explain_norm.EXPLAIN_NORMALIZERS[self.source_dialect]().normalize(self.stats)

    def get_stats(self):
        return self.stats

    def preprocess_statements(self, stats):
        return stats

    def normalize_engine_artifacts(self, stats):
        return None