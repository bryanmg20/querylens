import csv
import re
from dataclasses import dataclass
from pathlib import Path

from stages import canonicalizers

EXPLAINABLE_PREFIXES = ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")

PG_DURATION_RE = re.compile(r"duration:\s+([0-9.]+)\s+ms(?:\s+plan:.*?)?\s+statement:\s*(.*)$", re.DOTALL)

MYSQL_HEADER_RE = re.compile(r"^#\s+(Time|User@Host|Query_time|Schema):")
MYSQL_QUERY_TIME_RE = re.compile(r"#\s+Query_time:\s+([0-9.]+)")
MYSQL_SET_TIMESTAMP_RE = re.compile(r"^SET\s+timestamp\s*=.*$")
MYSQL_USE_RE = re.compile(r"^USE\s+\S+\s*;?\s*$", re.IGNORECASE)


@dataclass
class LogEntry:
    raw_text: str
    canonical_text: str
    duration_ms: float | None
    source: str
    log_time: str | None = None


def _is_explainable(query_text):
    return (query_text or "").strip().upper().startswith(EXPLAINABLE_PREFIXES)


def _field(row, index):
    if row is None or len(row) <= index:
        return None
    return row[index]


def _statement_from_message(message):
    if not message:
        return None
    match = PG_DURATION_RE.search(message)
    if not match:
        return None
    duration_ms = float(match.group(1))
    query_text = match.group(2).strip()
    return duration_ms, query_text


def parse_pg_csvlog(path) -> list:
    entries = []
    path = Path(path)

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue

            log_time = _field(row, 0)
            message = _field(row, 13) or ""

            duration_ms = None
            query_text = None

            extracted = _statement_from_message(message)
            if extracted is not None:
                duration_ms, query_text = extracted
            else:
                statement_field = _field(row, 19)
                if statement_field and _is_explainable(statement_field):
                    query_text = statement_field.strip()

            if not query_text or not _is_explainable(query_text):
                continue

            entries.append(
                LogEntry(
                    raw_text=query_text,
                    canonical_text=canonicalizers.canonicalize_query(query_text, "postgres"),
                    duration_ms=duration_ms,
                    source="postgres",
                    log_time=log_time,
                )
            )

    return entries


def parse_mysql_slow_log(path) -> list:
    entries = []
    path = Path(path)

    current_time = None
    current_duration = None
    sql_lines = []
    saw_header = False

    def flush():
        if not sql_lines:
            return
        raw_text = "\n".join(sql_lines).strip()
        raw_text = MYSQL_SET_TIMESTAMP_RE.sub("", raw_text, count=1).strip()
        raw_text = raw_text.rstrip().rstrip(";").strip()
        if raw_text and _is_explainable(raw_text):
            entries.append(
                LogEntry(
                    raw_text=raw_text,
                    canonical_text=canonicalizers.canonicalize_query(raw_text, "mysql"),
                    duration_ms=(current_duration * 1000) if current_duration is not None else None,
                    source="mysql",
                    log_time=current_time,
                )
            )
        sql_lines.clear()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()

            if MYSQL_HEADER_RE.match(stripped):
                if sql_lines or not saw_header:
                    flush()
                    saw_header = True
                    current_time = None
                    current_duration = None

                time_match = re.search(r"Time:\s*(.*)$", stripped)
                if time_match:
                    current_time = time_match.group(1).strip()

                duration_match = MYSQL_QUERY_TIME_RE.search(stripped)
                if duration_match:
                    current_duration = float(duration_match.group(1))
                continue

            if MYSQL_SET_TIMESTAMP_RE.match(stripped) or MYSQL_USE_RE.match(stripped):
                continue

            sql_lines.append(line.rstrip("\n"))

    flush()
    return entries


def build_log_index(dialect, path, min_duration_ms=None) -> dict:
    if dialect == "postgres":
        entries = parse_pg_csvlog(path)
    elif dialect == "mysql":
        entries = parse_mysql_slow_log(path)
    else:
        return {}

    index = {}
    for entry in entries:
        if min_duration_ms is not None and entry.duration_ms is not None and entry.duration_ms < min_duration_ms:
            continue
        if not entry.canonical_text:
            continue
        previous = index.get(entry.canonical_text)
        if previous is None or (entry.duration_ms or 0) >= (previous.duration_ms or 0):
            index[entry.canonical_text] = entry
    return index