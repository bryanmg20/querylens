from models.stats import Stats


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
        return "Not available"


def clean_mysql_sintax(query):
    return query.replace("DISTINCTROW", "DISTINCT")


def create_canonic_queries(stats: Stats, source_dialect="postgres", clean_mysql=False):
    for stmt in stats.get("top_impact_queries") or []:
        query_text = stmt.get("query_text")
        if clean_mysql:
            query_text = clean_mysql_sintax(query_text) if query_text else None
        stmt["canonic_query"] = canonicalize_query(query_text, source_dialect)


def normalize_querytext_active(stats: Stats, source_dialect="postgres"):
    for stmt in stats.get("active_queries") or []:
        query_text = stmt.get("query_text")
        del stmt["query_text"]
        if not query_text:
            stmt["canonic_query"] = "Not available"
            continue

        stmt["canonic_query"] = canonicalize_query(query_text, source_dialect) or "Not available"


def redact_active_queries(stats: Stats):
    """Reja de privacidad del camino degradado (Q4): sin `statements` no hay
    normalizacion disponible, pero el texto real de las queries activas nunca
    puede viajar. Se descarta `query_text` y `canonic_query` queda en
    "Not available" para que sea inconfundible que no hubo contexto."""
    for stmt in stats.get("active_queries") or []:
        stmt.pop("query_text", None)
        stmt["canonic_query"] = "Not available"