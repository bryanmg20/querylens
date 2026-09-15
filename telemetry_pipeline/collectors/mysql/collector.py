from collectors.base import DB_Engine_Collector
from .queries import INDEXES_QUERY, TABLES_QUERY, STATEMENTS_QUERY, LOCKS_QUERY, ACTIVE_QUERIES_QUERY, STATS_RESET_QUERY
import json
import stages.normalize as normalize
from stages import canonicalizers
from sqlalchemy import text

from logger import get_logger

logger = get_logger(__name__)

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
                    logger.error(f"mysql | collect_telemetry | query={key} | {e}")


            if self.stats['statements'] is not None:

                try:
                    self.calculate_stddev_coeff()

                except Exception as e:
                    logger.error(f"mysql | calculate_stddev_coeff | {e}")
                
                try:
                    self.select_high_impact_time_statements()
                except Exception as e:
                    logger.error(f"mysql | select_high_impact_time_statements | {e}")

                try:
                    self.select_unstable_statements()
                except Exception as e:
                    logger.error(f"mysql | select_unstable_statements | {e}")

                #try:
                #   self.select_io_heavy_statements()
                #except Exception as e:
                #    logger.error(f"for Postgres Error calling function select_io_heavy_statements: {e}")
                #    print("for mysql Error calling function select_io_heavy_statements")

                try:
                    self.select_disk_spill_indicator()
                except Exception as e:
                    logger.error(f"mysql | select_disk_spill_indicator | {e}")

                try:
                    self.select_candidates_to_explain()
                except Exception as e:
                    logger.error(f"mysql | select_candidates_to_explain | {e}")

                try:
                    self.select_explain_ready()
                except Exception as e:
                    logger.error(f"mysql | select_explain_ready | {e}")


                self.stats['query_explain'] = []
                for query in self.stats.get('explain_candidates',[]):
                    query_id = query.get('query_id')
                    if query_id is not None and query.get('real_query_found', False) == True:
                        try:
                            result = conn.execute(text(f"EXPLAIN FORMAT=JSON {query.get('query_text')}"))
                            plan_row = next(result.mappings(), None)
                            if plan_row is None:
                                logger.error(f"mysql | EXPLAIN | query_id={query_id} | returned no plan row")
                                continue
                            plan = json.loads(plan_row["EXPLAIN"])
                            self.stats['query_explain'].append({
                                "query_id": query_id,
                                "plan": plan
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"mysql | EXPLAIN | query_id={query_id} | {e}")

                try:
                    self.normalize_explain()
                except Exception as e:
                    logger.error(f"mysql | normalize_explain | {e}")

                try:
                    self.anonimize_query_text()
                except Exception as e:
                    logger.error(f"mysql | anonimize_query_text | {e}")

                try:
                    self.create_canonic_queries()
                except Exception as e:
                    logger.error(f"mysql | create_canonic_queries | {e}")

                try:
                    self.normalize_predicate()
                except Exception as e:
                    logger.error(f"mysql | normalize_predicate | {e}")

                try:
                    self.normalize_locks()
                except Exception as e:
                    logger.error(f"mysql | normalize_locks | {e}")

                try:
                    self.normalize_querytext_active()
                except Exception as e:
                    logger.error(f"mysql | normalize_querytext_active | {e}")

                try:
                    self.normalize_blocking_pids()
                except Exception as e:
                    logger.error(f"mysql | normalize_blocking_pids | {e}")




    def create_canonic_queries(self):
        canonicalizers.create_canonic_queries(self.stats, self.source_dialect, clean_mysql=True)

    def clean_mysql_sintax(self, query):
        return canonicalizers.clean_mysql_sintax(query)

  
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

    def normalize_engine_artifacts(self, stats):
        normalize.normalize_locks(stats)
        normalize.normalize_blocking_pids(stats)
        normalize.normalize_predicate(stats)
