"""Catalogo de patologias: el codigo del informe (Tablas 2 y 3) de cada regla.

El resto del detector identifica cada patologia por su nombre (el `antipatron`
del hallazgo). El codigo se guarda junto al problema porque es el ultimo
criterio del orden por impacto (SegundoInforme 6.2) y la clave del estado de
evaluacion por patologia (6.1).
"""

# nombre de la regla -> codigo. En el orden del informe.
CODES: dict[str, str] = {
    "baseline_degradation": "AP-01",
    "avoidable_full_scan": "AP-02",
    "non_sargable_predicate": "AP-03",
    "missing_index": "AP-04",
    "unused_index": "AP-05",
    "n_plus_one": "AP-06",
    "disk_spill": "AP-07",
    "cardinality_misestimate": "AP-08",
    "inconsistent_lock_order_deadlock": "CT-01",
    "lock_mode_conversion_deadlock": "CT-02",
    "range_contention_deadlock": "CT-03",
    "lock_amplification_deadlock": "CT-04",
    "long_transaction_cascade": "CT-05",
}

# El abrazo que no se pudo distinguir entre CT-01 y CT-02 no es una patologia
# del catalogo: lleva "CT", sin numero, y no tiene estado de evaluacion propio.
UNCLASSIFIED_DEADLOCK = "unclassified_deadlock"
_UNCLASSIFIED_CODE = "CT"


def code_of(antipatron: str) -> str | None:
    """Codigo de la patologia, o None si el nombre no es del catalogo."""
    if antipatron == UNCLASSIFIED_DEADLOCK:
        return _UNCLASSIFIED_CODE
    return CODES.get(antipatron)
