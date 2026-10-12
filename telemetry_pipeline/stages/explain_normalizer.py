import json

from models.stats import Stats
from stages.normalize import _to_number as to_number
from stages.normalize import redact_postgres_plan_predicate
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

            # El plan generico ya trae $n; el de auto_explain trae los valores
            # de la ejecucion y la cola no los puede conservar.
            if explain.get("explain_source") == "auto_explain":
                for operation in canonical_plan["physical_operations"]:
                    operation["predicate"] = redact_postgres_plan_predicate(
                        operation["predicate"]
                    )

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

    def _walk_plan(self, node, canonical_plan, bitmap_heap=None):
        """`bitmap_heap` es la operacion del Bitmap Heap Scan de arriba mientras
        se recorren sus Bitmap Index Scan (directos o bajo BitmapAnd/BitmapOr):
        esos nodos nombran el indice pero no la tabla."""
        node_type = node.get("Node Type")
        operation = None
        operations = []
        is_subquery = (
            node.get("Parent Relationship") in ("SubPlan", "InitPlan")
            or "Subplan Name" in node
        )

        if node_type in ("Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"):
            canonical_plan["logical_shape"]["scans"] += 1
            operation = self._scan_operation(node, bitmap_heap)
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

        if node_type == "Bitmap Heap Scan":
            child_heap = operation if operation["type"] == "scan" else operations[0]
            self._set_bitmap_index_name(node, child_heap)
        elif node_type in ("BitmapAnd", "BitmapOr"):
            child_heap = bitmap_heap
        else:
            child_heap = None

        for child in node.get("Plans", []):
            if isinstance(child, dict):
                self._walk_plan(child, canonical_plan, child_heap)

    @staticmethod
    def _set_bitmap_index_name(node, heap_operation):
        """Con un solo Bitmap Index Scan el heap lleva su indice. Con BitmapAnd/
        BitmapOr son varios y index_name es uno solo: cada indice queda en su
        Bitmap Index Scan, que ya lo trae."""
        names = []
        pending = [child for child in node.get("Plans", []) if isinstance(child, dict)]
        while pending:
            child = pending.pop()
            if child.get("Node Type") == "Bitmap Index Scan":
                if child.get("Index Name"):
                    names.append(child["Index Name"])
            elif child.get("Node Type") in ("BitmapAnd", "BitmapOr"):
                pending.extend(c for c in child.get("Plans", []) if isinstance(c, dict))
        if len(names) == 1:
            heap_operation["index_name"] = names[0]

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

    def _scan_operation(self, node, bitmap_heap=None):
        access_methods = {
            "Seq Scan": "full_table_scan",
            "Index Scan": "index_lookup",
            "Index Only Scan": "index_lookup",
            "Bitmap Heap Scan": "index_lookup",
            "Bitmap Index Scan": "index_lookup",
        }
        node_type = node["Node Type"]
        operation = _base_operation("scan")
        operation["access_method"] = access_methods[node_type]
        # Index Scan sin Index Cond no busca: recorre el indice entero (para dar
        # el orden de un ORDER BY, por ejemplo), igual que `index` en MySQL.
        if node_type in ("Index Scan", "Index Only Scan") and not node.get("Index Cond"):
            operation["access_method"] = "full_index_scan"
        # relation es el alias, con el que el predicado califica las columnas;
        # table_name es la tabla real (Postgres renombra a tabla_1 la segunda
        # lectura sin alias de una misma tabla).
        operation["relation"] = node.get("Alias") or node.get("Relation Name")
        operation["table_name"] = node.get("Relation Name")
        if node_type == "Bitmap Index Scan":
            # El nodo nombra el indice, no la tabla, y su Index Cond es la
            # misma condicion que el Recheck Cond del heap, que la lleva.
            if bitmap_heap is not None:
                operation["relation"] = bitmap_heap["relation"]
                operation["table_name"] = bitmap_heap["table_name"]
        else:
            # Index Cond (o Recheck Cond) y Filter juntos: con uno solo se
            # perdia la parte que se revisa fila por fila.
            operation["predicate"] = _and_conditions(
                node.get("Index Cond") or node.get("Recheck Cond"),
                node.get("Filter"),
            )
        self._copy_fields(operation, node, {
            "Plan Rows": "estimated_rows",
            "Index Name": "index_name",
        })
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
        "table_name": None,
        "estimated_rows": None,
        "predicate": None,
        "index_name": None,
    }


def _enclosed(condition):
    """True si el primer parentesis cierra al final: `(a) OR (b)` no lo esta."""
    if not (condition.startswith("(") and condition.endswith(")")):
        return False
    depth = 0
    quoted = False
    for position, char in enumerate(condition):
        # Un literal de auto_explain (todavia sin redactar) puede traer
        # parentesis: dentro de comillas no cuentan.
        if char == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position == len(condition) - 1
    return False


def _and_conditions(*conditions):
    """Une con AND las condiciones presentes de un scan (la del indice primero).
    Cada parte va entre parentesis para que un OR no cambie de alcance."""
    parts = [condition.strip() for condition in conditions if condition and condition.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return " AND ".join(part if _enclosed(part) else f"({part})" for part in parts)

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
        # `index` no busca con el indice: lo recorre entero (para cubrir
        # columnas o dar el orden), igual que un Index Scan sin Index Cond.
        access_methods = {
            "ALL": "full_table_scan",
            "index": "full_index_scan",
            "range": "index_lookup",
            "const": "index_lookup",
            "system": "index_lookup",
            "eq_ref": "index_lookup",
            "ref": "index_lookup",
            "ref_or_null": "index_lookup",
            "index_merge": "index_lookup",
            "fulltext": "index_lookup",
            "unique_subquery": "index_lookup",
            "index_subquery": "index_lookup",
        }

        operation = _base_operation("scan")

        if "access_type" in node:
            operation["access_method"] = access_methods.get(access_type)

        # table_name del JSON de MySQL es el alias; la tabla real la resuelve
        # el consumidor con los alias de la query, asi que table_name canonico
        # queda en None.
        field_mapping = {
            "table_name": "relation",
            "key": "index_name",
        }

        for source_field, canonical_field in field_mapping.items():
            if source_field in node:
                operation[canonical_field] = node[source_field]

        # index_condition es la parte que aplica el index condition pushdown;
        # attached_condition, la que se revisa fila por fila.
        operation["predicate"] = _and_conditions(
            node.get("index_condition"),
            node.get("attached_condition"),
        )
        operation["estimated_rows"] = self._rows_per_scan(node)

        return operation

    @staticmethod
    def _rows_per_scan(node):
        """Filas que devuelve cada lectura de la tabla, como el Plan Rows de
        Postgres. rows_produced_per_join es el acumulado de todas las vueltas
        del join (5 000 000 sobre una tabla de 5 000 filas): solo coincide para
        la primera tabla."""
        examined = to_number(node.get("rows_examined_per_scan"))
        filtered = to_number(node.get("filtered"))
        if isinstance(examined, float) and isinstance(filtered, float):
            return round(examined * filtered / 100, 2)
        return node.get("rows_produced_per_join")

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