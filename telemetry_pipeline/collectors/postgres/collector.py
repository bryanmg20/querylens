from collectors.base import DB_Engine_Collector
from .queries import (
    INDEXES_QUERY,
    TABLES_QUERY,
    STATEMENTS_QUERY,
    LOCKS_QUERY,
    ACTIVE_QUERIES_QUERY,
    CAPTURE_TIMESTAMP_QUERY,
SERVER_START_QUERY,
    COLUMNS_QUERY,
    SCHEMA_RESOLVER_QUERY,
    FOREIGN_KEYS_QUERY,
    STATEMENTS_QUERY_PG16,
    INDEXES_QUERY_PG15,
)
import stages.normalize as normalize
from models.stats import Stats


class Postgres_Collector(DB_Engine_Collector):
    def __init__(self, engine):
        self.stats = {}
        self.engine = engine
        self.source_dialect = "postgres"
        self.queries = {
                        "indexes": INDEXES_QUERY,
                        "tables": TABLES_QUERY,
                        "statements": STATEMENTS_QUERY,
                        "capture_timestamp": CAPTURE_TIMESTAMP_QUERY,
                        "locks": LOCKS_QUERY,
                        "active_queries": ACTIVE_QUERIES_QUERY,
                        "server_start_timestamp": SERVER_START_QUERY,
                        "columns": COLUMNS_QUERY,
                        "schema_resolver": SCHEMA_RESOLVER_QUERY,
                        "foreign_keys": FOREIGN_KEYS_QUERY
                        }

    def use_server_version(self, version_num: int | None) -> None:
        # Sin version (el preflight no pudo leerla) quedan las de PG 17: si la
        # version no las tiene, falla la seccion y classify lo reporta.
        if version_num is None:
            return
        self.queries["statements"] = STATEMENTS_QUERY if version_num >= 170000 else STATEMENTS_QUERY_PG16
        self.queries["indexes"] = INDEXES_QUERY if version_num >= 160000 else INDEXES_QUERY_PG15

    def normalize_engine_artifacts(self, stats: Stats) -> Stats:
        normalize.normalize_active_query_timestamps(stats)
        return stats

    def mark_explainable(self, stats: Stats) -> Stats:
        for candidate in stats.get("top_impact_queries", []):
            candidate["ready_for_explain"] = True
        return stats