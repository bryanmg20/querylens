import json

from sqlalchemy import text

from logger import get_logger
from models.stats import Stats
from stages import explain_normalizer as en

logger = get_logger(__name__)

EXPLAIN_NORMALIZERS = en.EXPLAIN_NORMALIZERS


def is_single_statement(query_text):
    trimmed = (query_text or "").rstrip().rstrip(";").strip()
    return ";" not in trimmed


class ExplainStage:
    def __init__(self, collector):
        self.collector = collector

    @staticmethod
    def _search_path_sql(schema_name, engine):
        if not schema_name:
            return "SET LOCAL search_path TO DEFAULT"
        quoted = engine.dialect.identifier_preparer.quote(str(schema_name))
        return f"SET LOCAL search_path TO {quoted}"

    @staticmethod
    def _schema_context_sql(schema_name, dialect, engine):
        if dialect == "postgres":
            return ExplainStage._search_path_sql(schema_name, engine)
        if schema_name:
            quoted = engine.dialect.identifier_preparer.quote(str(schema_name))
            return f"USE {quoted}"
        return None

    def execute(self, stats: Stats, conn):
        stats["query_explain"] = []

        for query in stats.get("top_impact_queries", []):
            query_id = query.get("query_id")
            if query_id is None or not query.get("real_query_found"):
                continue

            query_text = query.get("query_text")
            if not is_single_statement(query_text):
                logger.warning(
                    f"{self.collector.source_dialect} | EXPLAIN | query_id={query_id} "
                    "| skipped multi-statement query"
                )
                continue

            try:
                context_sql = self._schema_context_sql(
                    query.get("schema_name"),
                    self.collector.source_dialect,
                    self.collector.engine,
                )
                if context_sql:
                    conn.execute(text(context_sql))

                if self.collector.source_dialect == "postgres":
                    result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {query.get('query_text')}"))
                    stats["query_explain"].append({
                        "query_id": query_id,
                        "plan": [dict(row) for row in result.mappings()],
                    })
                else:
                    result = conn.execute(text(f"EXPLAIN FORMAT=JSON {query.get('query_text')}"))
                    plan_row = next(result.mappings(), None)
                    if plan_row is None:
                        logger.error(f"mysql | EXPLAIN | query_id={query_id} | returned no plan row")
                        continue
                    stats["query_explain"].append({
                        "query_id": query_id,
                        "plan": json.loads(plan_row["EXPLAIN"]),
                    })
            except Exception as e:
                conn.rollback()
                logger.error(f"{self.collector.source_dialect} | EXPLAIN | query_id={query_id} | {e}")

        normalizer = EXPLAIN_NORMALIZERS[self.collector.source_dialect]()
        normalizer.normalize(stats)
        stats.pop("query_explain", None)

        return stats