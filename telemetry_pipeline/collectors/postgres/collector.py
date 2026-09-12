from collectors.base import DB_Engine_Collector
from .queries import INDEXES_QUERY, TABLES_QUERY, STATEMENTS_QUERY, LOCKS_QUERY, ACTIVE_QUERIES_QUERY, STATS_RESET_QUERY
import json
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
                    self.select_disk_spill_indicator()
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
                                "plan": [dict(row) for row in result.mappings()]
                            })
                        except Exception as e:
                            conn.rollback()
                            logger.error(f"for Postgres Error executing EXPLAIN for query_id {query_id}: {e}")
                            print(f"for Postgres Error executing EXPLAIN for query_id {query_id}")

                try:
                    self.normalize_explain()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function normalize_explain: {e}")
                    print("for Postgres Error calling function normalize_explain")

                try:
                    self.anonimize_query_text()
                except Exception as e:
                    logger.error("for Postgres Error calling function anonimize_query_text: {e}")
                    print("for Postgres Error calling function anonimize_query_text")

                try:
                    self.create_canonic_queries()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function create_canonic_query: {e}")
                    print("for Postgres Error calling function create_canonic_query")

                try:
                    self.eliminate_querytext_active()
                except Exception as e:
                    logger.error(f"for Postgres Error calling function eliminate_querytext_active: {e}")
                    print("for Postgres Error calling function eliminate_querytext_active")


    def create_canonic_queries(self):
        if self.stats.get("explain_candidates"):
            for stmdt in self.stats["explain_candidates"]:
                    stmdt["canonic_query"] = " ".join(stmdt.get("query_text").split())
                

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

            root_plan = self._postgres_root_plan(explain.get("plan"))
            if root_plan:
                canonical_plan["estimates"] = self._postgres_plan_estimates(root_plan)
                self._walk_postgres_plan(root_plan, canonical_plan)

            canonic_explains.append({
                "query_id": explain.get("query_id"),
                "canonical_plan": canonical_plan,
            })

        self.stats["canonic_explains"] = canonic_explains
        return canonic_explains

    def _postgres_root_plan(self, plan):
        value = plan
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None

        if isinstance(value, list):
            if not value:
                return None
            return self._postgres_root_plan(value[0])

        if not isinstance(value, dict):
            return None

        if "Plan" in value:
            return self._postgres_root_plan(value["Plan"])
        if "QUERY PLAN" in value:
            return self._postgres_root_plan(value["QUERY PLAN"])
        if "Node Type" in value:
            return value
        return None

    def _walk_postgres_plan(self, node, canonical_plan):
        node_type = node.get("Node Type")
        operation = None
        is_subquery = (
            node.get("Parent Relationship") in ("SubPlan", "InitPlan")
            or "Subplan Name" in node
        )

        if node_type in ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"):
            canonical_plan["logical_shape"]["scans"] += 1
            operation = self._postgres_scan_operation(node)
        elif node_type in ("Nested Loop", "Hash Join", "Merge Join"):
            canonical_plan["logical_shape"]["joins"] += 1
            operation = self._postgres_join_operation(node)
        elif node_type in ("Aggregate", "Group"):
            canonical_plan["logical_shape"]["aggregates"] += 1
            operation = self._postgres_aggregate_operation(node)
        elif node_type in ("Sort", "Incremental Sort"):
            canonical_plan["logical_shape"]["sorts"] += 1
            operation = self._postgres_sort_operation(node)
        elif node_type in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            operation = self._postgres_base_operation("subquery")
        elif node_type in ("Unique",):
            canonical_plan["logical_shape"]["distinct"] += 1
            operation = self._postgres_base_operation("distinct")

        if is_subquery and node_type not in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            if operation is None:
                operation = self._postgres_base_operation("subquery")

        if operation is not None and operation.get("type") == "scan":
            canonical_plan["physical_operations"].append(operation)
        elif operation is not None and operation.get("type") == "join":
            join_predicate = (
                node.get("Join Filter")
                or node.get("Hash Cond")
                or node.get("Merge Cond")
            )
            if join_predicate is not None:
                operation["predicate"] = join_predicate
                canonical_plan["physical_operations"].append(operation)

        for child in node.get("Plans", []):
            if isinstance(child, dict):
                self._walk_postgres_plan(child, canonical_plan)

    def _postgres_plan_estimates(self, node):
        estimates = {
            "total_cost": None,
        }
        if "Total Cost" in node:
            estimates["total_cost"] = self._to_number(node["Total Cost"])
        return estimates

    def _postgres_scan_operation(self, node):
        access_methods = {
            "Seq Scan": "full_table_scan",
            "Index Scan": "index_lookup",
            "Index Only Scan": "index_lookup",
            "Bitmap Heap Scan": "index_lookup",
            "Bitmap Index Scan": "index_lookup",
        }
        operation = self._postgres_base_operation("scan")
        operation["access_method"] = access_methods[node["Node Type"]]
        operation["relation"] = node.get("Alias") or node.get("Relation Name")
        field_mapping = {
            "Plan Rows": "estimated_rows",
            "Filter": "predicate",
            "Index Cond": "predicate",
            "Index Name": "index_name",
        }
        self._copy_postgres_fields(operation, node, field_mapping)
        return operation

    def _postgres_join_operation(self, node):
        operation = self._postgres_base_operation("join")
        return operation

    def _postgres_aggregate_operation(self, node):
        operation = self._postgres_base_operation("aggregate")
        self._copy_postgres_fields(operation, node, {
            "Filter": "predicate",
        })
        return operation

    def _postgres_sort_operation(self, node):
        operation = self._postgres_base_operation("sort")
        return operation

    @staticmethod
    def _postgres_base_operation(operation_type):
        return {
            "type": operation_type,
            "access_method": None,
            "relation": None,
            "estimated_rows": None,
            "predicate": None,
            "index_name": None,
        }

    @staticmethod
    def _copy_postgres_fields(operation, node, field_mapping):
        for source_field, canonical_field in field_mapping.items():
            if source_field in node:
                operation[canonical_field] = node[source_field]

    @staticmethod
    def _to_number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    def get_canonic_explains(self):
        with open("canonic_explains_postgres.json", "w") as file:
            json.dump(self.stats.get("canonic_explains", []), file, indent=4)

    def get_explain(self):
        with open("explain_postgres.json", "w") as file:
            json.dump(self.stats.get("query_explain", []), file, indent=4)

    def get_statements(self):
        with open("statements_postgres.json", "w") as file:
            json.dump(self.stats.get("statements", []), file, indent=4)

    def get_candidates(self):
        with open("candidates_postgres.json", "w") as file:
            json.dump(self.stats.get("explain_candidates", []), file, indent=4)

    def get_locks(self):
        with open("locks_postgres.json", "w") as file:
            json.dump(self.stats.get("locks",[]), file, indent=4)

    def get_active_queries(self):
        with open("active_queries_postgres.json", "w") as file:
            json.dump(self.stats.get("active_queries", []), file, indent=4, default=str)
          
                
                            