"""Clave por problema (storage.finding_key) de los patrones de contencion.

CT-01 a CT-04 y el abrazo sin clasificar usan la firma del abrazo: el mismo
ciclo entre las mismas sentencias, aunque ocurra a otra hora, es el mismo
problema. CT-05 se reporta una vez por transaccion: cada transaccion es su
propio problema.
"""
from datetime import timedelta

from deadlock_builders import T0, mysql_ct01, pg_ct01, pg_ct02, snapshot

from deadlocks.ct05_long_transaction_cascade import ANTIPATRON as CT05
from deadlocks.deadlock_detector import detect_deadlocks
from deadlocks.state import DeadlockMemory
from models import Hallazgo
from storage import finding_key


def test_the_same_deadlock_at_another_time_is_the_same_problem():
    memory = DeadlockMemory()
    keys = []
    for i in range(3):
        findings, memory = detect_deadlocks(
            snapshot(mysql_ct01(detected_at=f"2026-10-22 00:5{i}:26.000000")), T0, memory,
        )
        keys.append(finding_key(findings[0]))
    assert len(set(keys)) == 1
    assert keys[0] == findings[0].evidencia["signature"]


def test_same_statements_as_ct01_and_as_ct02_are_two_problems():
    first, memory = detect_deadlocks(snapshot(pg_ct01()), T0, DeadlockMemory())
    second, _ = detect_deadlocks(snapshot(pg_ct02(detected_at="2026-10-22 00:47:01.000000")), T0, memory)
    assert finding_key(first[0]) != finding_key(second[0])


def _cascade(pid, started):
    return Hallazgo(
        antipatron=CT05, severidad="medium", explicacion="e", recomendacion="r",
        evidencia={"root": {"process_id": pid, "transaction_start_time": started}},
        query_id=1, table_name="cuentas",
    )


def test_ct05_one_problem_per_transaction():
    started = (T0 - timedelta(minutes=1)).isoformat()
    same = _cascade(100, started)
    assert finding_key(same) == finding_key(_cascade(100, started)) == f"100|{started}"
    # otra transaccion, aunque sea el mismo proceso o la misma consulta
    assert finding_key(_cascade(100, T0.isoformat())) != finding_key(same)
    assert finding_key(_cascade(101, started)) != finding_key(same)
