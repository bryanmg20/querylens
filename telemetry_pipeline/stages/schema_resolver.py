from models.stats import Stats


def _build_user_schema_map(resolver_rows):
    schema_by_user = {}
    for row in resolver_rows or []:
        user_id = row.get("user_id")
        resolved_schema = row.get("resolved_schema")
        if user_id is None or not resolved_schema:
            continue
        schema_by_user.setdefault(user_id, resolved_schema)
    return schema_by_user


def resolve_statements_schema(stats: Stats) -> Stats:
    resolver_rows = stats.get("schema_resolver")
    stats.pop("schema_resolver", None)
    if not resolver_rows:
        return stats

    schema_by_user = _build_user_schema_map(resolver_rows)

    for key in ("statements", "top_impact_queries", "non_explainable_candidates"):
        for stmt in stats.get(key) or []:
            user_id = stmt.pop("userid", None)
            stmt["schema_name"] = schema_by_user.get(user_id)

    return stats