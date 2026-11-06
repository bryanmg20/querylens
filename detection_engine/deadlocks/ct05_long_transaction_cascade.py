"""CT-05: cascada de bloqueo por transaccion larga (PG y MySQL).

Regla (Tabla 3, issue #46): en dos muestras consecutivas, una transaccion raiz
con antiguedad >= 30 s bloquea a >= 2 sesiones que esperan >= 5 s.

No es un abrazo mortal: la espera va en una sola direccion, asi que el motor no
la resuelve solo. Postgres espera indefinidamente (lock_timeout = 0 por
defecto) y MySQL hasta innodb_lock_wait_timeout (50 s). La raiz tipica es una
sesion 'idle in transaction': la aplicacion abrio la transaccion y no la cerro.

Todas las antiguedades se miden contra captured_at, que sale del mismo reloj
del servidor que transaction_start_time y wait_start_time.
"""
from datetime import datetime

from querylens_contract import ActiveQueryRow, SnapshotPayload

from anti_patterns.statement_history import parse_instant
from models import Hallazgo

from .state import CascadeRoot, RootsByKey
from .wait_graph import build_wait_graph

ANTIPATRON = "long_transaction_cascade"

MIN_ROOT_AGE_S = 30
MIN_WAIT_S = 5
MIN_BLOCKED = 2

# Severidad por impacto (seccion 3.3): tiempo de espera acumulado de las
# sesiones afectadas. La Tabla 3 no fija cortes; 120 s es el valor inicial a
# calibrar: 2 sesiones al minimo de la regla suman 10 s, y la inyeccion de la
# Tabla 3 (5 sesiones) supera los 120 s al confirmarse.
_HIGH_IMPACT_WAIT_S = 120

_RISK = (
    "Terminar la sesion raiz revierte toda su transaccion: los cambios que "
    "llevaba se pierden y la aplicacion recibe un error en esa conexion, asi "
    "que debe estar preparada para reintentar. Bajar lock_timeout o "
    "innodb_lock_wait_timeout hace que las sesiones en espera fallen antes en "
    "vez de esperar; idle_in_transaction_session_timeout corta tambien "
    "transacciones legitimamente largas si el valor es muy bajo."
)


def detect_long_transaction_cascade(
    snapshot: SnapshotPayload,
    captured_at: datetime,
    previous: RootsByKey,
) -> tuple[list[Hallazgo], RootsByKey]:
    """Devuelve los hallazgos de esta muestra y las raices que cumplieron la
    regla en ella, que seran el `previous` de la siguiente.

    Una raiz solo produce hallazgo cuando ya habia cumplido en la muestra
    anterior (dos consecutivas) y una sola vez por transaccion: mientras siga
    abierta, las muestras siguientes la arrastran con reported=True.
    """
    graph = build_wait_graph(snapshot.active_queries)
    findings: list[Hallazgo] = []
    current: RootsByKey = {}

    for root_pid in graph.roots():
        root = graph.sessions.get(root_pid)
        # sin fila (proceso de fondo, o MySQL sin la transaccion en innodb_trx)
        # no hay transaction_start_time contra el cual medir
        if root is None or root.transaction_start_time is None:
            continue
        root_age_s = _seconds_since(root.transaction_start_time, captured_at)
        if root_age_s is None or root_age_s < MIN_ROOT_AGE_S:
            continue

        blocked = [graph.sessions[pid] for pid in sorted(graph.blocked_under(root_pid)) if pid in graph.sessions]
        waits = {row.process_id: _seconds_since(row.wait_start_time, captured_at) for row in blocked}
        # sin wait_start_time no se sabe cuanto espera: no cuenta para el umbral
        long_waiters = [row for row in blocked if (waits[row.process_id] or 0) >= MIN_WAIT_S]
        if len(long_waiters) < MIN_BLOCKED:
            continue

        seen = previous.get((root_pid, root.transaction_start_time))
        state = CascadeRoot(
            pid=root_pid,
            transaction_start_time=root.transaction_start_time,
            first_seen_at=seen.first_seen_at if seen else captured_at,
            reported=seen.reported if seen else False,
        )
        if seen is not None and not seen.reported:
            findings.append(
                _finding(snapshot, root, root_age_s, blocked, waits, len(long_waiters), state, captured_at)
            )
            state.reported = True
        current[state.key] = state

    return findings, current


def _seconds_since(value: str | None, captured_at: datetime) -> float | None:
    instant = parse_instant(value)
    if instant is None:
        return None
    return (captured_at - instant).total_seconds()


def _finding(
    snapshot: SnapshotPayload,
    root: ActiveQueryRow,
    root_age_s: float,
    blocked: list[ActiveQueryRow],
    waits: dict[int, float | None],
    long_waiters: int,
    state: CascadeRoot,
    captured_at: datetime,
) -> Hallazgo:
    # impacto (seccion 3.3): espera acumulada de todas las sesiones afectadas
    impact_wait_s = round(sum(wait for wait in waits.values() if wait is not None), 1)
    involved = {root.process_id} | {row.process_id for row in blocked}
    locks = [lock for lock in snapshot.locks if lock.process_id in involved]
    # tablas donde esperan las bloqueadas; si el motor no las da (Postgres
    # espera sobre el transactionid), las que retiene la raiz
    tables = sorted({
        lock.table_name for lock in locks
        if lock.table_name and not lock.is_granted and lock.process_id != root.process_id
    }) or sorted({
        lock.table_name for lock in locks if lock.table_name and lock.process_id == root.process_id
    })
    idle = root.session_state is not None and root.session_state.startswith("idle in transaction")
    # la base de lo que se espera; la de la sesion raiz puede ser otra (MySQL
    # sin USE la deja en NULL)
    databases = sorted({
        lock.database_name for lock in locks
        if lock.database_name and not lock.is_granted and lock.process_id != root.process_id
    })

    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad=_severity(impact_wait_s),
        evidencia={
            "root": {
                "process_id": root.process_id,
                "database_name": root.database_name,
                "session_state": root.session_state,
                "transaction_start_time": root.transaction_start_time,
                "transaction_age_s": round(root_age_s, 1),
                # ultima sentencia de la raiz: si esta en reposo, no es la que
                # tomo el bloqueo, pero orienta a que parte de la aplicacion es
                "query_id": root.query_id,
                "canonic_query": root.canonic_query,
                "query_text": root.query_text,
            },
            "blocked_sessions": [
                {
                    "process_id": row.process_id,
                    "blocking_pids": row.blocking_pids,
                    "wait_start_time": row.wait_start_time,
                    "wait_s": round(waits[row.process_id], 1) if waits[row.process_id] is not None else None,
                    "query_id": row.query_id,
                    "canonic_query": row.canonic_query,
                    "query_text": row.query_text,
                }
                for row in blocked
            ],
            "blocked_count": len(blocked),
            f"blocked_waiting_{MIN_WAIT_S}s": long_waiters,
            # define la severidad y el orden de prioridad entre hallazgos
            "impact_wait_s": impact_wait_s,
            "risk": _RISK,
            "locks": [lock.model_dump() for lock in locks],
            "tables": tables,
            "first_seen_at": state.first_seen_at.isoformat(),
            "captured_at": captured_at.isoformat(),
            "thresholds": {
                "min_root_age_s": MIN_ROOT_AGE_S,
                "min_wait_s": MIN_WAIT_S,
                "min_blocked": MIN_BLOCKED,
            },
        },
        explicacion=_explanation(root, root_age_s, long_waiters, idle),
        recomendacion=_recommendation(root.process_id, idle),
        query_id=root.query_id,
        table_name=", ".join(tables) if tables else None,
        database_name=", ".join(databases) if databases else root.database_name,
    )


def _severity(impact_wait_s: float) -> str:
    if impact_wait_s >= _HIGH_IMPACT_WAIT_S:
        return "high"
    return "medium"


def _explanation(root: ActiveQueryRow, root_age_s: float, long_waiters: int, idle: bool) -> str:
    text = (
        f"La transaccion de la sesion {root.process_id} lleva {root_age_s:.0f} s abierta "
        f"y retiene bloqueos que tienen esperando a {long_waiters} sesiones "
        f"(>= {MIN_WAIT_S} s cada una). No es un abrazo mortal: el motor no lo "
        "resuelve solo y las sesiones siguen esperando hasta que la raiz termine "
        "o salte un timeout."
    )
    if idle:
        text += (
            " La raiz esta 'idle in transaction': no ejecuta nada, la aplicacion "
            "abrio la transaccion y no la cerro."
        )
    return text


def _recommendation(root_pid: int, idle: bool) -> str:
    text = (
        f"Inmediato: terminar la sesion raiz {root_pid} "
        f"(Postgres: SELECT pg_terminate_backend({root_pid}); MySQL: KILL {root_pid}). "
        "De fondo: transacciones cortas, sin trabajo externo (llamadas HTTP, "
        "esperas del usuario) entre BEGIN y COMMIT; partir los lotes grandes. "
        "Red de seguridad: lock_timeout y statement_timeout en Postgres, "
        "innodb_lock_wait_timeout mas bajo en MySQL."
    )
    if idle:
        text += (
            " Revisar que todos los caminos de error hagan COMMIT o ROLLBACK antes "
            "de devolver la conexion al pool; en Postgres, "
            "idle_in_transaction_session_timeout corta estas sesiones."
        )
    return text
