"""CT-01: abrazo mortal por orden inconsistente de acceso (PG y MySQL).

Regla (Tabla 3, issue #44): ciclo de >= 2 transacciones en el grafo de espera
en el que cada una mantiene en modo exclusivo el recurso que la otra solicita,
adquiridos en orden opuesto. Firma normalizada: conjunto ordenado de (huella,
tabla, indice, modo).

Operativamente, con el vocabulario comun del contrato: cada una pide un
bloqueo exclusivo de fila o de tabla sobre un recurso distinto, y quien la
bloquea mantiene ese recurso en X. Mismo recurso en S que pasa a X es
conversion (CT-02); bloqueos de hueco son contencion de rango (CT-03).

Sin targets no se sabe si esperaban recursos distintos: el abrazo queda sin
clasificar (unclassified_deadlock). Sin holds se acepta sin comprobar en que
modo retenia cada una lo que la otra pedia, y queda anotado: hoy es el caso de
Postgres, que no informa lo que mantiene cada proceso.
"""
from querylens_contract import DeadlockReport

from models import Hallazgo

from . import cycles

ANTIPATRON = "inconsistent_lock_order_deadlock"

_UNKNOWN_HOLDS = (
    "El informe no dice que mantenia cada transaccion: no se puede confirmar "
    "que retenia en modo exclusivo lo que la otra pedia. Si cada una habia "
    "leido antes con FOR SHARE la fila que despues pidio, el patron es "
    "conversion de modo (CT-02) y no orden inconsistente."
)

_RISK = (
    "Cambiar el orden de acceso toca la logica de la aplicacion: todas las "
    "rutas que modifican estas tablas tienen que seguir el mismo orden, o el "
    "ciclo reaparece entre la ruta nueva y una vieja. Tomar los bloqueos por "
    "adelantado (SELECT ... FOR UPDATE ordenado) alarga el tiempo que se "
    "retienen y puede aumentar la espera de otras sesiones."
)


def is_inconsistent_order(report: DeadlockReport) -> bool:
    if not cycles.is_cycle(report) or not cycles.targets_known(report):
        return False
    transactions = report.transactions
    if any(not cycles.requests_exclusive(trx.waits_for) for trx in transactions):
        return False
    # orden opuesto: cada una espera un recurso distinto. Todas sobre el mismo
    # es una conversion de modo (CT-02).
    waited = [frozenset(trx.waits_for.targets) for trx in transactions]
    if any(a & b for i, a in enumerate(waited) for b in waited[i + 1:]):
        return False
    if cycles.amplified(report):
        return False
    if not cycles.holds_known(report):
        return True
    return all(
        any(cycles.holds_on(holder, trx.waits_for, cycles.exclusive) for holder in cycles.holders(report, trx))
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
            [] if holds_known else [_UNKNOWN_HOLDS],
        ),
        explicacion=_explanation(report, table_names, recurrence["count"], holds_known),
        recomendacion=_recommendation(report, table_names),
        query_id=victim.query_id if victim and victim.query_id is not None else None,
        table_name=", ".join(table_names) if table_names else None,
    )


def _explanation(report: DeadlockReport, table_names: list[str], occurrences: int, holds_known: bool) -> str:
    # sin lo que mantiene cada una no se sabe en que modo lo retenia
    held = "retenia en modo exclusivo" if holds_known else "retenia"
    return (
        f"{len(report.transactions)} transacciones formaron un abrazo mortal{cycles.where(table_names)}: "
        f"cada una {held} lo que la otra pedia, porque tomaron los mismos "
        f"recursos en orden opuesto. {cycles.resolution(report)}"
        f"{cycles.recurrence_text(occurrences)}"
    )


def _recommendation(report: DeadlockReport, table_names: list[str]) -> str:
    return (
        f"Imponer un orden de acceso unico{cycles.where(table_names, 'sobre')}: toda transaccion que modifique "
        "varias filas o tablas debe tomarlas en el mismo orden (por ejemplo, por "
        "clave primaria ascendente), o bloquearlas todas al inicio con "
        "SELECT ... FOR UPDATE ordenado por esa clave. Mantener las "
        "transacciones cortas reduce la ventana del ciclo. "
        f"{cycles.retry_text(report)}"
    )
