from pathlib import Path
import re

from config.logs import LOG_SOURCES
from logger import get_logger
from models.stats import Stats
from stages import log_reader

logger = get_logger(__name__)


def params_complete_for_explain(query_text, params, dialect) -> bool:
    if dialect != "postgres":
        return True
    placeholders = set(int(m) for m in re.findall(r"\$(\d+)", query_text or ""))
    if not placeholders:
        return True
    have = set(params or {})
    return placeholders.issubset(have)


class LogsBackfillStage:
    def __init__(self, collector, log_sources=None):
        self.collector = collector
        self.log_sources = log_sources if log_sources is not None else LOG_SOURCES

    def _effective_min_duration_ms(self, cfg):
        if "min_duration_seconds" in cfg:
            return cfg["min_duration_seconds"] * 1000
        return cfg.get("min_duration_ms")

    def execute(self, stats: Stats) -> Stats:
        dialect = self.collector.source_dialect
        cfg = self.log_sources.get(dialect)
        if not cfg or not cfg.get("enabled"):
            return stats

        path = Path(cfg["path"])
        if not path.exists():
            logger.warning(f"{dialect} | log_backfill | log file not found: {path}")
            return stats

        try:
            index = log_reader.build_log_index(
                dialect,
                path,
                min_duration_ms=self._effective_min_duration_ms(cfg),
            )
        except Exception as e:
            logger.error(f"{dialect} | log_backfill | {e}")
            return stats

        matched = 0
        for candidate in stats.get("top_impact_queries", []):
            if candidate.get("real_query_found"):
                continue

            query_text = candidate.get("query_text")
            if not query_text:
                continue

            canonical = log_reader.fast_signature(query_text)
            entry = index.get(canonical)
            if entry is None:
                continue

            if not params_complete_for_explain(entry.raw_text, entry.params, dialect):
                logger.warning(
                    f"{dialect} | log_backfill | query_id={candidate.get('query_id')} "
                    "| log row lacks parameter values; left as not found"
                )
                continue

            candidate["real_query_found"] = True
            candidate["query_text"] = entry.raw_text
            candidate["query_params"] = entry.params
            matched += 1

        if matched:
            logger.info(
                f"{dialect} | log_backfill | {matched} candidate(s) "
                "recovered real query text from logs"
            )

        return stats