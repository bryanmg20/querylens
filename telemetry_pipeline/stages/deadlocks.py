"""Informes de abrazo mortal de la ventana reciente (CT-01 a CT-04).

El motor resuelve un abrazo mortal en cuanto lo detecta (Postgres tras
deadlock_timeout, InnoDB al instante): el ciclo nunca aparece en una muestra
de active_queries. Lo que queda es el informe que el motor escribe al
revertir la victima, y eso es lo que lee este stage.

Postgres: las lineas "deadlock detected" (state_code 40P01) del log JSON, de
la misma ventana que lee auto_explain (stages/pg_log.PgLogTail, una sola
lectura por ciclo; guia references/006). Los errores van al log con la
configuracion por defecto (log_min_messages=warning). Con log_lock_waits,
tambien los avisos "still waiting" de la misma ventana: dicen en que fila
esperaba cada proceso que no fue la victima.

MySQL: SHOW ENGINE INNODB STATUS trae el detalle, pero solo del ultimo abrazo.
performance_schema.error_log trae el encabezado de cada uno (MY-012468, con
innodb_print_all_deadlocks=ON y log_error_verbosity=3), pero no el detalle,
que InnoDB escribe directo al stderr. Se combinan: el encabezado que coincide
con el ultimo informe le da su hora exacta; los demas de la ventana salen
como abrazos sin detalle (complete=False).

No hay estado entre ciclos: cada snapshot trae los informes de los ultimos
LOOKBACK_S segundos y el detector descarta los que ya vio.
"""
import json
from datetime import datetime, timedelta

from querylens_contract import to_naive_utc
from sqlalchemy import text

from health.report import HealthReport
from logger import get_logger
from models.stats import Stats
from stages.deadlock_reports import (
    parse_innodb_status,
    parse_innodb_timestamp,
    parse_postgres_deadlock,
    pg_still_waiting_xid,
)
from stages.pg_log import PgLogTail

logger = get_logger(__name__)

# Ventana de informes por snapshot. Cubre varios ciclos (10 s) y el backoff
# del runner (tope 60 s): un abrazo no se pierde porque un ciclo falle.
LOOKBACK_S = 120

# La ventana del log es la de PgLogTail (2 MB). Un dia con mucho auto_explain
# puede escribir mas que eso en LOOKBACK_S: los abrazos que queden antes de la
# ventana leida se pierden.

# Un encabezado de error_log y el informe de INNODB STATUS son el mismo abrazo
# si estan a menos de esto (INNODB STATUS escribe la hora sin fracciones).
_MATCH_TOLERANCE = timedelta(seconds=2)

# state_code es clave de primer nivel de la linea jsonlog: se filtra sin parsear.
_PG_DEADLOCK_MARKER = b'"state_code":"40P01"'
# El aviso de log_lock_waits es un LOG sin state_code propio: se filtra por
# el texto del mensaje.
_PG_STILL_WAITING_MARKER = b"still waiting for"

PG_SETTINGS_SQL = """
SELECT current_setting('log_error_verbosity') AS log_error_verbosity,
       current_setting('log_min_messages')    AS log_min_messages,
       current_setting('log_lock_waits')      AS log_lock_waits,
       current_setting('deadlock_timeout')    AS deadlock_timeout,
       current_database()                     AS database_name,
       now() AT TIME ZONE 'UTC'               AS now_utc
"""

# Cada linea jsonlog trae la hora en log_timezone con su abreviatura ("UTC",
# "-05", "CET"): el servidor la entiende sin ambiguedad.
PG_TIMESTAMPS_SQL = text(
    "SELECT raw, (raw::timestamptz AT TIME ZONE 'UTC') AS utc FROM unnest(CAST(:raw AS text[])) AS raw"
)

PG_RELATIONS_SQL = text(
    """
    SELECT c.oid::bigint AS oid, n.nspname AS schema_name, c.relname AS table_name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = ANY(CAST(:oids AS oid[]))
    """
)

# Tablas de los row_key por nombre, en todos los esquemas de la base: una
# sentencia sin esquema se resuelve con el search_path de la aplicacion, que no
# es el de la sesion del pipeline. Si el nombre esta en un solo esquema no hay
# ambiguedad.
PG_ROW_TABLES_SQL = text(
    """
    SELECT n.nspname AS schema_name, c.relname AS table_name, c.oid::bigint AS table_oid
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = ANY(CAST(:tables AS text[]))
      AND c.relkind IN ('r', 'p')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg_toast%'
    """
)

# search_path con que arranca una sesion del rol en esta base: el de ALTER
# ROLE ... IN DATABASE ... SET, el del rol, el de la base o el del servidor,
# en ese orden. El del servidor se lee de la sesion del pipeline solo si no
# lo pisa algo del pipeline (su rol, su base o un SET).
PG_ROLE_SEARCH_PATH_SQL = text(
    """
    SELECT
      (SELECT cfg FROM pg_catalog.pg_db_role_setting s, unnest(s.setconfig) AS cfg
       WHERE cfg LIKE 'search_path=%' AND s.setrole = r.oid AND s.setdatabase = d.oid) AS role_database,
      (SELECT cfg FROM pg_catalog.pg_db_role_setting s, unnest(s.setconfig) AS cfg
       WHERE cfg LIKE 'search_path=%' AND s.setrole = r.oid AND s.setdatabase = 0) AS role_only,
      (SELECT cfg FROM pg_catalog.pg_db_role_setting s, unnest(s.setconfig) AS cfg
       WHERE cfg LIKE 'search_path=%' AND s.setrole = 0 AND s.setdatabase = d.oid) AS database_only,
      CASE WHEN st.source IN ('default', 'configuration file', 'command line', 'environment variable', 'override')
           THEN 'search_path=' || st.setting END AS server
    FROM pg_catalog.pg_database d
    LEFT JOIN pg_catalog.pg_roles r ON r.rolname = :role
    CROSS JOIN pg_catalog.pg_settings st
    WHERE d.datname = current_database() AND st.name = 'search_path'
    """
)
_PG_DEFAULT_SEARCH_PATH = 'search_path="$user", public'

# Indices unicos de las tablas de los row_key: si las columnas del WHERE
# cubren uno, la sentencia toca una sola fila. Los parciales y de expresiones
# no cuentan. Solo las columnas clave: indkey trae tambien las de INCLUDE, que
# no hacen falta en el WHERE para que sea unica.
PG_UNIQUE_KEYS_SQL = text(
    """
    SELECT i.indrelid::bigint AS table_oid, i.indexrelid::bigint AS index_oid,
           array_agg(a.attname::text ORDER BY a.attname) AS columns
    FROM pg_catalog.pg_index i
    JOIN pg_catalog.pg_attribute a
      ON a.attrelid = i.indrelid AND a.attnum = ANY((i.indkey::int2[])[0:i.indnkeyatts - 1])
    WHERE i.indrelid = ANY(CAST(:oids AS oid[]))
      AND i.indisunique AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL
    GROUP BY i.indrelid, i.indexrelid, i.indisprimary
    ORDER BY i.indrelid, i.indisprimary DESC, i.indexrelid
    """
)

# La sesion del pipeline esta en +00:00 (config/connections): LOGGED y NOW(6)
# salen en UTC. INNODB STATUS escribe en la zona del sistema: el offset la
# pasa a UTC.
MYSQL_SETTINGS_SQL = """
SELECT UTC_TIMESTAMP(6) AS now_utc,
       TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', 'SYSTEM')) AS system_offset_s,
       @@innodb_print_all_deadlocks AS print_all_deadlocks,
       @@log_error_verbosity AS log_error_verbosity
"""

INNODB_STATUS_SQL = "SHOW ENGINE INNODB STATUS"

MYSQL_ERROR_LOG_SQL = f"""
SELECT LOGGED AS logged_at
FROM performance_schema.error_log
WHERE ERROR_CODE = 'MY-012468'
  AND LOGGED >= NOW(6) - INTERVAL {LOOKBACK_S} SECOND
ORDER BY LOGGED
"""

_PG_SILENT_LEVELS = {"log", "fatal", "panic"}


class DeadlocksStage:
    def __init__(self, collector, report: HealthReport | None = None):
        self.collector = collector
        self.report = report if report is not None else HealthReport()

    def execute(self, stats: Stats, conn, log: PgLogTail | None = None) -> Stats:
        stats["deadlocks"] = []
        dialect = self.collector.source_dialect
        if dialect not in ("postgres", "mysql"):
            return stats
        try:
            # Transaccion corta, como auto_explain: si falta una funcion o un
            # permiso, el rollback deja la conexion lista para lo que sigue.
            with conn.begin():
                if dialect == "postgres":
                    reports = self._postgres(conn, log if log is not None else PgLogTail())
                else:
                    reports = self._mysql(conn)
        except Exception as e:
            logger.info(f"{dialect} | DEADLOCKS | informes no disponibles | {e}")
            self.report.add("DEADLOCK_REPORTS_UNAVAILABLE", section="deadlocks")
            return stats
        stats["deadlocks"] = reports
        if reports:
            logger.info(
                f"{dialect} | DEADLOCKS | {len(reports)} en los ultimos {LOOKBACK_S} s "
                f"({sum(not r['complete'] for r in reports)} sin detalle)"
            )
        return stats

    # ---------------------------------------------------------- postgres

    def _postgres(self, conn, log: PgLogTail) -> list[dict]:
        settings = next(iter(conn.execute(text(PG_SETTINGS_SQL)).mappings()))
        if settings["log_error_verbosity"] == "terse" or settings["log_min_messages"] in _PG_SILENT_LEVELS:
            self.report.add(
                "DEADLOCK_REPORTS_PARTIAL", section="deadlocks",
                log_error_verbosity=settings["log_error_verbosity"],
                log_min_messages=settings["log_min_messages"],
            )
        if settings["log_lock_waits"] != "on":
            # sin el aviso de cada espera, una sentencia con $1 no dice que
            # fila esperaba: CT-01 y CT-02 no se distinguen
            self.report.add(
                "DEADLOCK_ROW_WAITS_UNLOGGED", section="deadlocks",
                log_lock_waits=settings["log_lock_waits"],
                deadlock_timeout=settings["deadlock_timeout"],
            )
        since = to_naive_utc(settings["now_utc"]) - timedelta(seconds=LOOKBACK_S)

        records, still_waiting = _pg_deadlock_records(log, conn, since)
        if not records:
            return []
        reports = [parse_postgres_deadlock(record, still_waiting) for record in records]

        raw = sorted({report["detected_at"] for report in reports if report["detected_at"]})
        utc = {
            row["raw"]: to_naive_utc(row["utc"])
            for row in conn.execute(PG_TIMESTAMPS_SQL.bindparams(raw=raw)).mappings()
        } if raw else {}
        kept = []
        for report in reports:
            detected_at = utc.get(report["detected_at"])
            if detected_at is None or detected_at < since:
                continue
            # el log es de todo el servidor: un deadlock de otra base trae
            # consultas de otro proyecto y tablas que esta conexion no ve. Un
            # 40P01 siempre sale de un backend con base, asi que sin dbname
            # tampoco se puede asegurar que sea de esta.
            if report["database_name"] != settings["database_name"]:
                continue
            report["detected_at"] = _iso(detected_at)
            kept.append(report)

        self._resolve_pg_relations(conn, kept)
        self._label_pg_rows(conn, kept)
        # el log se lee del archivo mas nuevo al mas viejo; como en MySQL, por hora
        return sorted(kept, key=lambda report: report["detected_at"])

    def _resolve_pg_relations(self, conn, reports: list[dict]) -> None:
        """relation_oid -> schema y tabla. Los informes ya son todos de la base
        conectada: los OID de otra base no significarian nada aca."""
        locks = [
            trx["waits_for"]
            for report in reports
            for trx in report["transactions"]
            if trx["waits_for"].get("relation_oid") is not None
        ]
        oids = sorted({lock["relation_oid"] for lock in locks})
        names = {
            int(row["oid"]): row
            for row in conn.execute(PG_RELATIONS_SQL.bindparams(oids=oids)).mappings()
        } if oids else {}
        for lock in locks:
            name = names.get(lock.pop("relation_oid"))
            if name is not None:
                lock["schema_name"] = name["schema_name"]
                lock["table_name"] = name["table_name"]

    def _label_pg_rows(self, conn, reports: list[dict]) -> None:
        """row_key o row_ctid -> targets ("row 1", "row 2"...): dos esperas con
        la misma etiqueta son sobre la misma fila. Se etiqueta un informe solo
        si todas sus esperas son de fila y cada una identifica su fila en una
        tabla que se pudo resolver; si no, targets queda en None y el detector
        no adivina.

        Primero por los valores de las sentencias (row_key), si cubren un
        indice unico. Si no alcanzan, por el ctid de cada espera (row_ctid):
        dos esperas sobre el mismo ctid de la misma tabla son la misma fila.
        Nunca mezclados en un informe: un "row 1" por clave y otro por ctid no
        se comparan."""
        local = []
        for report in reports:
            locks = [trx["waits_for"] for trx in report["transactions"]]
            keys = [lock.pop("row_key", None) for lock in locks]
            ctids = [lock.pop("row_ctid", None) for lock in locks]
            keys = keys if keys and all(keys) else None
            ctids = ctids if ctids and all(ctids) else None
            if keys or ctids:
                local.append((locks, keys, ctids))
        refs = [ref for _, keys, ctids in local for ref in (keys or []) + (ctids or [])]
        names = sorted({ref["table"] for ref in refs})
        if not names:
            return

        candidates: dict[str, list[tuple[str, int]]] = {}
        for row in conn.execute(PG_ROW_TABLES_SQL.bindparams(tables=names)).mappings():
            candidates.setdefault(row["table_name"], []).append((row["schema_name"], int(row["table_oid"])))
        # el search_path solo hace falta para los nombres que estan en varios esquemas
        ambiguous_roles = sorted({
            ref["role"] for ref in refs
            if ref["schema"] is None and len(candidates.get(ref["table"], [])) > 1 and ref["role"]
        })
        paths = {role: self._pg_search_path(conn, role) for role in ambiguous_roles}

        def resolve(refs):
            return [_resolve_table(ref, candidates.get(ref["table"], []), paths.get(ref["role"])) for ref in refs] if refs else None

        resolved = [(locks, keys, resolve(keys), ctids, resolve(ctids)) for locks, keys, ctids in local]
        oids = sorted({oid for _, _, key_oids, _, _ in resolved for oid in key_oids or () if oid is not None})
        unique: dict[int, list[tuple[int, list[str]]]] = {}
        if oids:
            for row in conn.execute(PG_UNIQUE_KEYS_SQL.bindparams(oids=oids)).mappings():
                unique.setdefault(int(row["table_oid"]), []).append((int(row["index_oid"]), list(row["columns"])))

        for locks, keys, key_oids, ctids, ctid_oids in resolved:
            if keys and _label_rows(locks, _key_identities(keys, key_oids, unique), "row_key"):
                continue
            if ctids:
                identities = [(oid, ref["ctid"]) if oid is not None else None for ref, oid in zip(ctids, ctid_oids, strict=True)]
                _label_rows(locks, identities, "ctid")

    def _pg_search_path(self, conn, role: str) -> list[str]:
        row = next(iter(conn.execute(PG_ROLE_SEARCH_PATH_SQL.bindparams(role=role)).mappings()), None)
        setting = next(
            (row[column] for column in ("role_database", "role_only", "database_only", "server") if row and row[column]),
            _PG_DEFAULT_SEARCH_PATH,
        )
        return search_path_schemas(setting.removeprefix("search_path="), role)

    # ------------------------------------------------------------- mysql

    def _mysql(self, conn) -> list[dict]:
        settings = next(iter(conn.execute(text(MYSQL_SETTINGS_SQL)).mappings()))
        now = to_naive_utc(settings["now_utc"])
        since = now - timedelta(seconds=LOOKBACK_S)
        offset = timedelta(seconds=int(settings["system_offset_s"] or 0))

        status = next(iter(conn.execute(text(INNODB_STATUS_SQL)).mappings()), None)
        latest = parse_innodb_status(status["Status"] if status is not None else "")

        headers = self._mysql_error_log(conn, settings)
        reports = []
        if latest is not None:
            detected_at = parse_innodb_timestamp(latest["detected_at"]) - offset
            # el encabezado mas nuevo dentro de la tolerancia es el del ultimo informe
            match = max(
                (h for h in headers if abs(h - detected_at) <= _MATCH_TOLERANCE),
                default=None,
            )
            if match is not None:
                headers.remove(match)
                detected_at = match
            if detected_at >= since:
                latest["detected_at"] = _iso(detected_at)
                reports.append(latest)
        # los demas de la ventana: INNODB STATUS ya los piso con uno mas nuevo
        for header in headers:
            reports.append({
                "detected_at": _iso(header),
                "source": "mysql_error_log",
                "database_name": None,
                "complete": False,
                "transactions": [],
            })
        return sorted(reports, key=lambda report: report["detected_at"])

    def _mysql_error_log(self, conn, settings) -> list[datetime]:
        if not settings["print_all_deadlocks"] or int(settings["log_error_verbosity"] or 0) < 3:
            # Sin esto error_log no recibe los encabezados: solo se ve el
            # ultimo abrazo entre dos ciclos
            self.report.add(
                "DEADLOCK_REPORTS_PARTIAL", section="deadlocks",
                innodb_print_all_deadlocks=int(settings["print_all_deadlocks"] or 0),
                log_error_verbosity=int(settings["log_error_verbosity"] or 0),
            )
            return []
        try:
            return [to_naive_utc(row["logged_at"]) for row in conn.execute(text(MYSQL_ERROR_LOG_SQL)).mappings()]
        except Exception as e:
            # performance_schema.error_log existe desde 8.0.22
            logger.info(f"mysql | DEADLOCKS | error_log no disponible | {e}")
            self.report.add("DEADLOCK_REPORTS_PARTIAL", section="deadlocks")
            return []


def _pg_deadlock_records(log: PgLogTail, conn, since: datetime) -> tuple[list[dict], dict[tuple[int, str], dict]]:
    """Los 40P01 de la ventana y los avisos "still waiting" por (pid,
    transaccion esperada). Un archivo que no cambio desde `since` no puede
    tener abrazos de la ventana: ahi se corta, sin leer los mas viejos."""
    records = []
    still_waiting: dict[tuple[int, str], dict] = {}
    for lines in log.files(conn, modified_since=since):
        for line in lines:
            deadlock = _PG_DEADLOCK_MARKER in line
            if not deadlock and _PG_STILL_WAITING_MARKER not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if deadlock and record.get("state_code") == "40P01":
                records.append(record)
                continue
            xid = pg_still_waiting_xid(record)
            if xid is None or not isinstance(record.get("pid"), int):
                continue
            # el mismo pid pudo esperar dos veces la misma transaccion: la
            # del abrazo es la ultima (la hora del log se compara como texto:
            # misma zona y formato en todo el log)
            key = (record["pid"], xid)
            previous = still_waiting.get(key)
            if previous is None or str(record.get("timestamp")) > str(previous.get("timestamp")):
                still_waiting[key] = record
    return records, still_waiting


def _resolve_table(key: dict, candidates: list[tuple[str, int]], search_path: list[str] | None) -> int | None:
    """OID de la tabla del row_key o row_ctid. Con esquema, ese; sin esquema, la unica
    con ese nombre o la primera del search_path de la aplicacion. Un SET
    search_path de la sesion de la aplicacion no queda en ningun catalogo: si
    el nombre esta en varios esquemas y el del rol no decide, no se resuelve."""
    if key["schema"] is not None:
        return next((oid for schema, oid in candidates if schema == key["schema"]), None)
    if len(candidates) == 1:
        return candidates[0][1]
    for wanted in search_path or ():
        oid = next((oid for schema, oid in candidates if schema == wanted), None)
        if oid is not None:
            return oid
    return None


def search_path_schemas(value: str, role: str | None) -> list[str]:
    """'"$user", public, "Mi Esquema"' -> [role, 'public', 'Mi Esquema'].
    Sin comillas Postgres pasa el nombre a minusculas; $user es el rol."""
    items: list[tuple[str, bool]] = []
    current, quoted, in_quotes = "", False, False
    index = 0
    while index < len(value):
        char = value[index]
        if in_quotes:
            if char == '"' and value[index + 1:index + 2] == '"':
                current += '"'
                index += 1
            elif char == '"':
                in_quotes = False
            else:
                current += char
        elif char == '"':
            in_quotes, quoted = True, True
        elif char == ",":
            items.append((current, quoted))
            current, quoted = "", False
        elif not char.isspace():
            current += char
        index += 1
    items.append((current, quoted))

    schemas = []
    for name, was_quoted in items:
        name = name if was_quoted else name.lower()
        if name == "$user":
            if role:
                schemas.append(role)
        elif name:
            schemas.append(name)
    return schemas


def _key_identities(keys: list[dict], table_oids: list[int | None], unique: dict[int, list]) -> list[tuple | None]:
    identities = [
        _row_identity(key, oid, unique.get(oid, [])) if oid is not None else None
        for key, oid in zip(keys, table_oids, strict=True)
    ]
    # la misma tabla por dos indices distintos (id = 1 y email = 'x') puede
    # ser la misma fila: no se puede comparar
    indexes = {}
    if all(identities) and any(indexes.setdefault(table, index) != index for table, index, _ in identities):
        return [None] * len(identities)
    return identities


def _label_rows(locks: list[dict], identities: list[tuple | None], source: str) -> bool:
    """targets de cada espera por su identidad, o nada si falta alguna."""
    if not all(identities):
        return False
    labels: dict[tuple, str] = {}
    for lock, identity in zip(locks, identities, strict=True):
        lock["targets"] = [labels.setdefault(identity, f"row {len(labels) + 1}")]
        lock["targets_source"] = source
    return True


def _row_identity(key: dict, table_oid: int, indexes: list[tuple[int, list[str]]]) -> tuple | None:
    """(tabla, indice, tokens de sus columnas) del primer indice unico que el
    WHERE cubre entero, la clave primaria primero."""
    for index_oid, columns in indexes:
        if all(column in key["values"] for column in columns):
            return table_oid, index_oid, tuple(key["values"][column] for column in columns)
    return None


def _iso(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="microseconds")
