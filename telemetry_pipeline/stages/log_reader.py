import csv
import io
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stages import canonicalizers

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

EXPLAINABLE_PREFIXES = ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")

PG_DURATION_RE = re.compile(
    r"duration:\s+([0-9.]+)\s+ms(?:\s+plan:.*?)?\s+"
    r"(?:statement:\s*|execute\s+\S+\s*:\s*)(.*)$",
    re.DOTALL,
)

PG_PARAMS_RE = re.compile(r"\$(\d+)\s*=\s*((?:'[^']*(?:''[^']*)*')|[^,]+)")

MAX_PARSE_ROWS = 500000

# Cuantas lineas fisicas del final de cada log se leen por ciclo. Como el log
# crece sin fin, se preserva lo RECIENTE (cola) en vez de lo viejo (cabeza).
LOG_TAIL_LINES = int(os.getenv("QL_LOG_TAIL_LINES", "50000"))

# Primera columna del csvlog de PostgreSQL (log_time) para detectar filas validas
PG_ROW_START = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+")

_T_READER_CHUNK = 1 << 20


def _parse_log_time(raw):
    """Normaliza el log_time a datetime naive UTC (None si no se puede)."""
    if not raw:
        return None
    norm = raw.strip().replace("Z", "").replace(" UTC", "").replace("+00:00", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(norm[:26], fmt)
        except ValueError:
            continue
    return None


def _since_dt(since):
    if not since:
        return None
    s = since.strip().replace("Z", "").replace(" UTC", "").replace("+00:00", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def _read_tail_lines(path, n) -> list:
    """Ultimas n lineas fisicas del archivo, leyendo desde el final por bloques."""
    path = Path(path)
    size = path.stat().st_size
    if size == 0:
        return []

    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""
        while buf.count(b"\n") < n and pos > 0:
            step = min(_T_READER_CHUNK, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf

    if not buf:
        return []
    lines = buf.split(b"\n")
    if pos > 0:
        lines = lines[1:]  # primera pieza es una linea cortada -> descartar
    lines = lines[-n:]
    return [ln.decode("utf-8", errors="replace") for ln in lines]


def fast_signature(query_text) -> str:
    text = (query_text or "").lower()
    text = text.replace("`", "").replace('"', "")
    text = text.replace("distinctrow", "distinct")
    text = re.sub(r",\s*\.\.\.", ",?", text)
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r"\$\d+", "?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    match = re.search(r"values\s*\(", text)
    if match:
        text = text[: match.end()] + "...)"
    return re.sub(r"\s+", "", text).strip(".,?;")

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
    params: dict | None = None


def _parse_pg_params(detail) -> dict:
    if not detail:
        return {}
    body = re.sub(r"^[Pp]arameters?:\s*", "", detail)
    params = {}
    for match in PG_PARAMS_RE.finditer(body):
        index = int(match.group(1))
        raw = match.group(2).strip()
        if raw.startswith("'") and raw.endswith("'"):
            raw = raw[1:-1].replace("''", "'")
        params[index] = raw
    return params


def materialize_placeholders(query_text, params=None) -> str:
    if not query_text or "$" not in query_text:
        return query_text
    params = params or {}

    def replace(match):
        index = int(match.group(1))
        value = params.get(index)
        if value is None:
            return "0"
        return "'" + value.replace("'", "''") + "'"

    return re.sub(r"\$(\d+)", replace, query_text)


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


def parse_pg_csvlog(path, tail_lines=LOG_TAIL_LINES, max_rows=MAX_PARSE_ROWS, since=None) -> list:
    entries = []
    path = Path(path)

    lines = _read_tail_lines(path, tail_lines)
    cleaned = []
    for line in lines:
        if not cleaned and not PG_ROW_START.match(line):
            continue  # descartar fila cortada en la frontera de la cola
        cleaned.append(line)
    if not cleaned:
        return entries

    reader = csv.reader(io.StringIO("\n".join(cleaned)))
    for row_count, row in enumerate(reader):
        if row_count >= max_rows:
            break
        if not row or len(row) < 20:  # fila parcial/sucio del borde del log
            continue

        log_time = _field(row, 0)
        message = _field(row, 13) or ""
        detail = _field(row, 14)

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

        query_text = query_text.rstrip(";").strip()

        params = _parse_pg_params(detail)

        if since is not None:
            since_dt = _since_dt(since)
            entry_dt = _parse_log_time(log_time)
            if since_dt is not None and entry_dt is not None and entry_dt < since_dt:
                continue

        entries.append(
            LogEntry(
                raw_text=query_text,
                canonical_text=fast_signature(query_text),
                duration_ms=duration_ms,
                source="postgres",
                log_time=log_time,
                params=params,
            )
        )

    return entries


def parse_mysql_slow_log(path, tail_lines=LOG_TAIL_LINES, max_rows=MAX_PARSE_ROWS, since=None) -> list:
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
            if since is not None:
                since_dt = _since_dt(since)
                entry_dt = _parse_log_time(current_time)
                if since_dt is not None and entry_dt is not None and entry_dt < since_dt:
                    sql_lines.clear()
                    return
            entries.append(
                LogEntry(
                    raw_text=raw_text,
                    canonical_text=fast_signature(raw_text),
                    duration_ms=(current_duration * 1000) if current_duration is not None else None,
                    source="mysql",
                    log_time=current_time,
                )
            )
        sql_lines.clear()

    for line in _read_tail_lines(path, tail_lines):
        if len(entries) >= max_rows:
            break
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

        if not saw_header:
            continue  # descartar linea cortada por delante de la cola

        if MYSQL_SET_TIMESTAMP_RE.match(stripped) or MYSQL_USE_RE.match(stripped):
            continue

        sql_lines.append(stripped)

    flush()
    return entries


def build_log_index(dialect, path, min_duration_ms=None, since=None) -> dict:
    if dialect == "postgres":
        entries = parse_pg_csvlog(path, since=since)
    elif dialect == "mysql":
        entries = parse_mysql_slow_log(path, since=since)
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