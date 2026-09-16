from sqlalchemy import text

from logger import get_logger
from models.stats import Stats

logger = get_logger(__name__)


class CollectStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats: Stats, conn) -> Stats:
        for key, query in self.collector.queries.items():
            try:
                result = conn.execute(text(query))
                stats[key] = [dict(row) for row in result.mappings()]
            except Exception as e:
                conn.rollback()
                stats[key] = None
                logger.error(f"{self.collector.source_dialect} | collect_telemetry | query={key} | {e}")
        return stats