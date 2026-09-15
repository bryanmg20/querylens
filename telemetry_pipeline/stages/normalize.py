from datetime import datetime
import re
import sqlglot
from sqlglot import exp

def _to_number(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value

def normalize_stats(stats, collector):
    collector.normalize_engine_artifacts(stats)  
    return stats

def normalize_active_query_timestamps(stats):
        for stmt in stats.get("active_queries") or []:
            ts = stmt.get("transaction_start_time")
            if ts is None:
                continue
            if isinstance(ts, datetime):
                stmt["transaction_start_time"] = ts.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")

def normalize_blocking_pids(stats):
        for query in stats.get("active_queries", []):
            blocking_pids = query.get("blocking_pids")
            if blocking_pids is not None:
                query["blocking_pids"] = [int(pid) for pid in blocking_pids.split(",") if pid.strip().isdigit()]
            else:
                query["blocking_pids"] = []

def normalize_locks(stats):
    for lock in stats.get("locks", []):
        is_granted = lock.get("is_granted")
        if is_granted == "GRANTED":
            lock["is_granted"] = True
        elif is_granted == "WAITING":
            lock["is_granted"] = False

def normalize_predicate(stats):
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