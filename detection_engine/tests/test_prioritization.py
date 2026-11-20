"""Orden de los hallazgos por impacto (SegundoInforme 6.2, issue #51).

De mayor a menor impacto; los empates, por severidad y despues por el codigo
de la patologia. La misma entrada da siempre el mismo orden, llegue como
llegue.
"""
import itertools
import random

from models import Hallazgo
from prioritization import priority_key, prioritize


def _finding(antipatron, impacto_ms, severidad="medium", query="q", table="t"):
    return Hallazgo(
        antipatron=antipatron, severidad=severidad, explicacion="e", recomendacion="r", riesgo="x",
        evidencia={"canonic_query": f"SELECT 1 FROM {table} /* {query} */", "schema_name": "public"},
        query_id=query, table_name=table, impacto_ms=impacto_ms,
    )


def _labels(findings):
    return [(f.antipatron, f.impacto_ms, f.severidad) for f in findings]


def test_higher_impact_goes_first_whatever_the_severity():
    # 6.2: la severidad desempata, no ordena. Una media sobre la sentencia mas
    # ejecutada pesa mas que una alta sobre una poco frecuente
    low_impact_high = _finding("non_sargable_predicate", 50.0, "high")
    high_impact_medium = _finding("missing_index", 9_000.0, "medium")
    high_impact_low = _finding("unused_index", 12_000.0, "low")
    ordered = prioritize([low_impact_high, high_impact_medium, high_impact_low])
    assert [f.antipatron for f in ordered] == ["unused_index", "missing_index", "non_sargable_predicate"]


def test_ties_are_broken_by_severity_and_then_by_pathology_code():
    same_impact = [
        _finding("cardinality_misestimate", 300.0, "medium"),  # AP-08
        _finding("missing_index", 300.0, "medium"),  # AP-04
        _finding("avoidable_full_scan", 300.0, "low"),  # AP-02
        _finding("disk_spill", 300.0, "high"),  # AP-07
        _finding("non_sargable_predicate", 300.0, "high"),  # AP-03
    ]
    assert [(f.antipatron, f.severidad) for f in prioritize(same_impact)] == [
        ("non_sargable_predicate", "high"),  # alta, AP-03 antes que AP-07
        ("disk_spill", "high"),
        ("missing_index", "medium"),  # media, AP-04 antes que AP-08
        ("cardinality_misestimate", "medium"),
        ("avoidable_full_scan", "low"),
    ]


def test_code_order_is_the_one_of_the_report_not_alphabetical_by_name():
    # por nombre, avoidable_full_scan (AP-02) iria antes que baseline_degradation (AP-01)
    tied = [_finding("avoidable_full_scan", 10.0), _finding("baseline_degradation", 10.0)]
    assert [f.antipatron for f in prioritize(tied)] == ["baseline_degradation", "avoidable_full_scan"]
    # y contencion despues de los antipatrones
    tied = [_finding("long_transaction_cascade", 10.0), _finding("cardinality_misestimate", 10.0)]
    assert [f.antipatron for f in prioritize(tied)] == ["cardinality_misestimate", "long_transaction_cascade"]


def test_unmeasured_impact_counts_as_zero_and_ties_go_to_severity():
    # un abrazo mortal no trae impacto: no se le inventa uno. Queda debajo de
    # lo que si consumio tiempo; con un impacto de 0, la severidad lo sube
    deadlock_high = _finding("inconsistent_lock_order_deadlock", None, "high")
    deadlock_medium = _finding("lock_mode_conversion_deadlock", None, "medium")
    idle_index = _finding("unused_index", 0.0, "low")
    slow_query = _finding("disk_spill", 2_000.0, "low")
    ordered = prioritize([idle_index, deadlock_medium, slow_query, deadlock_high])
    assert [f.antipatron for f in ordered] == [
        "disk_spill",
        "inconsistent_lock_order_deadlock",
        "lock_mode_conversion_deadlock",
        "unused_index",
    ]


def test_same_input_gives_the_same_order_in_any_arrival_order():
    findings = [
        _finding("missing_index", 300.0, "medium", query="a"),
        _finding("missing_index", 300.0, "medium", query="b"),  # empate total salvo el problema
        _finding("avoidable_full_scan", 300.0, "medium", query="a"),
        _finding("disk_spill", 4_000.0, "high"),
        _finding("unused_index", 0.0, "low"),
        _finding("unclassified_deadlock", None, "medium"),
        _finding("range_contention_deadlock", None, "high"),
        _finding("otro_patron", 300.0, "medium"),  # fuera del catalogo
    ]
    expected = _labels(prioritize(findings))
    keys = [priority_key(f) for f in prioritize(findings)]
    assert keys == sorted(keys) and len(set(keys)) == len(keys), "queda un empate sin resolver"

    for permutation in itertools.islice(itertools.permutations(findings), 0, 5000, 37):
        assert _labels(prioritize(list(permutation))) == expected
    rng = random.Random(51)
    for _ in range(200):
        shuffled = findings[:]
        rng.shuffle(shuffled)
        assert _labels(prioritize(shuffled)) == expected


def test_a_pattern_outside_the_catalog_goes_after_the_catalog_on_ties():
    tied = [_finding("otro_patron", 10.0), _finding("long_transaction_cascade", 10.0)]
    assert [f.antipatron for f in prioritize(tied)] == ["long_transaction_cascade", "otro_patron"]


def test_prioritize_does_not_change_its_input():
    findings = [_finding("unused_index", 1.0), _finding("disk_spill", 2.0)]
    before = list(findings)
    prioritize(findings)
    assert findings == before
