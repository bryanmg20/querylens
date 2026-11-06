"""CT-02: abrazo mortal por conversion de modo (PG y MySQL).

Regla (Tabla 3, issue #45): ciclo en el que todas las transacciones tienen
FOR SHARE sobre el mismo recurso y piden exclusivo. S es compatible con S, asi
que las dos entran a leer; ninguna puede pasar a X mientras la otra mantenga
su S, y ninguna lo suelta hasta terminar.

Con el vocabulario comun del contrato:

- Con holds (MySQL): cada una pide X sobre un recurso que ya mantiene en S, y
  quien la bloquea tambien lo mantiene en S.
- Sin holds (Postgres): todas piden X sobre el mismo recurso. Se deduce la
  conversion: dos transacciones solo se esperan mutuamente por la misma fila
  si ya la tenian las dos, y eso solo es posible en modo compartido. Queda
  anotado en la evidencia como inferido.

Sin targets no se sabe si esperaban el mismo recurso: el abrazo queda sin
clasificar (unclassified_deadlock).

El informe muestra la sentencia que esperaba (el UPDATE), no la lectura con
FOR SHARE que tomo el S: esa paso antes, en la misma transaccion.
"""
from querylens_contract import DeadlockReport

from models import Hallazgo

from . import cycles

ANTIPATRON = "lock_mode_conversion_deadlock"

_INFERRED = (
    "Inferido: el informe no dice que mantenia cada transaccion. Se deduce de "
    "que todas esperaban la misma fila: dos transacciones solo pueden "
    "esperarse mutuamente por la misma fila si ya la tenian las dos, y eso "
    "solo es posible en modo compartido."
)

_RISK = (
    "Pedir la fila con FOR UPDATE desde la lectura pone en fila a las "
    "transacciones que antes la leian en paralelo: las que solo leen y no "
    "modifican esperan ahora a las que modifican. Si la mayoria solo lee, "
    "conviene separar esa ruta y dejarla con FOR SHARE o sin bloqueo."
)

# De donde sale el S cuando el codigo no tiene un FOR SHARE escrito. Es texto
# para la recomendacion: la regla no depende del motor.
_IMPLICIT_SHARED = {
    "innodb_status": (
        "En MySQL el bloqueo compartido tambien lo toma un INSERT en una tabla "
        "hija sobre la fila padre (clave foranea), y cualquier SELECT con "
        "aislamiento SERIALIZABLE: en esos casos, bloquear antes la fila con "
        "SELECT ... FOR UPDATE."
    ),
    "postgres_log": (
        "En Postgres un INSERT en una tabla hija bloquea la fila padre con FOR "
        "KEY SHARE: choca con un DELETE o un cambio de clave de esa fila en "
        "otra transaccion."
    ),
}


def is_mode_conversion(report: DeadlockReport) -> bool:
    if not cycles.is_cycle(report) or not cycles.targets_known(report):
        return False
    transactions = report.transactions
    if any(not cycles.requests_exclusive(trx.waits_for) for trx in transactions):
        return False
    if cycles.amplified(report):
        return False
    if not cycles.holds_known(report):
        common = set.intersection(*(set(trx.waits_for.targets) for trx in transactions))
        return bool(common)
    return all(
        # ya tenia en S lo que pide en X...
        cycles.holds_on(trx, trx.waits_for, cycles.shared)
        # ...y lo que la frena es el S de la otra
        and any(cycles.holds_on(holder, trx.waits_for, cycles.shared) for holder in cycles.holders(report, trx))
        for trx in transactions
    )


def finding(report: DeadlockReport, signature: str, recurrence: dict, captured_at) -> Hallazgo:
    table_names = cycles.tables(report)
    holds_known = cycles.holds_known(report)
    victim = cycles.victim(report)
    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad=cycles.severity(recurrence["count"]),
        evidencia=cycles.evidence(
            report, signature, recurrence, captured_at, _RISK,
            [] if holds_known else [_INFERRED],
        ),
        explicacion=_explanation(report, table_names, recurrence["count"], holds_known),
        recomendacion=_recommendation(report),
        query_id=victim.query_id if victim and victim.query_id is not None else None,
        table_name=", ".join(table_names) if table_names else None,
    )


def _explanation(report: DeadlockReport, table_names: list[str], occurrences: int, holds_known: bool) -> str:
    count = len(report.transactions)
    if holds_known:
        cause = (
            "cada una tenia en modo compartido (FOR SHARE) la fila que despues "
            "quiso modificar, y ninguna podia pasar a exclusivo mientras la otra "
            "mantuviera su bloqueo compartido."
        )
    else:
        cause = (
            "todas pidieron modificar la misma fila y cada una esperaba a la otra. "
            "Eso solo ocurre si ya la tenian bloqueada en modo compartido (por "
            "ejemplo, con FOR SHARE) y quisieron pasar a exclusivo."
        )
    return (
        f"{count} transacciones formaron un abrazo mortal{cycles.where(table_names)}: {cause} "
        f"{cycles.resolution(report)}{cycles.recurrence_text(occurrences)}"
    )


def _recommendation(report: DeadlockReport) -> str:
    implicit = _IMPLICIT_SHARED.get(report.source)
    return (
        "Si la transaccion lee la fila antes de modificarla, pedirla desde el "
        "inicio en modo exclusivo con SELECT ... FOR UPDATE en lugar de FOR "
        "SHARE, o hacer la verificacion en el WHERE del UPDATE (por ejemplo "
        "UPDATE ... WHERE id = ... AND saldo >= ...) para no leerla antes. El "
        "informe muestra la sentencia que esperaba; la lectura con FOR SHARE "
        "esta antes, en la misma transaccion. "
        f"{implicit + ' ' if implicit else ''}{cycles.retry_text(report)}"
    )
