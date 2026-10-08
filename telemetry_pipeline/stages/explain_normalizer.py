import json

from models.stats import Stats
from stages.normalize import _to_number as to_number
def _flag(node, key):
    """MySQL reporta Estas caracteristicas como bool en el plan; el snapshot solo
    tiene `predicate: str | None`, asi que el nombre de la flag viaja ahi."""
    value = node.get(key)
    if value is None:
        return None
    if value is True:
        return key
    if value is False:
        return None
    return str(value)


class PostgresExplainNormalizer:
    def normalize(self, stats: Stats) -> Stats:
        canonic_explains = []

        for explain in stats.get("query_explain") or []:
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

            root_plan = self._root_plan(explain.get("plan"))
            if root_plan:
                canonical_plan["estimates"] = self._plan_estimates(root_plan)
                self._walk_plan(root_plan, canonical_plan)

            canonic_explains.append({
                "query_id": explain.get("query_id"),
                "explain_source": explain.get("explain_source"),
                "canonical_plan": canonical_plan,
            })

        stats["canonic_explains"] = canonic_explains
        return stats

    def _root_plan(self, plan):
        value = plan
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None

        if isinstance(value, list):
            if not value:
                return None
            return self._root_plan(value[0])

        if not isinstance(value, dict):
            return None

        if "Plan" in value:
            return self._root_plan(value["Plan"])
        if "QUERY PLAN" in value:
            return self._root_plan(value["QUERY PLAN"])
        if "Node Type" in value:
            return value
        return None

    def _walk_plan(self, node, canonical_plan):
        node_type = node.get("Node Type")
        operation = None
        operations = []
        is_subquery = (
            node.get("Parent Relationship") in ("SubPlan", "InitPlan")
            or "Subplan Name" in node
        )

        if node_type in ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"):
            canonical_plan["logical_shape"]["scans"] += 1
            operation = self._scan_operation(node)
        elif node_type in ("Nested Loop", "Hash Join", "Merge Join"):
            canonical_plan["logical_shape"]["joins"] += 1
            operation = self._join_operation(node)
        elif node_type in ("Aggregate", "Group", "GroupAggregate"):
            canonical_plan["logical_shape"]["aggregates"] += 1
            operation = self._aggregate_operation(node)
        elif node_type in ("Sort", "Incremental Sort"):
            canonical_plan["logical_shape"]["sorts"] += 1
            operation = self._sort_operation(node)
        elif node_type in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            operation = self._subquery_operation(node)
        elif node_type in ("Unique",):
            canonical_plan["logical_shape"]["distinct"] += 1
            operation = self._distinct_operation(node)

        if is_subquery and node_type not in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            if operation is None:
                operation = self._subquery_operation(node)
            else:
                operations.append(operation)
                operation = self._subquery_operation(node)

        if operation is not None:
            if operation["type"] == "join":
                operation["predicate"] = (
                    node.get("Join Filter")
                    or node.get("Hash Cond")
                    or node.get("Merge Cond")
                )
            operations.append(operation)

        canonical_plan["physical_operations"].extend(operations)

        for child in node.get("Plans", []):
            if isinstance(child, dict):
                self._walk_plan(child, canonical_plan)

    def _plan_estimates(self, node):
        estimates = {
            "total_cost": None,
            "output_rows": None,
        }
        if "Total Cost" in node:
            estimates["total_cost"] = to_number(node["Total Cost"])
        if "Plan Rows" in node:
            estimates["output_rows"] = to_number(node["Plan Rows"])
        return estimates

    def _scan_operation(self, node):
        access_methods = {
            "Seq Scan": "full_table_scan",
            "Index Scan": "index_lookup",
            "Index Only Scan": "index_lookup",
            "Bitmap Heap Scan": "index_lookup",
            "Bitmap Index Scan": "index_lookup",
        }
        operation = _base_operation("scan")
        operation["access_method"] = access_methods[node["Node Type"]]
        operation["relation"] = node.get("Alias") or node.get("Relation Name")
        field_mapping = {
            "Plan Rows": "estimated_rows",
            "Filter": "predicate",
            "Index Cond": "predicate",
            "Index Name": "index_name",
        }
        self._copy_fields(operation, node, field_mapping)
        return operation

    def _join_operation(self, node):
        operation = _base_operation("join")
        return operation

    def _aggregate_operation(self, node):
        operation = _base_operation("aggregate")
        self._copy_fields(operation, node, {
            "Filter": "predicate",
            "Plan Rows": "estimated_rows",
        })
        return operation

    def _sort_operation(self, node):
        operation = _base_operation("sort")
        self._copy_fields(operation, node, {
            "Plan Rows": "estimated_rows",
        })
        return operation

    def _subquery_operation(self, node):
        operation = _base_operation("subquery")
        self._copy_fields(operation, node, {
            "Filter": "predicate",
            "Plan Rows": "estimated_rows",
        })
        return operation

    def _distinct_operation(self, node):
        operation = _base_operation("distinct")
        self._copy_fields(operation, node, {
            "Plan Rows": "estimated_rows",
        })
        return operation

    @staticmethod
    def _copy_fields(operation, node, field_mapping):
        for source_field, canonical_field in field_mapping.items():
            if source_field in node:
                operation[canonical_field] = node[source_field]

def _base_operation(operation_type):
    return {
        "type": operation_type,
        "access_method": None,
        "relation": None,
        "estimated_rows": None,
        "predicate": None,
        "index_name": None,
    }

class MysqlExplainNormalizer:

    WRAPPERS = (
        "ordering_operation",
        "grouping_operation",
        "duplicates_removal",
        "windowing",
        "buffer_result",
    )

    def normalize(self, stats: Stats) -> Stats:
        canonic_explains = []

        for explain in stats.get("query_explain") or []:
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
                    "output_rows": None,
                },
            }

            plan = explain.get("plan") or {}
            self._set_root_cost(plan, canonical_plan)
            canonical_plan["estimates"]["output_rows"] = self._get_output_rows(plan)
            self._walk_plan(plan, canonical_plan)

            canonic_explains.append({
                "query_id": explain.get("query_id"),
                "explain_source": explain.get("explain_source"),
                "canonical_plan": canonical_plan,
            })

        stats["canonic_explains"] = canonic_explains
        return stats

    def _get_output_rows(self, plan):
        query_block = plan.get("query_block") if isinstance(plan, dict) else None
        if not isinstance(query_block, dict):
            return None

        def find_last_table(node):
            if not isinstance(node, dict):
                return None

            if "nested_loop" in node:
                nested_loop = node["nested_loop"]
                if isinstance(nested_loop, list) and nested_loop:
                    last_elem = nested_loop[-1]
                    if isinstance(last_elem, dict) and "table" in last_elem:
                        table = last_elem["table"]
                        if isinstance(table, dict):
                            return to_number(table.get("rows_produced_per_join"))
            if "table" in node:
                table = node["table"]
                if isinstance(table, dict):
                    return to_number(table.get("rows_produced_per_join"))

            for w in self.WRAPPERS:
                if w in node:
                    return find_last_table(node[w])

            return None

        return find_last_table(query_block)

    def _set_root_cost(self, plan, canonical_plan):
        query_block = plan.get("query_block") if isinstance(plan, dict) else None
        cost_info = query_block.get("cost_info") if isinstance(query_block, dict) else None
        query_cost = cost_info.get("query_cost") if isinstance(cost_info, dict) else None
        if query_cost is not None:
            canonical_plan["estimates"]["total_cost"] = to_number(query_cost)

    def _walk_plan(self, value, canonical_plan):
        if isinstance(value, list):
            for item in value:
                self._walk_plan(item, canonical_plan)
            return

        if not isinstance(value, dict):
            return

        for key, node in value.items():
            if key == "table" and isinstance(node, dict):
                canonical_plan["logical_shape"]["scans"] += 1
                operation = self._scan_operation(node)
            elif key == "nested_loop":
                canonical_plan["logical_shape"]["joins"] += 1
                operation = self._join_operation(node)
            elif key == "grouping_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["aggregates"] += 1
                operation = self._aggregate_operation(node)
            elif key == "ordering_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["sorts"] += 1
                operation = self._sort_operation(node)
            elif key == "duplicates_removal":
                canonical_plan["logical_shape"]["distinct"] += 1
                operation = self._distinct_operation(node)
            elif key in ("subquery", "attached_subqueries", "dependent_subquery"):
                canonical_plan["logical_shape"]["subqueries"] += 1
                operation = self._subquery_operation(node)
            else:
                operation = None

            if operation is not None:
                canonical_plan["physical_operations"].append(operation)

            if isinstance(node, (dict, list)):
                self._walk_plan(node, canonical_plan)

    def _scan_operation(self, node):
        access_type = node.get("access_type")
        access_methods = {
            "ALL": "full_table_scan",
            "index": "index_lookup",
            "range": "index_lookup",
            "const": "index_lookup",
            "eq_ref": "index_lookup",
            "ref": "index_lookup",
        }

        operation = _base_operation("scan")

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

    def _join_operation(self, node):
        operation = _base_operation("join")
        if isinstance(node, dict):
            operation["predicate"] = _flag(node, "join_condition")
        return operation

    def _aggregate_operation(self, node):
        operation = _base_operation("aggregate")
        if isinstance(node, dict):
            operation["predicate"] = _flag(node, "using_temporary_table")
        return operation

    def _sort_operation(self, node):
        operation = _base_operation("sort")
        if isinstance(node, dict):
            operation["predicate"] = _flag(node, "using_filesort")
        return operation

    def _subquery_operation(self, node):
        return _base_operation("subquery")

    def _distinct_operation(self, node):
        return _base_operation("distinct")


EXPLAIN_NORMALIZERS = {
    "postgres": PostgresExplainNormalizer,
    "mysql": MysqlExplainNormalizer,
}