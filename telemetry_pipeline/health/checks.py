"""Chequeos sobre lo recolectado: sintomas que no son error del driver.

Corre justo despues del collect, ANTES de normalize/redact: esos stages
reemplazan el texto de las queries y el sintoma se perderia. Solo cuenta
filas; ningun texto sale de aqui.
"""
from health.report import HealthReport
from models.stats import Stats

# Lo que Postgres pone en query cuando el rol no puede ver queries ajenas.
PG_HIDDEN_TEXT = "<insufficient privilege>"


def _count(rows, predicate) -> int:
    return sum(1 for row in rows or [] if predicate(row))


def post_collect(stats: Stats, report: HealthReport, dialect: str) -> None:
    if dialect == "postgres":
        # En PG < 17 statements falla por la columna stats_since (42703) y el
        # collect lo lee como extension desactualizada; la causa real es la
        # version, que el preflight ya reporto con su propia remediacion.
        if report.has("PG_VERSION_UNSUPPORTED"):
            report.discard("PG_STATEMENTS_OUTDATED", "statements")
        hidden = _count(stats.get("statements"), lambda r: r.get("query_text") == PG_HIDDEN_TEXT)
        hidden += _count(stats.get("active_queries"), lambda r: r.get("query_text") == PG_HIDDEN_TEXT)
        if hidden:
            report.add("MISSING_PG_READ_ALL_STATS", rows=hidden)
        return

    if dialect == "mysql":
        limit = report.facts.get("sql_text_limit")
        if not limit:
            return
        # El limite es en bytes; una muestra que lo alcanza llego cortada.
        truncated = _count(
            stats.get("statements"),
            lambda r: bool(r.get("query_sample_text"))
            and len(str(r["query_sample_text"]).encode("utf-8")) >= limit,
        )
        if truncated:
            report.add("QUERY_TEXT_TRUNCATED", rows=truncated, sql_text_limit=limit)
