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
                                "plan": plan
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"for mysql Error executing EXPLAIN for query_id {query_id}: {e}")
                            print(f"for mysql Error executing EXPLAIN for query_id {query_id}")

                try:
                    self.normalize_explain()
                except Exception as e:
                    logger.error(f"for mysql Error calling function normalize_explain: {e}")
                    print("for mysql Error calling function normalize_explain")

                try:
                    self.anonimize_query_text()
                except Exception as e:
                    logger.error(f"for mysql Error calling function anonimize_query_text: {e}")
                    print("for mysql Error calling function anonimize_query_text")

                try:
                    self.create_canonic_queries()
                except Exception as e:
                    logger.error(f"for mysql Error calling function create_canonic_query: {e}")
                    print("for mysql Error calling function create_ast_query")

                try:
                    self.normalize_predicate()
                except Exception as e:
                    logger.error(f"for mysql Error calling function normalize_predicate: {e}")
                    print("for mysql Error calling function normalize_predicate")

                try:
                    self.normalize_locks()
                except Exception as e:
                    logger.error(f"for mysql Error calling function normalize_locks: {e}")
                    print("for mysql Error calling function normalize_locks")

                try:
                    self.normalize_querytext_active()
                except Exception as e:
                    logger.error(f"for mysql Error calling function normalize_querytext_active: {e}")
                    print("for mysql Error calling function normalize_querytext_active")

                try:
                    self.normalize_blocking_pids()
                except Exception as e:
                    logger.error(f"for mysql Error calling function normalize_blocking_pids: {e}")
                    print("for mysql Error calling function normalize_blocking_pids")



    def normalize_blocking_pids(self):
        for query in self.stats.get("active_queries", []):
            blocking_pids = query.get("blocking_pids")
            if blocking_pids is not None:
                query["blocking_pids"] = [int(pid) for pid in blocking_pids.split(",") if pid.strip().isdigit()]
            else:
                query["blocking_pids"] = []

    def normalize_locks(self):
        for lock in self.stats.get("locks", []):
            is_granted = lock.get("is_granted")
            if is_granted == "GRANTED":
                lock["is_granted"] = True
            elif is_granted == "WAITING":
                lock["is_granted"] = False

    def normalize_predicate(self):
        for explain in self.stats.get("canonic_explains") or []:
            for operation in explain.get("canonical_plan", []).get("physical_operations", []):
                predicate = operation.get("predicate")
                if predicate:
                    cleaned_predicate = self.clean_mysql_explain_predicate_dynamic(predicate)
                    operation["predicate"] = cleaned_predicate


    def clean_mysql_explain_predicate_dynamic(self,pred_text):
        import re
        if not pred_text:
            return None
        
        # 1. Quitar comentarios del optimizador estilo /* select#2 */
        cleaned = re.sub(r'/\*.*?\*/', '', pred_text)
        
        # 2. Quitar backticks de MySQL
        cleaned = cleaned.replace('`', '')
        
        # 3. Detectar dinámicamente el esquema (captura el primer identificador antes del primer punto, ej: "ql_demo")
        # Busca patrones como `esquema`.`tabla` o esquema.tabla y extrae el esquema
        match = re.search(r'\b([a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\.', cleaned)
        if match:
            dynamic_schema = match.group(1)
            # Eliminamos dinámicamente solo ese esquema detectado seguido de un punto
            cleaned = re.sub(rf'\b{dynamic_schema}\.', '', cleaned)
        
        # 4. Normalizar espacios múltiples
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        return cleaned


    def create_canonic_queries(self):
        if self.stats.get('explain_candidates'):
            import sqlglot

            for stmd in self.stats['explain_candidates']:
                query_text = stmd.get('query_text')
                query_clean = self.clean_mysql_sintax(query_text) if query_text else None
                canonic_query = self.transform_to_canonical(sqlglot.parse_one(query_clean, read='mysql')) if query_clean else None
                stmd['canonic_query'] = canonic_query
                   

    def clean_mysql_sintax(self,query):
        # Remove backticks from the query
        cleaned_query = query.replace('DISTINCTROW', 'DISTINCT')
        return cleaned_query


    def transform_to_canonical(self, ast):
        import re
        
        # 1. Generamos el SQL en postgres (sqlglot convierte el '?' de mysql en '%s')
        query_sql = ast.sql(dialect="postgres", identify=False)
        
        # 2. Reemplazamos cada '%s' de forma secuencial por $1, $2, $3...
        param_index = 1
        def replace_placeholder(match):
            nonlocal param_index
            current_param = f"${param_index}"
            param_index += 1
            return current_param

        query_with_dollars = re.sub(r'%s', replace_placeholder, query_sql)
        
        # 3. Limpiamos las comillas dobles
        final_query = query_with_dollars.replace('"', '')
        
        return final_query
    
    def calculate_stddev_coeff(self):
        import math

        for stmt in self.stats.get("statements", []):
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


    def normalize_explain(self):
        canonic_explains = []

        for explain in self.stats.get("query_explain") or []:
            canonical_plan = {
                "logical_shape": {
                    "scans": 0,
                    "joins": 0,
                    "aggregates": 0,
                    "sorts": 0,
                    "subqueries": 0,
                    "distinct": 0,
                },
                "physical_operations": [],
                "estimates": {
                    "total_cost": None,
                },
            }

            plan = explain.get("plan") or {}
            self._set_mysql_root_cost(plan, canonical_plan)
            self._walk_mysql_plan(plan, canonical_plan)

            canonic_explains.append({
                "query_id": explain.get("query_id"),
                "canonical_plan": canonical_plan,
            })

        self.stats["canonic_explains"] = canonic_explains

    def _set_mysql_root_cost(self, plan, canonical_plan):
        query_block = plan.get("query_block") if isinstance(plan, dict) else None
        cost_info = query_block.get("cost_info") if isinstance(query_block, dict) else None
        query_cost = cost_info.get("query_cost") if isinstance(cost_info, dict) else None
        if query_cost is not None:
            canonical_plan["estimates"]["total_cost"] = self._to_number(query_cost)

    def _walk_mysql_plan(self, value, canonical_plan):
        if isinstance(value, list):
            for item in value:
                self._walk_mysql_plan(item, canonical_plan)
            return

        if not isinstance(value, dict):
            return

        for key, node in value.items():
            if key == "table" and isinstance(node, dict):
                canonical_plan["logical_shape"]["scans"] += 1
                operation = self._mysql_scan_operation(node)
            elif key == "nested_loop":
                canonical_plan["logical_shape"]["joins"] += 1
                operation = self._mysql_base_operation("join")
            elif key == "grouping_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["aggregates"] += 1
                operation = self._mysql_aggregate_operation(node)
            elif key == "ordering_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["sorts"] += 1
                operation = self._mysql_sort_operation(node)
            elif key == "duplicates_removal":
                canonical_plan["logical_shape"]["distinct"] += 1
                operation = self._mysql_base_operation("distinct")
            elif key in ("subquery", "attached_subqueries", "dependent_subquery"):
                canonical_plan["logical_shape"]["subqueries"] += 1
                operation = self._mysql_base_operation("subquery")
            else:
                operation = None

            if operation is not None and operation.get("type") == "scan":
                canonical_plan["physical_operations"].append(operation)

            if isinstance(node, (dict, list)):
                self._walk_mysql_plan(node, canonical_plan)

    def _mysql_scan_operation(self, node):
        access_type = node.get("access_type")
        access_methods = {
            "ALL": "full_table_scan",
            "index": "index_lookup",
            "range": "index_lookup",
            "const": "index_lookup",
            "eq_ref": "index_lookup",
            "ref": "index_lookup",
        }

        operation = self._mysql_base_operation("scan")

        if "access_type" in node:
            operation["access_method"] = access_methods.get(access_type)

        field_mapping = {
            "table_name": "relation",
            "rows_produced_per_join": "estimated_rows",
            "attached_condition": "predicate",
            "key": "index_name",
        }

        for source_field, canonical_field in field_mapping.items():
            if source_field in node:
                operation[canonical_field] = node[source_field]

        return operation

    def _mysql_aggregate_operation(self, node):
        operation = self._mysql_base_operation("aggregate")
        return operation

    def _mysql_sort_operation(self, node):
        operation = self._mysql_base_operation("sort")
        return operation

    @staticmethod
    def _mysql_base_operation(operation_type):
        return {
            "type": operation_type,
            "access_method": None,
            "relation": None,
            "estimated_rows": None,
            "predicate": None,
            "index_name": None,
        }

    @staticmethod
    def _to_number(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    def get_canonic_explains(self):
        with open("canonic_explains_mysql.json", "w") as f:
            json.dump(self.stats.get("canonic_explains", []), f, indent=4)

    def get_explain(self):
        with open("explain_mysql.json", "w") as f:
            json.dump(self.stats.get("query_explain", []), f, indent=4)

    def get_statements(self):
        with open("statements_mysql.json", "w") as f:
            json.dump(self.stats.get("statements", []), f, indent=4)   

    def get_candidates(self):
        with open("candidates_mysql.json", "w") as f:
            json.dump(self.stats.get("explain_candidates", []), f, indent=4) 

    def get_locks(self):
            with open("locks_mysql.json", "w") as file:
                json.dump(self.stats.get("locks",[]), file, indent=4)

    def get_active_queries(self):
            with open("active_queries_mysql.json", "w") as file:
                json.dump(self.stats.get("active_queries", []), file, indent=4, default=str)