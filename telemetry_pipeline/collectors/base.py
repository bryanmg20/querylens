from models.stats import Stats


class DB_Engine_Collector:
    def preprocess_statements(self, stats: Stats) -> Stats:
        return stats

    def normalize_engine_artifacts(self, stats: Stats) -> Stats:
        return stats