import json

from stages.normalize import _to_number as to_number

class PostgresExplainNormalizer:
    def normalize(self, stats):
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
        elif node_type in ("Aggregate", "Group"):
            canonical_plan["logical_shape"]["aggregates"] += 1
            operation = self._aggregate_operation(node)
        elif node_type in ("Sort", "Incremental Sort"):
            canonical_plan["logical_shape"]["sorts"] += 1
            operation = self._sort_operation(node)
        elif node_type in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            operation = self._base_operation("subquery")
        elif node_type in ("Unique",):
            canonical_plan["logical_shape"]["distinct"] += 1
            operation = self._base_operation("distinct")

        if is_subquery and node_type not in ("Subquery Scan", "SubPlan", "InitPlan"):
            canonical_plan["logical_shape"]["subqueries"] += 1
            if operation is None:
                operation = self._base_operation("subquery")

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
                self._walk_plan(child, canonical_plan)

    def _plan_estimates(self, node):
        estimates = {
            "total_cost": None,
        }
        if "Total Cost" in node:
            estimates["total_cost"] = to_number(node["Total Cost"])
        return estimates

    def _scan_operation(self, node):
        access_methods = {
            "Seq Scan": "full_table_scan",
            "Index Scan": "index_lookup",
            "Index Only Scan": "index_lookup",
            "Bitmap Heap Scan": "index_lookup",
            "Bitmap Index Scan": "index_lookup",
        }
        operation = self._base_operation("scan")
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
        operation = self._base_operation("join")
        return operation

    def _aggregate_operation(self, node):
        operation = self._base_operation("aggregate")
        self._copy_fields(operation, node, {
            "Filter": "predicate",
        })
        return operation

    def _sort_operation(self, node):
        operation = self._base_operation("sort")
        return operation

    @staticmethod
    def _base_operation(operation_type):
        return {
            "type": operation_type,
            "access_method": None,
            "relation": None,
            "estimated_rows": None,
            "predicate": None,
            "index_name": None,
        }

    @staticmethod
    def _copy_fields(operation, node, field_mapping):
        for source_field, canonical_field in field_mapping.items():
            if source_field in node:
                operation[canonical_field] = node[source_field]


class MysqlExplainNormalizer:
    def normalize(self, stats):
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

            plan = explain.get("plan") or {}
            self._set_root_cost(plan, canonical_plan)
            self._walk_plan(plan, canonical_plan)

            canonic_explains.append({
                "query_id": explain.get("query_id"),
                "canonical_plan": canonical_plan,
            })

        stats["canonic_explains"] = canonic_explains
        return stats

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
                operation = self._base_operation("join")
            elif key == "grouping_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["aggregates"] += 1
                operation = self._aggregate_operation(node)
            elif key == "ordering_operation" and isinstance(node, dict):
                canonical_plan["logical_shape"]["sorts"] += 1
                operation = self._sort_operation(node)
            elif key == "duplicates_removal":
                canonical_plan["logical_shape"]["distinct"] += 1
                operation = self._base_operation("distinct")
            elif key in ("subquery", "attached_subqueries", "dependent_subquery"):
                canonical_plan["logical_shape"]["subqueries"] += 1
                operation = self._base_operation("subquery")
            else:
                operation = None

            if operation is not None and operation.get("type") == "scan":
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

        operation = self._base_operation("scan")

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

    def _aggregate_operation(self, node):
        operation = self._base_operation("aggregate")
        return operation

    def _sort_operation(self, node):
        operation = self._base_operation("sort")
        return operation

    @staticmethod
    def _base_operation(operation_type):
        return {
            "type": operation_type,
            "access_method": None,
            "relation": None,
            "estimated_rows": None,
            "predicate": None,
            "index_name": None,
        }


EXPLAIN_NORMALIZERS = {
    "postgres": PostgresExplainNormalizer,
    "mysql": MysqlExplainNormalizer,
}