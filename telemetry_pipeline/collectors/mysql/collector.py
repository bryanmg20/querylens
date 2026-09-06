from collectors.base import DB_Engine_Collector
from .queries import INDEXES_QUERY, TABLES_QUERY, STATEMENTS_QUERY, LOCKS_QUERY, ACTIVE_QUERIES_QUERY, STATS_RESET_QUERY
import json
from sqlalchemy import text

from logger import get_logger

logger = get_logger(__name__)

class Mysql_Collector(DB_Engine_Collector):
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
                    logger.error(f"For mysql Error executing query for {key}: {e}")
                    print(f"for mysql Error executing collect_telemetry for queries")


            if self.stats['statements'] is not None:

                try:
                    self.calculate_stddev_coeff()

                except Exception as e:
                    logger.error(f"For mysql Error executing calculate_sttdev_coeff: {e}")
                    print("for mysql Error executing calculate_sttdev_coeff")
                
                try:
                    self.select_high_impact_time_statements()
                except Exception as e:
                    logger.error(f"for mysql Error calling function select_high_impact_statements: {e}")
                    print("for mysql Error calling function select_high_impact_statements")

                try:
                    self.select_unstable_statements()
                except Exception as e:
                    logger.error(f"for mysql Error calling function select_unstable_statements: {e}")
                    print("for mysql Error calling function select_unstable_statements")

                #try:
                #   self.select_io_heavy_statements()
                #except Exception as e:
                #    logger.error(f"for Postgres Error calling function select_io_heavy_statements: {e}")
                #    print("for mysql Error calling function select_io_heavy_statements")

                try:
                    self.select_disk_spill_indicator()
                except Exception as e:
                    logger.error(f"for mysql Error calling function select_disk_spill_statements: {e}")
                    print("for mysql Error calling function select_disk_spill_statements")

                try:
                    self.select_candidates_to_explain()
                except Exception as e:
                    logger.error(f"for mysql Error calling function select_candidates_to_explain: {e}")
                    print("for mysql Error calling function select_candidates_to_explain")

                try:
                    self.select_explain_ready()
                except Exception as e:
                    logger.error(f"For mysql Error calling function select_explain_ready: {e}")
                    print("for mysql Error calling function select_explain_ready")


                self.stats['query_explain'] = []
                for query in self.stats.get('explain_candidates',[]):
                    query_id = query.get('query_id')
                    if query_id is not None and query.get('real_query_found', False) == True:
                        try:
                            result = conn.execute(text(f"EXPLAIN FORMAT=JSON {query.get('query_text')}"))
                            plan_row = next(result.mappings(), None)
                            plan = json.loads(plan_row["EXPLAIN"])
                            self.stats['query_explain'].append({
                                "query_id": query_id,
                                "query_text": query.get('query_text'),
                                "plan": plan
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"for mysql Error executing EXPLAIN for query_id {query_id}: {e}")
                            print(f"for mysql Error executing EXPLAIN for query_id {query_id}")



    def calculate_stddev_coeff(self):
        import math

        for stmt in self.stats.get("statements", []):
            mean = float(stmt.get('mean_time_ms') or 0)
            total = float(stmt.get('total_time_ms') or 0)
            count = int(stmt.get('execution_count') or 0)
            max_time = float(stmt.get('max_time_ms') or 0)

            if mean > 0 and max_time > mean * 1000:
                stmt['stddev_time_ms'] = None
                stmt['coeff_of_variation'] = None
                continue

            if count > 1 and mean > 0:
                variance = (total * total / count) - (mean * mean)
                stddev = math.sqrt(max(variance, 0))
            else:
                stddev = None

            if stddev is not None and mean > 0:
                coeff_of_variation = stddev / mean
            else:
                coeff_of_variation = None

            if stddev is not None and stddev > max_time:
                stmt['stddev_time_ms'] = None
                stmt['coeff_of_variation'] = None
                continue

            stmt['stddev_time_ms'] = stddev
            stmt['coeff_of_variation'] = coeff_of_variation
                                