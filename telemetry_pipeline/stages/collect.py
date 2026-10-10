from sqlalchemy import text

from health.classify import classify
from health.report import HealthReport
from logger import get_logger
from models.stats import Stats

logger = get_logger(__name__)


class CollectStage:
    def __init__(self, collector, report: HealthReport | None = None):
        self.collector = collector
        self.report = report if report is not None else HealthReport()

    def execute(self, stats: Stats, conn) -> Stats:
        for key, query in self.collector.queries.items():
            try:
                result = conn.execute(text(query))
                stats[key] = [dict(row) for row in result.mappings()]
                self.report.section_ok(key)
            except Exception as e:
                conn.rollback()
                stats[key] = None
                logger.error(f"{self.collector.source_dialect} | collect_telemetry | query={key} | {e}")
                code, params = classify(
                    e, dialect=self.collector.source_dialect, scope="collect", section=key
                )
                self.report.add(code, section=key, **params)
                self.report.section_failed(key)
        self.report.checked("collect")
        return stats
