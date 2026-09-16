from collectors.base import DB_Engine_Collector
from .queries import (
    INDEXES_QUERY,
    TABLES_QUERY,
    STATEMENTS_QUERY,
    LOCKS_QUERY,
    ACTIVE_QUERIES_QUERY,
    STATS_RESET_QUERY,
)
import stages.normalize as normalize


class Postgres_Collector(DB_Engine_Collector):
    def __init__(self, engine):
        self.stats = {}
        self.engine = engine
        self.source_dialect = "postgres"
        self.queries = {
                        "indexes": INDEXES_QUERY,
                        "tables": TABLES_QUERY,
                        "statements": STATEMENTS_QUERY,
                        "locks": LOCKS_QUERY,
                        "active_queries": ACTIVE_QUERIES_QUERY,
                        "stats_reset_timestamp": STATS_RESET_QUERY
                        }

    def normalize_engine_artifacts(self, stats):
        normalize.normalize_active_query_timestamps(stats)
        return stats