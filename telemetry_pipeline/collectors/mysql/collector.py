from collectors.base import DB_Engine_Collector
from .queries import (
    INDEXES_QUERY,
    TABLES_QUERY,
    STATEMENTS_QUERY,
    LOCKS_QUERY,
    ACTIVE_QUERIES_QUERY,
    STATS_RESET_QUERY,
    COLUMNS_QUERY
)
import stages.normalize as normalize


class Mysql_Collector(DB_Engine_Collector):
    def __init__(self, engine):
        self.stats = {}
        self.engine = engine
        self.source_dialect = "mysql"
        self.queries = {
                        "indexes": INDEXES_QUERY,
                        "tables": TABLES_QUERY,
                        "statements": STATEMENTS_QUERY,
                        "locks": LOCKS_QUERY,
                        "active_queries": ACTIVE_QUERIES_QUERY,
                        "stats_reset_timestamp": STATS_RESET_QUERY,
                        "columns": COLUMNS_QUERY
                        }

    def preprocess_statements(self, stats):
        return self.calculate_stddev_coeff(stats)

    def calculate_stddev_coeff(self, stats=None):
        import math

        if stats is None:
            stats = self.stats

        for stmt in stats.get("statements", []):
            mean = float(stmt.get('mean_time_ms') or 0)
            count = int(stmt.get('execution_count') or 0)
            max_time = float(stmt.get('max_time_ms') or 0)

            if mean > 0 and max_time > mean * 1000:
                stmt['stddev_time_ms'] = None
                stmt['coeff_of_variation'] = None
                continue

            if count > 1 and mean > 0 and max_time > mean:
                stddev = (max_time - mean) / math.sqrt(count)
            else:
                stddev = None

            if stddev is not None and mean > 0:
                coeff_of_variation = stddev / mean
            else:
                coeff_of_variation = None

            stmt['stddev_time_ms'] = stddev
            stmt['coeff_of_variation'] = coeff_of_variation

        return stats

    def normalize_engine_artifacts(self, stats):
        normalize.normalize_locks(stats)
        normalize.normalize_blocking_pids(stats)
        normalize.normalize_predicate(stats)
        return stats