from collectors.base import DB_Engine_Collector
from .queries import INDEXES_QUERY, TABLES_QUERY, STATEMENTS_QUERY, LOCKS_QUERY, ACTIVE_QUERIES_QUERY, STATS_RESET_QUERY
from sqlalchemy import text

from logger import get_logger

logger = get_logger(__name__)

class Postgres_Collector(DB_Engine_Collector):
    def __init__(self, engine):
        self.stats = {}
        self.engine = engine
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
                    logger.error(f"For Postgres Error executing postgres-query for {key}: {e}")
                    print(f"for Postgres Error executing collect_telemetry for queries")


            if self.stats['statements'] is not None:
                
                try:
                    self.select_high_impact_time_statements()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function select_high_impact_statements: {e}")
                    print("for Postgres Error calling function select_high_impact_statements")

                try:
                    self.select_unstable_statements()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function select_unstable_statements: {e}")
                    print("for Postgres Error calling function select_unstable_statements")

                try:
                    self.select_io_heavy_statements()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function select_io_heavy_statements: {e}")
                    print("for Postgres Error calling function select_io_heavy_statements")

                try:
                    self.select_disk_spill_statements()
                except Exception as e:
                    logger.error(f"Error calling function select_disk_spill_statements: {e}")
                    print("for Posthres Error calling function select_disk_spill_statements")

                try:
                    self.select_candidates_to_explain()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function select_candidates_to_explain: {e}")
                    print("for Postgres Error calling function select_candidates_to_explain")

                try:
                    self.select_explain_ready()
                except Exception as e:
                    logger.error(f"For Postgres Error calling function select_explain_ready: {e}")
                    print("for Postgres Error calling function select_explain_ready")


                self.stats['query_explain'] = []
                for query in self.stats.get('explain_candidates',[]):
                    query_id = query.get('query_id')
                    if query_id is not None and query.get('real_query_found', False) == True:
                        try:
                            result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {query.get('query_text')}"))
                            self.stats['query_explain'].append({
                                "query_id": query_id,
                                "query_text": query.get('query_text'),
                                "plan": [dict(row) for row in result.mappings()]
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"for Postgres Error executing EXPLAIN for query_id {query_id}: {e}")
                            print(f"for Postgres Error executing EXPLAIN for query_id {query_id}")
                
                            