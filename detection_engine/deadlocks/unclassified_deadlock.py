"""Abrazo mortal que no se puede clasificar entre CT-01 y CT-02.

Hubo un ciclo, pero el informe no identifica que recurso esperaba cada
transaccion (targets None): no se sabe si tomaron recursos distintos en orden
opuesto (CT-01) o si todas pedian el mismo que ya tenian en S (CT-02). Hoy es
el caso de Postgres cuando las sentencias no traen sus valores escritos
(sentencias preparadas) o no tocan una sola fila por una clave unica.

No se adivina: se informa el abrazo, el arreglo que cubre los dos casos y como
averiguar cual fue.
"""
from querylens_contract import DeadlockReport

from models import Hallazgo

from . import cycles

ANTIPATRON = "unclassified_deadlock"

# Por que el informe de cada motor no dice que recurso esperaba cada una, y
# como averiguarlo en ese motor. Es texto: la regla no depende del motor.
_WHY_UNKNOWN = {
    "postgres_log": (
        "Postgres no informa que fila espera cada proceso, solo la transaccion "
        "que la tiene. La fila se identifica por los valores de la sentencia, "
        "cuando vienen escritos en ella y cubren una clave unica, o por el "
        "aviso que Postgres escribe con log_lock_waits si una sesion espera "
        "mas que deadlock_timeout: con sentencias preparadas ($1) y esperas "
        "que empezaron casi juntas no hay de donde sacarla."
    ),
}
_WHY_UNKNOWN_DEFAULT = "El informe no identifica el recurso que esperaba cada transaccion."

_DIAGNOSIS = {
    "postgres_log": (
        "Si no queda claro, activar log_lock_waits en el servidor (registra la "
        "fila de las esperas largas, y con eso QueryLens distingue los abrazos "
        "cuyas esperas empezaron con mas de deadlock_timeout de diferencia), "
        "o registrar por un tiempo acotado las "
        "sentencias del rol de la aplicacion (ALTER ROLE ... SET log_statement "
        "= 'all'), que muestra la lectura previa y sus valores; esto ultimo deja "
        "datos reales en el log del servidor."
    ),
}

_RISK = (
    "Bloquear todas las filas al inicio alarga el tiempo que se retienen y "
    "puede aumentar la espera de otras sesiones; hacerlo por clave ascendente "
    "en todas las rutas que modifican estas tablas, o el ciclo reaparece."
)


def is_unclassified(report: DeadlockReport) -> bool:
    return cycles.is_cycle(report) and not cycles.targets_known(report)


def finding(report: DeadlockReport, signature: str, recurrence: dict, captured_at) -> Hallazgo:
    table_names = cycles.tables(report)
    victim = cycles.victim(report)
    return Hallazgo(
        antipatron=ANTIPATRON,
        severidad=cycles.severity(recurrence["count"]),
        evidencia=cycles.evidence(
            report, signature, recurrence, captured_at, _RISK,
            [_WHY_UNKNOWN.get(report.source, _WHY_UNKNOWN_DEFAULT)],
        ),
        explicacion=_explanation(report, table_names, recurrence["count"]),
        recomendacion=_recommendation(report, table_names),
        query_id=victim.query_id if victim and victim.query_id is not None else None,
        table_name=", ".join(table_names) if table_names else None,
    )


def _explanation(report: DeadlockReport, table_names: list[str], occurrences: int) -> str:
    return (
        f"{len(report.transactions)} transacciones formaron un abrazo mortal{cycles.where(table_names)}. "
        f"{cycles.resolution(report)} El informe no identifica que recurso "
        "esperaba cada una, asi que no se puede saber si tomaron las mismas "
        "filas en orden opuesto (CT-01) o si leyeron la misma fila en modo "
        "compartido y despues quisieron modificarla (CT-02)."
        f"{cycles.recurrence_text(occurrences)}"
    )


def _recommendation(report: DeadlockReport, table_names: list[str]) -> str:
    diagnosis = _DIAGNOSIS.get(report.source)
    return (
        "Un arreglo que cubre los dos casos: al inicio de la transaccion, "
        "bloquear todas las filas que va a modificar con SELECT ... FOR UPDATE, "
        "ordenadas por clave primaria. Para saber cual fue, revisar que hace "
        f"antes esa transaccion{cycles.where(table_names, 'sobre')}: si lee la misma fila con FOR "
        "SHARE, es conversion de modo (CT-02); si modifica varias filas en un "
        "orden que depende de los datos, es orden inconsistente (CT-01). "
        f"{diagnosis + ' ' if diagnosis else ''}{cycles.retry_text(report)}"
    )
