import json

from sqlalchemy import text

from logger import get_logger

from . import explain_normalizer as en

logger = get_logger(__name__)

EXPLAIN_NORMALIZERS = en.EXPLAIN_NORMALIZERS


class ExplainStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats, conn):
        stats["query_explain"] = []

        for query in stats.get("top_impact_queries", []):
            query_id = query.get("query_id")
            if query_id is None or not query.get("real_query_found"):
                continue

            try:
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