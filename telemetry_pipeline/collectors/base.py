from models.stats import Stats


class DB_Engine_Collector:
    def preprocess_statements(self, stats: Stats) -> Stats:
        return stats

    def normalize_engine_artifacts(self, stats: Stats) -> Stats:
        return stats

    def mark_explainable(self, stats: Stats) -> Stats:
        return stats

    def use_server_version(self, version_num: int | None) -> None:
        """Ajusta las consultas a la version del servidor (la lee el preflight)."""