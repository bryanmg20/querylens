"""CT-03: abrazo mortal por contencion de rango (MySQL).

Regla (Tabla 3, issue #47): ciclo con un bloqueo de intencion de insercion
esperando por un bloqueo de hueco o next-key sobre el mismo indice.

Los bloqueos de hueco son compatibles entre si: varias transacciones pueden
tener el mismo hueco a la vez, pero ninguna puede insertar en el mientras otra
lo mantenga. El caso tipico es "verificar que no existe y despues insertar":
dos transacciones bloquean con FOR UPDATE claves inexistentes del mismo hueco
y despues cada una inserta la suya.

Con el vocabulario comun del contrato: alguna transaccion espera con scope
insert_intention y quien la bloquea mantiene, sobre ese mismo registro, un
bloqueo gap o row+gap (al final del indice InnoDB bloquea el supremum como
next-key). Las demas del ciclo pueden esperar otra cosa: basta un hueco en el
ciclo para que sea contencion de rango.

Postgres no tiene bloqueos de hueco: protege los rangos con bloqueos de
predicado (SERIALIZABLE) que no bloquean, y el conflicto termina en un error
de serializacion (40001), no en un abrazo mortal. Sin holds no se puede
comprobar que bloqueaba el hueco, asi que la regla los exige.

El rango se informa por el indice y el registro que cierra el hueco, con su
direccion fisica (space:page:heap): el pipeline descarta los valores de los
registros, e InnoDB no informa el registro que abre el hueco.
"""
from querylens_contract import DeadlockLock, DeadlockReport

from models import Hallazgo

from . import cycles

ANTIPATRON = "range_contention_deadlock"

# heap no 1 de una pagina es el supremum: el hueco despues de la ultima fila
_SUPREMUM_HEAP = "1"

_RANGE_LIMITS = (
    "El hueco se identifica por el registro que lo cierra, con su direccion "
    "fisica (space:page:heap) y no con su valor: los valores de los registros "
    "no salen del servidor. InnoDB no informa el registro que abre el hueco."
)

_RISK = (
    "INSERT ... ON DUPLICATE KEY UPDATE cambia 'fallar si ya existe' por "
    "'actualizar la existente', e INSERT IGNORE tambien convierte en avisos "
    "otros errores (por ejemplo, de tipo de dato). READ COMMITTED deja de "
    "proteger el rango: otra transaccion puede insertar filas que la "
    "verificacion no vio, y con replicacion requiere binlog_format = ROW."
)


def is_range_contention(report: DeadlockReport) -> bool:
    if not cycles.is_cycle(report) or not cycles.targets_known(report) or not cycles.holds_known(report):
        return False
    # un UPDATE/DELETE sin indice toma next-key sobre toda la tabla: es CT-04
    if cycles.amplified(report):
        return False
    return any(_blocked_insert(report, trx) for trx in report.transactions)


def finding(report: DeadlockReport, signature: str, recurrence: dict, captured_at) -> Hallazgo:
    table_names = cycles.tables(report)
    victim = cycles.victim(report)
    gaps = _gaps(report)
    evidence = cycles.evidence(report, signature, recurrence, captured_at, _RISK, [_RANGE_LIMITS])
    evidence["gaps"] = gaps
    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad=cycles.severity(recurrence["count"]),
        evidencia=evidence,
        explicacion=_explanation(report, table_names, gaps, recurrence["count"]),
        recomendacion=_recommendation(report),
        query_id=victim.query_id if victim and victim.query_id is not None else None,
        table_name=", ".join(table_names) if table_names else None,
    )


def _gap_hold(lock: DeadlockLock) -> bool:
    return lock.scope in cycles.GAP_HOLD_SCOPES


def _blocked_insert(report: DeadlockReport, trx) -> bool:
    """trx quiere insertar en un hueco que otra del ciclo tiene bloqueado."""
    return trx.waits_for.scope == "insert_intention" and any(
        cycles.holds_on(holder, trx.waits_for, _gap_hold) for holder in cycles.holders(report, trx)
    )


def _gaps(report: DeadlockReport) -> list[dict]:
    """Los huecos donde se quiso insertar, sin repetir: tabla, indice, si es
    el del final del indice y el registro que lo cierra."""
    gaps = []
    for trx in report.transactions:
        if not _blocked_insert(report, trx):
            continue
        lock = trx.waits_for
        for target in lock.targets:
            gap = {
                "table": cycles.qualified(lock.schema_name, lock.table_name),
                "index": lock.index_name,
                "position": "end_of_index" if target.rsplit(":", 1)[-1] == _SUPREMUM_HEAP else "before_record",
                "record": target,
            }
            if gap not in gaps:
                gaps.append(gap)
    return gaps


def _gap_text(gap: dict) -> str:
    where = "al final del" if gap["position"] == "end_of_index" else "antes de un registro del"
    return f"el hueco {where} indice {gap['index']}"


def _explanation(report: DeadlockReport, table_names: list[str], gaps: list[dict], occurrences: int) -> str:
    count = len(report.transactions)
    inserting = sum(1 for trx in report.transactions if _blocked_insert(report, trx))
    if inserting == count:
        who = "todas quisieron"
    elif inserting == 1:
        who = "una de ellas quiso"
    else:
        who = f"{inserting} de ellas quisieron"
    gap_texts = list(dict.fromkeys(_gap_text(gap) for gap in gaps))
    return (
        f"{count} transacciones formaron un abrazo mortal{cycles.where(table_names)} por contencion "
        f"de rango: {who} insertar en {' y en '.join(gap_texts)}, y ese hueco lo tenia bloqueado "
        "otra transaccion del ciclo (bloqueo de hueco o next-key). Los bloqueos de hueco son "
        "compatibles entre si, asi que varias transacciones pueden tener el mismo hueco a la vez, "
        "pero ninguna puede insertar en el mientras otra lo mantenga. "
        f"{cycles.resolution(report)}{cycles.recurrence_text(occurrences)}"
    )


def _recommendation(report: DeadlockReport) -> str:
    return (
        "El informe muestra la sentencia que esperaba (la insercion); el hueco lo bloqueo "
        "antes, en la misma transaccion, una lectura con FOR UPDATE o FOR SHARE, o un "
        "UPDATE/DELETE, que busco una clave que no existia o un rango. Si la transaccion "
        "verifica que la fila no existe y despues la inserta, reemplazar las dos sentencias "
        "por INSERT ... ON DUPLICATE KEY UPDATE o INSERT IGNORE, o insertar directamente y "
        "tratar el error de clave duplicada (1062). Si la verificacion no necesita proteger "
        "el rango, el aislamiento READ COMMITTED para esa transaccion evita los bloqueos de "
        f"hueco en las busquedas. {cycles.retry_text(report)}"
    )
