from datetime import datetime
import re
import sqlglot
from sqlglot import exp

from models.stats import Stats
from stages import canonicalizers

def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value

class NormalizeStage:
    def __init__(self, collector):
        self.collector = collector

    def execute(self, stats: Stats) -> Stats:
        canonicalizers.anonimize_query_text(stats)
        canonicalizers.create_canonic_queries(
            stats,
            self.collector.source_dialect,
            clean_mysql=(self.collector.source_dialect == "mysql"),
        )
        normalize_statement_epochs(stats)
        stats = self.collector.normalize_engine_artifacts(stats)
        canonicalizers.normalize_querytext_active(stats, self.collector.source_dialect)
        return stats


EPOCH_FIELDS = ("counters_epoch", "minmax_epoch")

EPOCH_SECTIONS = ("statements", "top_impact_queries", "non_explainable_candidates")


def normalize_statement_epochs(stats: Stats):
    """Deja las fechas de contadores en un unico formato sin tz.

    Postgres (`stats_since`, timestamptz) y MySQL (`FIRST_SEEN`, DATETIME) llegan
    con tipos distintos: uno con zona y el otro naive. Se hornean a la misma
    representacion que usa `normalize_active_query_timestamps`, asi el
    consumidor puede compararlas entre motores sin parsear dos formatos.
    """
    for section in EPOCH_SECTIONS:
        for row in stats.get(section) or []:
            for field in EPOCH_FIELDS:
                ts = row.get(field)
                if isinstance(ts, datetime):
                    row[field] = ts.replace(tzinfo=None).isoformat(
                        sep=" ", timespec="microseconds"
                    )


def normalize_active_query_timestamps(stats: Stats):
    for stmt in stats.get("active_queries") or []:
        ts = stmt.get("transaction_start_time")
        if ts is None:
            continue
        if isinstance(ts, datetime):
            stmt["transaction_start_time"] = ts.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")

def normalize_blocking_pids(stats: Stats):
    for query in stats.get("active_queries", []):
        blocking_pids = query.get("blocking_pids")
        if blocking_pids is not None:
            query["blocking_pids"] = [int(pid) for pid in blocking_pids.split(",") if pid.strip().isdigit()]
        else:
            query["blocking_pids"] = []

def normalize_locks(stats: Stats):
    for lock in stats.get("locks", []):
        is_granted = lock.get("is_granted")
        if is_granted == "GRANTED":
            lock["is_granted"] = True
        elif is_granted == "WAITING":
            lock["is_granted"] = False

def normalize_predicate(stats: Stats):
    for explain in stats.get("canonic_explains") or []:
        for operation in explain.get("canonical_plan", []).get("physical_operations", []):
            predicate = operation.get("predicate")
            if predicate:
                cleaned_predicate = clean_mysql_explain_predicate_dynamic(predicate)
                operation["predicate"] = cleaned_predicate

def clean_mysql_explain_predicate_dynamic(pred_text):
    if not pred_text:
        return None

    try:
        preprocessed = pred_text.replace('<cache>', '').replace('</cache>', '')
        ast = sqlglot.parse_one(preprocessed, read='mysql')
        for column in list(ast.find_all(exp.Column)):
            if column.db:
                column.set('db', None)
        for table in list(ast.find_all(exp.Table)):
            if table.db:
                table.set('db', None)
        return ast.sql(dialect='postgres', identify=False, comments=False).replace('"', '')
    except Exception:
        cleaned = re.sub(r'/\*.*?\*/', '', pred_text)
        cleaned = cleaned.replace('`', '')
        match = re.search(r'\b([a-zA-Z0-9_]+)\.[a-zA-Z0-9_]+\.', cleaned)
        if match:
            dynamic_schema = match.group(1)
            cleaned = re.sub(rf'\b{dynamic_schema}\.', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned