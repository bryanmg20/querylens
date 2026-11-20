"""Estado de evaluacion por patologia (SegundoInforme 6.1, 6.3 y Tabla 6).

Para cada snapshot dice, de cada una de las 13 patologias del catalogo, si la
regla se pudo evaluar. "Sin hallazgos" y "no evaluada" no son lo mismo: sin
esto, la ausencia de un hallazgo se lee como la ausencia del problema.

Tres estados:

  evaluada     la regla tuvo lo que necesita
  parcial      se evaluo, pero una parte de lo que tenia que mirar quedo
               afuera (consultas sin plan, 'no utilizado' de AP-05 con la
               ventana de estadisticas recien empezada...). Puede haber
               hallazgos; no estan todos los que podria haber
  no_evaluada  no se pudo evaluar nada en esta ventana

Cada estado que no es "evaluada" lleva sus motivos. Hay dos origenes:

  recoleccion  un problema del estado de la recoleccion que viaja en el
               snapshot (SnapshotPayload.collection_status): una seccion que
               no se pudo leer o un codigo del catalogo del pipeline
               (telemetry_pipeline/health/catalog.py). Trae su mensaje y su
               accion de remedio ya redactados
  detector     una condicion de la propia regla que no es un problema de la
               recoleccion (Tabla 6): linea base sin completar, ventana de
               estadisticas corta, plan que no viene de una ejecucion real

Solo se registran condiciones de la patologia completa. Las de una sentencia
suelta (su ventana no se pudo medir, su plan fallo) entran como conteo en
`detalle` y, si son todas o una parte, como estado no_evaluada o parcial.

Todo sale del snapshot y de la historia que el detector ya tenia: la misma
entrada da el mismo estado. Lo que el pipeline calcula despues de encolar
(LOG_WINDOW_OVERRUN, DEADLOCKS_MISSED) no llega aca: queda en la salud de la
extraccion. Y si no hubo snapshot (problema bloqueante de conexion) el
detector no corre: ese caso lo deduce la API de pipeline_health.

Un snapshot sin collection_status (anterior a ese campo) se evalua solo con
las condiciones del detector: no se sabe si alguna seccion fallo.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

from querylens_contract import SnapshotPayload

from anti_patterns import avoidable_full_scan, cardinality_misestimate, missing_index, non_sargable_predicate
from anti_patterns.baseline_degradation import DEFAULT_BASELINE_WINDOW
from anti_patterns.statement_history import WINDOW_MIN_CALLS
from anti_patterns.unused_index import DEFAULT_MIN_STATS_WINDOW, _stats_window_is_fresh
from catalog import CODES
from models import Hallazgo, StatementHistory

EVALUADA = "evaluada"
PARCIAL = "parcial"
NO_EVALUADA = "no_evaluada"

ORIGEN_RECOLECCION = "recoleccion"
ORIGEN_DETECTOR = "detector"


@dataclass(frozen=True)
class Reason:
    """Por que una patologia no se evaluo (o se evaluo en parte) y que hacer."""

    codigo: str
    origen: str
    mensaje: str
    accion: str
    # solo recoleccion: blocking | degraded | info
    severidad: str | None = None
    # solo recoleccion: seccion del snapshot que no se pudo leer ("" si no es de una)
    seccion: str = ""

    def to_dict(self) -> dict:
        return {
            "codigo": self.codigo,
            "origen": self.origen,
            "severidad": self.severidad,
            "seccion": self.seccion,
            "mensaje": self.mensaje,
            "accion": self.accion,
        }


@dataclass
class PathologyState:
    codigo: str
    antipatron: str
    estado: str = EVALUADA
    motivos: list[Reason] = field(default_factory=list)
    # lo que la regla tuvo para mirar en esta ventana (conteos); informativo
    detalle: dict = field(default_factory=dict)
    # True: a la regla le falto una fuente o le llego una que no es confiable
    # (una seccion que no se pudo leer, contadores de uso apagados). Lo que
    # haya detectado en esta ventana no vale: process_job lo descarta
    descartar_hallazgos: bool = False

    @property
    def firma(self) -> str:
        """Los motivos en una linea, sin repetir y ordenados. Con el estado,
        identifica la fila de la patologia dentro de un tramo."""
        parts = {f"{r.codigo}@{r.seccion}" if r.seccion else r.codigo for r in self.motivos}
        return ",".join(sorted(parts))


# ------------------------------------------------- motivos propios del detector

_BASELINE_MINUTES = DEFAULT_BASELINE_WINDOW * 10 // 60

LINEA_BASE_EN_CONSTRUCCION = Reason(
    codigo="LINEA_BASE_EN_CONSTRUCCION",
    origen=ORIGEN_DETECTOR,
    mensaje=(
        "Ninguna consulta tiene todavia su linea base completa: la regla compara cada consulta "
        f"con sus {DEFAULT_BASELINE_WINDOW} ventanas anteriores ({_BASELINE_MINUTES} minutos), "
        f"con al menos {WINDOW_MIN_CALLS} ejecuciones en cada una."
    ),
    accion=(
        "No requiere accion: se evalua sola cuando una consulta acumula ese historial. Una "
        f"consulta que se ejecuta menos de {WINDOW_MIN_CALLS} veces por ventana nunca lo completa."
    ),
)

SIN_PLAN = Reason(
    codigo="SIN_PLAN",
    origen=ORIGEN_DETECTOR,
    mensaje=(
        "Hay consultas candidatas sin plan de ejecucion en esta ventana: sin el plan no se sabe "
        "si recorren la tabla completa."
    ),
    accion=(
        "Revisar en la salud de la extraccion si hay problemas de EXPLAIN abiertos. Una "
        "sentencia con varias instrucciones y, en MySQL, una que no deja una muestra de su "
        "texto no se pueden explicar."
    ),
)

SIN_FILAS_REALES = Reason(
    codigo="SIN_FILAS_REALES",
    origen=ORIGEN_DETECTOR,
    mensaje=(
        "Hay consultas candidatas cuyo plan no viene de una ejecucion real (plan generico de "
        "Postgres o muestra de MySQL): sin las filas que devolvio esa ejecucion no se puede "
        "medir el error de estimacion."
    ),
    accion=(
        "Postgres: instalar auto_explain con log_analyze (guia de instalacion); solo quedan en "
        "el log las ejecuciones que superan auto_explain.log_min_duration. MySQL: se evalua "
        "cuando la ejecucion sigue en events_statements_history."
    ),
)

SIN_CLAVES_FORANEAS = Reason(
    codigo="SIN_CLAVES_FORANEAS",
    origen=ORIGEN_DETECTOR,
    mensaje=(
        "La base no declara claves foraneas: la regla reconoce el N+1 por la clave foranea que "
        "une la consulta padre con la hija."
    ),
    accion=(
        "Declarar las claves foraneas de las relaciones. Sin ellas QueryLens no puede evaluar "
        "este patron en esta base."
    ),
)


def _short_stats_window(min_stats_window: timedelta) -> Reason:
    minutes = int(min_stats_window.total_seconds() // 60)
    return Reason(
        codigo="VENTANA_ESTADISTICAS_CORTA",
        origen=ORIGEN_DETECTOR,
        mensaje=(
            f"Las estadisticas de uso de los indices cuentan desde hace menos de {minutes} "
            "minutos (arranque del servidor o reinicio de estadisticas): 'no utilizado' todavia "
            "no se evalua; 'redundante' si."
        ),
        accion="No requiere accion: se evalua sola cuando la ventana de estadisticas llega al minimo.",
    )


def _unread_section(section: str) -> Reason:
    # la seccion fallo y el estado de la recoleccion no trae un problema propio
    # de ella (el pipeline lo descarto por otro mas preciso)
    return Reason(
        codigo="SECCION_NO_LEIDA",
        origen=ORIGEN_DETECTOR,
        seccion=section,
        mensaje=f"La recoleccion no pudo leer la seccion '{section}' de la telemetria en este ciclo.",
        accion="Revisar los problemas abiertos en la salud de la extraccion.",
    )


# --------------------------------------------- codigos del estado de la recoleccion
#
# Son codigos de telemetry_pipeline/health/catalog.py. Si alla se renombra uno,
# aca deja de reconocerse: tests/test_evaluation.py comprueba que existan.

# Sin estadisticas por sentencia no se evalua ningun antipatron.
_NO_STATEMENTS_CODES = (
    "PERFORMANCE_SCHEMA_OFF",
    # Postgres oculta el texto y el queryid de las sentencias de otros roles:
    # las del rol de monitoreo no cuentan, asi que no queda ninguna
    "MISSING_PG_READ_ALL_STATS",
)
# Consumers de performance_schema apagados: los digests dejan de actualizarse o
# faltan las muestras. Con sentencias en el snapshot, se evalua lo que haya.
_STALE_STATEMENTS_CODE = "PS_CONSUMER_DISABLED"

# Por que una candidata puede no tener plan.
PLAN_CODES = (
    "EXPLAIN_PERMISSION_DENIED",
    "EXPLAIN_FOREIGN_DB_CONNECT_DENIED",
    "EXPLAIN_FAILED",
    "EXPLAIN_PARAM_TYPES_AMBIGUOUS",
    "NO_EXPLAINABLE_CANDIDATES",
    "QUERY_TEXT_TRUNCATED",
    "PG_VERSION_LIMITED",
)
# Sin el log de auto_explain los planes de Postgres son genericos: no traen las
# filas de una ejecucion, y AP-08 no tiene respaldo.
_NO_ACTUAL_ROWS_CODE = "AUTO_EXPLAIN_LOG_UNAVAILABLE"

# Los contadores de uso de indices no avanzan: todos parecen sin uso.
_NO_INDEX_USAGE_CODES = ("TRACK_COUNTS_OFF",)

_NO_DEADLOCK_REPORTS_CODES = ("DEADLOCK_REPORTS_UNAVAILABLE",)
_PARTIAL_DEADLOCK_REPORTS_CODES = ("DEADLOCK_REPORTS_PARTIAL",)
# Postgres sin log_lock_waits: con sentencias preparadas no se sabe que fila
# esperaba cada proceso y el abrazo queda sin clasificar entre CT-01 y CT-02.
_UNDISTINGUISHED_DEADLOCK_CODES = ("DEADLOCK_ROW_WAITS_UNLOGGED",)

# Sin ver las sesiones de otros usuarios no hay cadena de espera que medir.
_NO_SESSIONS_CODES = ("MISSING_PROCESS_PRIVILEGE", "MISSING_PG_READ_ALL_STATS")

# Todos los codigos de arriba, para el test que los cruza con el catalogo.
COLLECTION_CODES = (
    *_NO_STATEMENTS_CODES,
    _STALE_STATEMENTS_CODE,
    *PLAN_CODES,
    _NO_ACTUAL_ROWS_CODE,
    *_NO_INDEX_USAGE_CODES,
    *_NO_DEADLOCK_REPORTS_CODES,
    *_PARTIAL_DEADLOCK_REPORTS_CODES,
    *_UNDISTINGUISHED_DEADLOCK_CODES,
    *_NO_SESSIONS_CODES,
)


class _Collection:
    """El estado de la recoleccion del snapshot, indexado. Sin estado (None)
    no afirma nada: ninguna seccion consta como fallida."""

    def __init__(self, snapshot: SnapshotPayload):
        status = snapshot.collection_status
        self.known = status is not None
        self.failed = set(status.sections_failed) if status else set()
        self.issues = list(status.issues) if status else []

    @staticmethod
    def _reason(issue) -> Reason:
        return Reason(
            codigo=issue.code,
            origen=ORIGEN_RECOLECCION,
            severidad=issue.severity,
            seccion=issue.section,
            mensaje=issue.message,
            accion=issue.remediation,
        )

    def codes(self, *codes: str) -> list[Reason]:
        """Motivos por los problemas abiertos con alguno de esos codigos."""
        return [self._reason(issue) for issue in self.issues if issue.code in codes]

    def sections(self, *sections: str) -> list[Reason]:
        """Motivos por las secciones que la regla necesita y no se pudieron leer."""
        reasons = []
        for section in sections:
            if section not in self.failed:
                continue
            own = [self._reason(issue) for issue in self.issues if issue.section == section]
            reasons += own or [_unread_section(section)]
        return reasons


def _unique(reasons: list[Reason]) -> list[Reason]:
    seen, out = set(), []
    for reason in reasons:
        key = (reason.codigo, reason.seccion)
        if key not in seen:
            seen.add(key)
            out.append(reason)
    return out


def _state(
    antipatron: str,
    blind: list[Reason],
    partial: list[Reason],
    detalle: dict | None = None,
    discard: bool = False,
) -> PathologyState:
    state = PathologyState(codigo=CODES[antipatron], antipatron=antipatron, detalle=detalle or {})
    if blind:
        state.estado = NO_EVALUADA
        state.motivos = _unique(blind)
        state.descartar_hallazgos = discard
    elif partial:
        state.estado = PARCIAL
        state.motivos = _unique(partial)
    return state


# ----------------------------------------------------------- lo comun a los AP


def _statements_blind(collection: _Collection, snapshot: SnapshotPayload) -> tuple[list[Reason], bool]:
    """(motivos por los que no hay estadisticas por sentencia, si descarta).
    Descarta solo cuando la seccion no se pudo leer."""
    by_section = collection.sections("statements")
    reasons = by_section + collection.codes(*_NO_STATEMENTS_CODES)
    if not snapshot.statements:
        reasons += collection.codes(_STALE_STATEMENTS_CODE)
    return reasons, bool(by_section)


def _statements_partial(collection: _Collection, snapshot: SnapshotPayload) -> list[Reason]:
    return collection.codes(_STALE_STATEMENTS_CODE) if snapshot.statements else []


# El mismo texto llega en cada snapshot: se parsea una vez por regla.
@lru_cache(maxsize=4096)
def _is_simple_select(rule: str, canonic_query: str | None) -> bool:
    # Cada regla con su propia condicion 1 (son copias a proposito): si una
    # cambia, su estado de evaluacion la sigue
    modules = {
        "avoidable_full_scan": avoidable_full_scan,
        "non_sargable_predicate": non_sargable_predicate,
        "missing_index": missing_index,
    }
    return modules[rule]._simple_select_table(canonic_query) is not None


@lru_cache(maxsize=4096)
def _is_cardinality_candidate(canonic_query: str | None) -> bool:
    # las formas que AP-08 excluye por diseno (LIMIT, GROUP BY, agregados...) no
    # cuentan como no evaluadas
    select = cardinality_misestimate._parse_select(canonic_query)
    return cardinality_misestimate._exclusion(select) is None


def _plan_coverage(
    rule: str, snapshot: SnapshotPayload, collection: _Collection,
) -> tuple[list[Reason], list[Reason], dict]:
    """(motivos si ninguna tiene plan, motivos si a algunas les falta, detalle)
    de las candidatas que la regla evaluaria: los SELECT simples de
    top_impact_queries. Las que no son SELECT simples quedan fuera por diseno."""
    with_plan = {explain.query_id for explain in snapshot.canonic_explains if explain.query_id is not None}
    candidates = [
        candidate for candidate in snapshot.top_impact_queries
        if candidate.query_id is not None and _is_simple_select(rule, candidate.canonic_query)
    ]
    missing = [candidate for candidate in candidates if candidate.query_id not in with_plan]
    detalle = {"candidatas": len(candidates), "sin_plan": len(missing)}
    if not missing:
        return [], [], detalle
    reasons = collection.codes(*PLAN_CODES) or [SIN_PLAN]
    if len(missing) == len(candidates):
        return reasons, [], detalle
    return [], reasons, detalle


# ------------------------------------------------------------------ por regla


def _baseline_degradation(snapshot, history, collection) -> PathologyState:  # AP-01
    blind, discard = _statements_blind(collection, snapshot)
    with_baseline = sum(1 for row in history.values() if len(row.window_means_ms) >= DEFAULT_BASELINE_WINDOW)
    if not blind and not with_baseline:
        blind = [LINEA_BASE_EN_CONSTRUCCION]
    return _state(
        "baseline_degradation", blind, _statements_partial(collection, snapshot),
        {"consultas_con_linea_base": with_baseline}, discard,
    )


def _full_scan_rule(
    rule: str, sections: tuple[str, ...], snapshot, collection, partial_sections: tuple[str, ...] = (),
) -> PathologyState:
    # AP-02, AP-03 y AP-04: misma base de escaneo completo. partial_sections:
    # las que la regla lee para afinar, no para decidir; sin ellas sigue
    # detectando, pero con menos certeza
    blind, discard = _statements_blind(collection, snapshot)
    by_section = collection.sections(*sections)
    blind += by_section
    discard = discard or bool(by_section)
    no_plan, some_plan, detalle = _plan_coverage(rule, snapshot, collection)
    if not blind:
        blind = no_plan
    partial = _statements_partial(collection, snapshot) + some_plan + collection.sections(*partial_sections)
    return _state(rule, blind, partial, detalle, discard)


def _unused_index(snapshot, collection, captured_at, min_stats_window) -> PathologyState:  # AP-05
    blind, discard = _statements_blind(collection, snapshot)
    # sin indices no hay nada que mirar; sin el arranque del servidor no se sabe
    # si la ventana de estadisticas alcanza; sin las claves foraneas se
    # reportaria como sin uso el indice que respalda una
    unreliable = collection.sections("indexes", "server_start_timestamp", "foreign_keys")
    unreliable += collection.codes(*_NO_INDEX_USAGE_CODES)
    blind += unreliable
    partial = _statements_partial(collection, snapshot)
    if _stats_window_is_fresh(snapshot, min_stats_window, captured_at):
        partial.append(_short_stats_window(min_stats_window))
    return _state(
        "unused_index", blind, partial, {"indices": len(snapshot.indexes)}, discard or bool(unreliable),
    )


def _n_plus_one(snapshot, collection) -> PathologyState:  # AP-06
    blind, discard = _statements_blind(collection, snapshot)
    by_section = collection.sections("foreign_keys")
    blind += by_section
    if not blind and not snapshot.foreign_keys:
        blind = [SIN_CLAVES_FORANEAS]
    return _state(
        "n_plus_one", blind, _statements_partial(collection, snapshot),
        {"claves_foraneas": len({(row.schema_name, row.table_name, row.constraint_name)
                                 for row in snapshot.foreign_keys})},
        discard or bool(by_section),
    )


def _disk_spill(snapshot, collection) -> PathologyState:  # AP-07
    blind, discard = _statements_blind(collection, snapshot)
    return _state("disk_spill", blind, _statements_partial(collection, snapshot), {}, discard)


def _cardinality_misestimate(snapshot, collection) -> PathologyState:  # AP-08
    blind, discard = _statements_blind(collection, snapshot)
    explains = {explain.query_id: explain for explain in snapshot.canonic_explains if explain.query_id is not None}
    candidates = [
        candidate for candidate in snapshot.top_impact_queries
        if candidate.query_id is not None and _is_cardinality_candidate(candidate.canonic_query)
    ]
    without_plan = [c for c in candidates if c.query_id not in explains]
    without_rows = [
        c for c in candidates
        if c.query_id in explains and explains[c.query_id].canonical_plan.estimates.actual_rows is None
    ]
    detalle = {
        "candidatas": len(candidates),
        "sin_plan": len(without_plan),
        "sin_filas_reales": len(without_rows),
    }
    reasons: list[Reason] = []
    if without_plan:
        reasons += collection.codes(*PLAN_CODES) or [SIN_PLAN]
    if without_rows:
        reasons += collection.codes(_NO_ACTUAL_ROWS_CODE) or [SIN_FILAS_REALES]
    partial = _statements_partial(collection, snapshot)
    not_evaluable = len(without_plan) + len(without_rows)
    if not_evaluable and not_evaluable == len(candidates):
        if not blind:
            blind = reasons
    else:
        partial += reasons
    return _state("cardinality_misestimate", blind, partial, detalle, discard)


def _deadlock_rule(
    rule: str, snapshot, collection, distinguishes_rows: bool, sections: tuple[str, ...] = (),
) -> PathologyState:
    # CT-01 a CT-04: salen del informe de abrazo mortal. distinguishes_rows: la
    # regla necesita saber que fila esperaba cada transaccion (CT-01 y CT-02)
    by_section = collection.sections(*sections)
    blind = collection.codes(*_NO_DEADLOCK_REPORTS_CODES) + by_section
    partial = collection.codes(*_PARTIAL_DEADLOCK_REPORTS_CODES)
    if distinguishes_rows:
        partial += collection.codes(*_UNDISTINGUISHED_DEADLOCK_CODES)
    return _state(rule, blind, partial, {"informes": len(snapshot.deadlocks)}, bool(by_section))


def _long_transaction_cascade(snapshot, collection) -> PathologyState:  # CT-05
    by_section = collection.sections("active_queries")
    blind = by_section + collection.codes(*_NO_SESSIONS_CODES)
    return _state(
        "long_transaction_cascade", blind, [], {"sesiones": len(snapshot.active_queries)}, bool(by_section),
    )


def evaluate_pathologies(
    snapshot: SnapshotPayload,
    history: dict[str, StatementHistory] | None = None,
    captured_at: datetime | None = None,
    min_stats_window: timedelta = DEFAULT_MIN_STATS_WINDOW,
) -> list[PathologyState]:
    """El estado de las 13 patologias para este snapshot, en el orden del
    catalogo. `history` es la que el detector tenia antes de este snapshot, la
    misma que reciben las reglas."""
    history = history or {}
    collection = _Collection(snapshot)
    return [
        _baseline_degradation(snapshot, history, collection),
        _full_scan_rule("avoidable_full_scan", ("tables",), snapshot, collection),
        _full_scan_rule("non_sargable_predicate", ("tables", "indexes"), snapshot, collection),
        # AP-04 lee el tipo de cada columna para descartar una conversion
        # implicita; sin el, el hallazgo sale con tipo_de_columna_desconocido
        _full_scan_rule("missing_index", ("tables", "indexes"), snapshot, collection,
                        partial_sections=("columns",)),
        _unused_index(snapshot, collection, captured_at, min_stats_window),
        _n_plus_one(snapshot, collection),
        _disk_spill(snapshot, collection),
        _cardinality_misestimate(snapshot, collection),
        _deadlock_rule("inconsistent_lock_order_deadlock", snapshot, collection, distinguishes_rows=True),
        _deadlock_rule("lock_mode_conversion_deadlock", snapshot, collection, distinguishes_rows=True),
        _deadlock_rule("range_contention_deadlock", snapshot, collection, distinguishes_rows=False),
        # CT-04 lee los indices de la tabla para saber si falta uno
        _deadlock_rule("lock_amplification_deadlock", snapshot, collection, distinguishes_rows=False,
                       sections=("indexes",)),
        _long_transaction_cascade(snapshot, collection),
    ]


def drop_unreliable_findings(
    findings: list[Hallazgo], states: list[PathologyState],
) -> tuple[list[Hallazgo], list[Hallazgo]]:
    """(hallazgos que valen, hallazgos descartados). Se descartan los de las
    patologias a las que les falto una fuente o les llego una no confiable:
    una regla que no se pudo evaluar no opina en esa ventana. Sin esto, con la
    seccion de indices fallida AP-04 reportaria como ausente un indice que
    existe."""
    unreliable = {state.antipatron for state in states if state.descartar_hallazgos}
    kept = [finding for finding in findings if finding.antipatron not in unreliable]
    dropped = [finding for finding in findings if finding.antipatron in unreliable]
    return kept, dropped
