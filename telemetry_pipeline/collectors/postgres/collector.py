from collectors.base import DB_Engine_Collector
from .queries import INDEXES_QUERY, TABLES_QUERY, STATEMENTS_QUERY, LOCKS_QUERY, ACTIVE_QUERIES_QUERY, STATS_RESET_QUERY
import stages.normalize as normalize
from stages import canonicalizers
from sqlalchemy import text

from logger import get_logger

logger = get_logger(__name__)

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
        
    def collect_telemetry(self):
        with self.engine.connect() as conn:
            for key, query in self.queries.items():

                try:
                    result = conn.execute(text(query))
                    self.stats[key] = [dict(row) for row in result.mappings()]
                except Exception as e:
                    conn.rollback()
                    self.stats[key] = None
                    logger.error(f"postgres | collect_telemetry | query={key} | {e}")

            try:
                self.normalize_active_query_timestamps()
            except Exception as e:
                logger.error(f"postgres | normalize_active_query_timestamps | {e}")

            if self.stats['statements'] is not None:
                
                try:
                    self.select_high_impact_time_statements()
                except Exception as e:
                    logger.error(f"postgres | select_high_impact_time_statements | {e}")

                try:
                    self.select_unstable_statements()
                except Exception as e:
                    logger.error(f"postgres | select_unstable_statements | {e}")

                try:
                    self.select_disk_spill_indicator()
                except Exception as e:
                    logger.error(f"postgres | select_disk_spill_indicator | {e}")

                try:
                    self.select_candidates_to_explain()
                except Exception as e:
                    logger.error(f"postgres | select_candidates_to_explain | {e}")

                try:
                    self.select_explain_ready()
                except Exception as e:
                    logger.error(f"postgres | select_explain_ready | {e}")


                self.stats['query_explain'] = []
                for query in self.stats.get('top_impact_queries',[]):
                    query_id = query.get('query_id')
                    if query_id is not None and query.get('real_query_found', False) == True:
                        
                        try:
                            result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {query.get('query_text')}"))
                            self.stats['query_explain'].append({
                                "query_id": query_id,
                                "plan": [dict(row) for row in result.mappings()]
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"postgres | EXPLAIN | query_id={query_id} | {e}")

                try:
                    self.normalize_explain()
                except Exception as e:
                    logger.error(f"postgres | normalize_explain | {e}")

                try:
                    self.anonimize_query_text()
                except Exception as e:
                    logger.error(f"postgres | anonimize_query_text | {e}")

                try:
                    self.create_canonic_queries()
                except Exception as e:
                    logger.error(f"postgres | create_canonic_queries | {e}")

                try:
                    self.normalize_querytext_active()
                except Exception as e:
                    logger.error(f"postgres | normalize_querytext_active | {e}")


    def create_canonic_queries(self):
        canonicalizers.create_canonic_queries(self.stats, self.source_dialect)
                
  
    def normalize_engine_artifacts(self, stats):
        normalize.normalize_active_query_timestamps(stats)
        return stats