"""
QUERYLENS — manifiestos del banco de pruebas (SegundoInforme 3.3 y Tabla 7).

Cada escenario de inyeccion provoca una patologia conocida. Su manifiesto es
la verdad por construccion contra la que se comparan los hallazgos:

  esperados    lo que QueryLens tiene que encontrar. Un esperado que aparece en
               una repeticion es un verdadero positivo (VP) de su patologia; si
               no aparece, un falso negativo (FN).
  colaterales  hallazgos de OTRA patologia que la misma inyeccion produce por
               construccion (p. ej. al eliminar un indice, la consulta pasa a
               recorrer toda la tabla). Son ciertos, no son el objetivo del
               escenario: no cuentan como VP ni como falso positivo (FP).
  LINEA_BASE   hallazgos propios de la carga base (sysbench), por motor. Salen
               con o sin inyeccion y tampoco cuentan como FP (informe 3.3).
  DURANTE_INYECCION
               colaterales de cualquier inyeccion sobre la carga base (AP-01
               sobre sentencias de sysbench, que la inyeccion ralentiza de
               verdad). Solo valen en corridas con inyeccion; en las de
               control cuentan como FP.

Todo hallazgo que no coincide con nada de lo anterior es un FP de su
patologia, en las corridas con inyeccion y en las de control.

Son 22 escenarios para 24 combinaciones patologia-motor: 7 de antipatrones
por motor (AP-02 se evalua dentro del de AP-04) y 8 de contencion (3 en
Postgres, 5 en MySQL).

Un criterio es un dict que se compara con el hallazgo (las columnas de
public.hallazgos): cada clave tiene que coincidir. "evidencia" compara claves
de la evidencia, y un valor puede ser una condicion:
  {"max": 0.1}            numero <= 0.1
  {"en": ["a", "b"]}      alguno de la lista
  {"prefijo": "sbtest"}   texto que empieza asi
  {"contiene": "ql_scn"}  texto (o JSON del valor) que lo contiene
"evidencia_contiene" busca un texto en toda la evidencia.

Los escenarios de antipatrones son los de ql_sandbox/scenarios/run_scenario.py
(misma tabla, misma carga) y los de contencion, los casos de
ql_sandbox/mediciones/measure_ct.py.
"""

import json
from dataclasses import dataclass, field

ENGINES = ("postgres", "mysql")
# Orden en que la campana corre los motores con --engine both (el reporte
# sigue el de ENGINES)
ENGINE_RUN_ORDER = ("mysql", "postgres")

# codigo -> antipatron del hallazgo (detection_engine/catalog.py)
ANTIPATRON = {
    "AP-01": "baseline_degradation",
    "AP-02": "avoidable_full_scan",
    "AP-03": "non_sargable_predicate",
    "AP-04": "missing_index",
    "AP-05": "unused_index",
    "AP-06": "n_plus_one",
    "AP-07": "disk_spill",
    "AP-08": "cardinality_misestimate",
    "CT-01": "inconsistent_lock_order_deadlock",
    "CT-02": "lock_mode_conversion_deadlock",
    "CT-03": "range_contention_deadlock",
    "CT-04": "lock_amplification_deadlock",
    "CT-05": "long_transaction_cascade",
}
CODIGO = {antipatron: codigo for codigo, antipatron in ANTIPATRON.items()}
# El abrazo que no se pudo clasificar no es una patologia del catalogo
UNCLASSIFIED = "unclassified_deadlock"

NOMBRE = {
    "AP-01": "degradacion respecto a la linea base",
    "AP-02": "escaneo completo evitable",
    "AP-03": "predicado no sargable",
    "AP-04": "indice ausente",
    "AP-05": "indice no utilizado o redundante",
    "AP-06": "patron N+1",
    "AP-07": "vertido a disco",
    "AP-08": "error grave de estimacion de cardinalidad",
    "CT-01": "abrazo mortal por orden inconsistente",
    "CT-02": "abrazo mortal por conversion de modo",
    "CT-03": "abrazo mortal por contencion de rango",
    "CT-04": "ampliacion de bloqueos por ausencia de indice",
    "CT-05": "cascada por transaccion larga",
}

# ------------------------------------------------------------------
# Umbrales de la Tabla 7 (no se modifican durante el proyecto)
# ------------------------------------------------------------------

M1_MIN = 0.90  # exhaustividad por combinacion
M2_MIN = 0.90  # precision por combinacion
M3_MEMBERS_MIN = 0.95  # ciclos o cadenas con transacciones y recursos correctos
M3_CLASS_MIN = 0.90  # ciclos o cadenas con el patron correcto
# M4, antipatrones: "<= 2 ventanas (120 s)". El informe se escribio con
# ventanas de 60 s; con las de 10 s se mantiene el limite en segundos, y la
# latencia se informa tambien en ventanas.
M4_AP_S = 120
WINDOW_S = 10
# M4, AP-05: "cierre de la ventana de observacion". El banco espera a que el
# motor lleve encendido la ventana minima de estadisticas (30 min) antes de
# inyectar, asi que el hallazgo tiene que salir con el mismo limite.
M4_AP05_S = 120
AP05_STATS_WINDOW_MIN = 30
# M4, contencion: desde el abrazo (CT-01 a CT-04) o desde que la raiz cumple
# 30 s (CT-05) hasta que el hallazgo queda guardado
M4_DEADLOCK_S = 15
M4_CASCADE_S = 45


@dataclass(frozen=True)
class Expected:
    codigo: str
    descripcion: str
    criterio: dict
    engines: tuple = ENGINES


@dataclass(frozen=True)
class Collateral:
    descripcion: str  # por que sale por construccion
    criterio: dict
    engines: tuple = ENGINES


@dataclass(frozen=True)
class Manifest:
    scenario: str  # nombre en run_scenario.py
    descripcion: str
    expected: tuple
    collateral: tuple = field(default_factory=tuple)

    def codes(self) -> list[str]:
        return sorted({e.codigo for e in self.expected})


def _unused(index_name: str, subtipo: str) -> dict:
    return {"antipatron": "unused_index", "evidencia": {"index_name": index_name, "subtipo": subtipo}}


# Cada escenario crea ql_scn de nuevo con su indice sobre k (run_scenario.py,
# SETUP_SQL). Si la carga del escenario consulta ql_scn sin leer ese indice, el
# indice recien creado tiene 0 lecturas y AP-05 lo reporta como no usado: es
# cierto por construccion.
K_INDEX_UNUSED = Collateral(
    "AP-05: ql_scn_k, recien creado, no lo lee la carga del escenario",
    {"antipatron": "unused_index", "table_name": "ql_scn",
     "evidencia": {"index_name": "ql_scn_k", "subtipo": "no_usado"}},
)


# ------------------------------------------------------------------
# Escenarios de antipatrones (7 por motor)
# ------------------------------------------------------------------

AP_MANIFESTS = {
    m.scenario: m for m in [
        Manifest(
            scenario="baseline_degradation",
            descripcion=(
                "AP-01: 10 min de linea base con SELECT pad FROM ql_scn WHERE k = ? y despues se "
                "elimina el indice ql_scn_k. La inyeccion empieza al eliminar el indice."
            ),
            expected=(
                Expected("AP-01", "baseline_degradation sobre la consulta por k",
                         {"antipatron": "baseline_degradation", "evidencia": {"query_text": {"contiene": "ql_scn"}}}),
            ),
            collateral=(
                Collateral("AP-02: sin el indice, WHERE k = ? recorre toda la tabla para devolver ~10 filas",
                           {"antipatron": "avoidable_full_scan", "table_name": "ql_scn"}),
                Collateral("AP-04: sin el indice, k se filtra sin un indice que lo respalde",
                           {"antipatron": "missing_index", "table_name": "ql_scn",
                            "evidencia": {"equality_columns": ["k"]}}),
                Collateral("AP-08: MySQL estima un 10 % fijo para k = ? sin indice ni histograma (~100.000 "
                           "filas frente a ~10)", {"antipatron": "cardinality_misestimate", "table_name": "ql_scn"},
                           engines=("mysql",)),
            ),
        ),
        Manifest(
            scenario="missing_index",
            descripcion=(
                "AP-04 y AP-02: SELECT id, pad FROM ql_scn WHERE c = ? sobre 1.000.000 de filas, sin "
                "indice en c (~10 filas por ejecucion)."
            ),
            expected=(
                Expected("AP-04", "missing_index sobre ql_scn.c con la recomendacion de un indice sobre c", {
                    "antipatron": "missing_index", "table_name": "ql_scn",
                    "evidencia": {"equality_columns": ["c"], "suggested_index": "CREATE INDEX ON ql_scn (c)"},
                }),
                Expected("AP-02", "avoidable_full_scan sobre ql_scn con la fraccion real de filas devueltas", {
                    "antipatron": "avoidable_full_scan", "table_name": "ql_scn",
                    "evidencia": {"access_method": "full_table_scan", "selectivity": {"max": 0.1}},
                }),
            ),
            collateral=(
                K_INDEX_UNUSED,
                Collateral("AP-08: MySQL estima un 10 % fijo para c = ? sin indice ni histograma (~100.000 "
                           "filas frente a ~10)", {"antipatron": "cardinality_misestimate", "table_name": "ql_scn"},
                           engines=("mysql",)),
            ),
        ),
        Manifest(
            scenario="non_sargable_predicate",
            descripcion="AP-03: SELECT id, pad FROM ql_scn WHERE k + 0 = ?, con indice en k.",
            expected=(
                Expected("AP-03", "non_sargable_predicate arithmetic_on_column sobre ql_scn.k", {
                    "antipatron": "non_sargable_predicate", "table_name": "ql_scn",
                    "evidencia": {"column": "k", "subtipo": "arithmetic_on_column"},
                }),
            ),
            collateral=(
                K_INDEX_UNUSED,
                Collateral("AP-02: k + 0 = ? no puede usar el indice y recorre toda la tabla para devolver "
                           "~10 filas (misma base de escaneo completo que AP-03)",
                           {"antipatron": "avoidable_full_scan", "table_name": "ql_scn"}),
                Collateral("AP-08: ningun motor estima bien una expresion sobre la columna (Postgres 0,5 % "
                           "por defecto, MySQL casi toda la tabla) frente a ~10 filas reales",
                           {"antipatron": "cardinality_misestimate", "table_name": "ql_scn"}),
            ),
        ),
        Manifest(
            scenario="unused_index",
            descripcion=(
                "AP-05: indice sobre pad que nadie consulta, duplicado exacto de k e indice c "
                "cubierto por (c, k), con consultas que leen con k y con (c, k)."
            ),
            expected=(
                Expected("AP-05", "redundante: ql_scn_c es prefijo de ql_scn_c_k", _unused("ql_scn_c", "redundante")),
                Expected("AP-05", "redundante: ql_scn_k_dup duplica a ql_scn_k (o al reves)", {
                    "antipatron": "unused_index",
                    "evidencia": {"index_name": {"en": ["ql_scn_k_dup", "ql_scn_k"]}, "subtipo": "redundante"},
                }),
                Expected("AP-05", "no_usado: ql_scn_pad", _unused("ql_scn_pad", "no_usado")),
            ),
        ),
        Manifest(
            scenario="n_plus_one",
            descripcion=(
                "AP-06: lee 50 clientes y consulta los pedidos de cada uno, y lee 50 pedidos y "
                "consulta el cliente de cada uno, en bucle."
            ),
            expected=(
                Expected("AP-06", "n_plus_one uno_a_muchos: ql_n1_orders WHERE customer_id = ?", {
                    "antipatron": "n_plus_one", "table_name": "ql_n1_orders",
                    "evidencia": {"direccion": "uno_a_muchos"},
                }),
                Expected("AP-06", "n_plus_one muchos_a_uno: ql_n1_customers WHERE id = ?", {
                    "antipatron": "n_plus_one", "table_name": "ql_n1_customers",
                    "evidencia": {"direccion": "muchos_a_uno"},
                }),
            ),
        ),
        Manifest(
            scenario="disk_spill",
            descripcion="AP-07: GROUP BY de 100.000 grupos sobre ql_scn con la memoria de trabajo reducida.",
            expected=(
                Expected("AP-07", "disk_spill sobre la consulta de ql_scn",
                         {"antipatron": "disk_spill", "evidencia": {"tables": {"contiene": "ql_scn"}}}),
            ),
            collateral=(K_INDEX_UNUSED,),
        ),
        Manifest(
            scenario="cardinality_misestimate",
            descripcion=(
                "AP-08: WHERE a = ? AND b = ? AND c = ? sobre 100.000 filas con a = b = c "
                "(~100 estimadas frente a ~10.000 reales)."
            ),
            expected=(
                Expected("AP-08", "cardinality_misestimate sobre ql_scn_corr",
                         {"antipatron": "cardinality_misestimate", "table_name": "ql_scn_corr"}),
            ),
            collateral=(
                Collateral("AP-02: a, b y c no tienen indice y la consulta devuelve el 10 % de la tabla, justo "
                           "el limite de la regla", {"antipatron": "avoidable_full_scan", "table_name": "ql_scn_corr"}),
                Collateral("AP-04: igual, sin indice sobre a, b ni c",
                           {"antipatron": "missing_index", "table_name": "ql_scn_corr"}),
            ),
        ),
    ]
}

# Orden de ejecucion: AP-05 primero, luego los cortos y el de linea base al
# final. La consulta del escenario de AP-05 es liviana y el pipeline elige las
# candidatas por tiempo acumulado, asi que va justo despues de poner en cero las
# sentencias (limitacion declarada en el informe). "No utilizado" ademas
# necesita la ventana de 30 min de los indices: run_engine espera si hace falta.
AP_ORDER = (
    "unused_index", "missing_index", "non_sargable_predicate", "cardinality_misestimate", "n_plus_one",
    "disk_spill", "baseline_degradation",
)

# ------------------------------------------------------------------
# Escenarios de contencion: caso de measure_ct.py (fase "positivo") por motor
# ------------------------------------------------------------------

CT_CASES = {
    "postgres": {"CT-01": "ct01", "CT-02": "ct02", "CT-05": "ct05"},
    "mysql": {"CT-01": "ct01", "CT-02": "ct02", "CT-03": "ct03", "CT-04": "ct04", "CT-05": "ct05"},
}

# ------------------------------------------------------------------
# Linea base: hallazgos propios de la carga base, por motor
# ------------------------------------------------------------------

# sysbench crea un indice k_N en cada tabla sbtestN y oltp_read_write no lo usa
# para leer (solo lo actualiza): son los "indices de sysbench que la carga no
# consulta" del informe.
_SYSBENCH_K_UNUSED = Collateral(
    "AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer",
    {"antipatron": "unused_index", "table_name": {"prefijo": "sbtest"},
     "evidencia": {"index_name": {"prefijo": "k_"}, "subtipo": "no_usado"}},
)

LINEA_BASE: dict[str, tuple] = {
    "postgres": (_SYSBENCH_K_UNUSED,),
    "mysql": (_SYSBENCH_K_UNUSED,),
}


# ------------------------------------------------------------------
# Colaterales de cualquier inyeccion sobre la carga base
# ------------------------------------------------------------------

# Mientras corre una inyeccion (escenario de antipatrones o bloque de
# contencion), esta compite con sysbench por CPU, E/S y bloqueos, y las
# sentencias de sysbench se vuelven de verdad mas lentas que su linea base:
# AP-01 lo reporta y es cierto. No es el objetivo del escenario, asi que no
# cuenta como VP ni como FP. En las corridas de control no hay inyeccion: ahi
# un AP-01 sobre sysbench si es un FP.
_SYSBENCH_DEGRADED_BY_INJECTION = Collateral(
    "AP-01: la inyeccion compite con sysbench por CPU y bloqueos y degrada sus sentencias",
    {"antipatron": "baseline_degradation", "evidencia": {"query_text": {"contiene": "sbtest"}}},
)

DURANTE_INYECCION: dict[str, tuple] = {
    "postgres": (_SYSBENCH_DEGRADED_BY_INJECTION,),
    "mysql": (_SYSBENCH_DEGRADED_BY_INJECTION,),
}


def combinations() -> list[tuple[str, str]]:
    """Las 24 combinaciones patologia-motor de la Tabla 7."""
    combos = [(codigo, engine) for engine in ENGINES for codigo in sorted(ANTIPATRON) if codigo.startswith("AP")]
    combos += [(codigo, engine) for engine in ENGINES for codigo in sorted(CT_CASES[engine])]
    return combos


def scenario_count() -> int:
    return len(AP_MANIFESTS) * len(ENGINES) + sum(len(cases) for cases in CT_CASES.values())


# ------------------------------------------------------------------
# Comparacion de un hallazgo con un criterio
# ------------------------------------------------------------------

def _value_ok(actual, expected) -> bool:
    if not isinstance(expected, dict):
        return actual == expected
    for op, operand in expected.items():
        if op == "max":
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or actual > operand:
                return False
        elif op == "en":
            if actual not in operand:
                return False
        elif op == "prefijo":
            if not isinstance(actual, str) or not actual.startswith(operand):
                return False
        elif op == "contiene":
            text = actual if isinstance(actual, str) else json.dumps(actual, default=str)
            if actual is None or operand not in text:
                return False
        else:
            raise ValueError(f"condicion desconocida en el manifiesto: {op}")
    return True


def matches(criterio: dict, finding: dict) -> bool:
    evidence = finding.get("evidencia") or {}
    for key, expected in criterio.items():
        if key == "evidencia":
            if not all(_value_ok(evidence.get(name), value) for name, value in expected.items()):
                return False
        elif key == "evidencia_contiene":
            if expected not in json.dumps(evidence, default=str):
                return False
        elif not _value_ok(finding.get(key), expected):
            return False
    return True


def classify(finding: dict, engine: str, manifest: Manifest | None,
             injected: bool = False) -> tuple[str, object]:
    """Que es un hallazgo dentro de una corrida: ("esperado", Expected),
    ("colateral", Collateral), ("linea_base", Collateral) o ("no_declarado", None).
    manifest es None en las corridas sin escenario de antipatrones (control,
    bloque de contencion). injected: la corrida tuvo una inyeccion (escenario
    de antipatrones o bloque de contencion); en las de control es False."""
    if manifest is not None:
        for expected in manifest.expected:
            if engine in expected.engines and matches(expected.criterio, finding):
                return "esperado", expected
        for collateral in manifest.collateral:
            if engine in collateral.engines and matches(collateral.criterio, finding):
                return "colateral", collateral
    if injected:
        for collateral in DURANTE_INYECCION[engine]:
            if matches(collateral.criterio, finding):
                return "colateral", collateral
    for base in LINEA_BASE[engine]:
        if matches(base.criterio, finding):
            return "linea_base", base
    return "no_declarado", None
