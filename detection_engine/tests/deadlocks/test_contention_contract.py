"""Contrato del hallazgo en contencion (SegundoInforme 6.1 y 6.2, issue #50).

El riesgo es el mismo texto que cada patron ya dejaba en evidencia.risk. El
impacto de contencion es la espera acumulada de las sesiones afectadas: CT-05
la mide; un abrazo mortal no, porque el informe del motor no dice cuanto
espero cada transaccion, y sale sin impacto en vez de inventarle uno.
"""
from datetime import timedelta

import pytest
from deadlock_builders import T0, index_row, mysql_ct01, mysql_ct02, mysql_ct03, mysql_ct04, pg_report, snapshot
from test_ct05_cascade import T0 as CASCADE_T0
from test_ct05_cascade import _snapshot as cascade_snapshot

from catalog import CODES, code_of
from deadlocks.ct05_long_transaction_cascade import detect_long_transaction_cascade
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory

DEADLOCKS = {
    "inconsistent_lock_order_deadlock": lambda: snapshot(mysql_ct01()),
    "lock_mode_conversion_deadlock": lambda: snapshot(mysql_ct02()),
    "range_contention_deadlock": lambda: snapshot(mysql_ct03()),
    "lock_amplification_deadlock": lambda: snapshot(mysql_ct04(), indexes=[index_row("id")]),
    # Postgres sin saber que fila esperaba cada proceso
    "unclassified_deadlock": lambda: snapshot(pg_report()),
}


def _deadlock(antipatron):
    findings, _ = detect_deadlocks(DEADLOCKS[antipatron](), T0, DeadlockMemory())
    finding, = findings
    assert finding.antipatron == antipatron
    return finding


def _cascade(waiters=2, wait_s=10):
    t1 = CASCADE_T0 + timedelta(seconds=10)
    _, previous = detect_long_transaction_cascade(
        cascade_snapshot(CASCADE_T0, waiters=waiters, wait_s=wait_s), CASCADE_T0, {},
    )
    findings, _ = detect_long_transaction_cascade(
        cascade_snapshot(t1, root_age_s=50, waiters=waiters, wait_s=wait_s), t1, previous,
    )
    finding, = findings
    return finding


@pytest.mark.parametrize("antipatron", sorted(DEADLOCKS))
def test_deadlock_findings_declare_the_risk_they_already_had_in_the_evidence(antipatron):
    finding = _deadlock(antipatron)
    assert len(finding.riesgo.strip()) > 40
    assert finding.riesgo == finding.evidencia["risk"]
    assert finding.riesgo not in (finding.recomendacion, finding.explicacion)


@pytest.mark.parametrize("antipatron", sorted(DEADLOCKS))
def test_deadlock_findings_have_no_impact_because_the_report_has_no_wait_times(antipatron):
    assert _deadlock(antipatron).impacto_ms is None


def test_ct05_impact_is_the_accumulated_wait_in_milliseconds():
    # 2 sesiones que esperan 10 s cada una
    finding = _cascade(waiters=2, wait_s=10)
    assert finding.evidencia["impact_wait_s"] == 20
    assert finding.impacto_ms == 20_000.0
    # 5 sesiones de 25 s
    assert _cascade(waiters=5, wait_s=25).impacto_ms == 125_000.0


def test_ct05_declares_its_risk():
    finding = _cascade()
    assert finding.riesgo == finding.evidencia["risk"]
    assert "revierte" in finding.riesgo


def test_every_contention_pattern_has_a_code():
    for antipatron in DEADLOCKS:
        assert code_of(antipatron), antipatron
    assert code_of("long_transaction_cascade") == "CT-05"
    # el abrazo sin clasificar no es una patologia del catalogo
    assert "unclassified_deadlock" not in CODES and code_of("unclassified_deadlock") == "CT"
