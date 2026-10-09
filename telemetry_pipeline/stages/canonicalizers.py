import re

from sqlglot import exp

from models.stats import Stats

# Nodos de sqlglot que cargan un dato real de la query. HexString/BitString/
# ByteString no son exp.Literal: sin ellos 0xDEADBEEF, X'..', b'..' y E'..'
# viajaban crudos a la cola (UUIDs/hashes en columnas BINARY). Fuente unica
# para canonicalize_query y para los predicados de plan de MySQL.
DATA_LITERAL_NODES = (exp.Literal, exp.HexString, exp.BitString, exp.ByteString)

# pg_stat_statements respeta el espaciado original: IN (1,2,3) llega como
# IN ($1,$2,$3). El tokenizer postgres de sqlglot lee `$` como apertura de un
# dollar-quote y falla con un $N pegado a lo que sigue ($1,$2 / $1+$2), asi que
# la query quedaba en "Not available". Un tag de dollar-quote no empieza con
# digito, por lo que $N fuera de un identificador (col$1) siempre es parametro.
PG_PARAM_GLUED = re.compile(r"(?<![\w$])(\$\d+)(?=[^\s\d])")


def separate_pg_params(query_text):
    return PG_PARAM_GLUED.sub(r"\1 ", query_text)


def canonicalize_query(query_text, source_dialect="postgres"):
    import sqlglot

    if not query_text:
        return None

    if source_dialect == "postgres":
        query_text = separate_pg_params(query_text)

    try:
        ast = sqlglot.parse_one(query_text, read=source_dialect)
        for node in list(ast.find_all(*DATA_LITERAL_NODES, exp.Boolean, exp.Null, exp.Parameter)):
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