def canonicalize_query(query_text, source_dialect="postgres"):
    import re
    import sqlglot
    from sqlglot import exp

    if not query_text:
        return None

    try:
        ast = sqlglot.parse_one(query_text, read=source_dialect)
        for node in list(ast.find_all(exp.Literal, exp.Boolean, exp.Null, exp.Parameter)):
            node.replace(exp.Placeholder())
        canonic = ast.sql(dialect="postgres", identify=False, comments=False)

        param_index = 1

        def replace_placeholder(match):
            nonlocal param_index
            current_param = f"${param_index}"
            param_index += 1
            return current_param

        return re.sub(r"%s", replace_placeholder, canonic).replace('"', "")
    except Exception:
        return " ".join(query_text.split())


def clean_mysql_sintax(query):
    return query.replace("DISTINCTROW", "DISTINCT")


def create_canonic_queries(stats, source_dialect="postgres", clean_mysql=False):
    for stmt in stats.get("top_impact_queries") or []:
        query_text = stmt.get("query_text")
        if clean_mysql:
            query_text = clean_mysql_sintax(query_text) if query_text else None
        stmt["canonic_query"] = canonicalize_query(query_text, source_dialect)


def anonimize_query_text(stats):
    dict_statements = {
        item["query_id"]: {k: v for k, v in item.items() if k != "query_id"}
        for item in stats.get("statements", [])
    }
    for stmd in stats.get("top_impact_queries", []):
        query_id = stmd.get("query_id")
        if query_id in dict_statements:
            stmd["query_text"] = dict_statements[query_id].get("query_text")


def normalize_querytext_active(stats, source_dialect="postgres"):
    for stmt in stats.get("active_queries", []):
        query_text = stmt.get("query_text")
        del stmt["query_text"]
        if not query_text:
            stmt["canonic_query"] = "Not available"
            continue

        stmt["canonic_query"] = canonicalize_query(query_text, source_dialect) or "Not available"