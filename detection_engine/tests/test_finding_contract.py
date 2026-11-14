"""Contrato del hallazgo (SegundoInforme 6.1 y 6.2, issue #50): cada hallazgo
de un antipatron lleva el riesgo de aplicar su recomendacion y su impacto, y
la base donde ocurrio (issue #78).

Impacto de un antipatron: tiempo de base de datos de su sentencia en la
ventana, es decir, la diferencia de total_time_ms (total_exec_time en
Postgres, SUM_TIMER_WAIT en MySQL) entre el snapshot anterior y este. La
evidencia guarda el mismo valor, para poder recalcularlo.

Los snapshots son minimos y se validan con el contrato, igual que los que
llegan por la cola. Los de AP-02, AP-03, AP-04, AP-05 y AP-08 salen de los
armadores de sus propias pruebas.
"""
from datetime import datetime, timedelta, timezone

import pytest
from querylens_contract import SnapshotPayload

import test_cardinality_misestimate as ap08
import test_predicate_fixes as scans
import test_unused_index as ap05
from anti_patterns.avoidable_full_scan import detect_avoidable_full_scans
from anti_patterns.baseline_degradation import detect_baseline_degradation
from anti_patterns.disk_spill import detect_disk_spill
from anti_patterns.missing_index import detect_missing_indexes
from anti_patterns.n_plus_one import detect_n_plus_one
from anti_patterns.non_sargable_predicate import detect_non_sargable_predicates
from models import StatementHistory

NOW = datetime(2026, 10, 13, 10, 6, tzinfo=timezone.utc)
PREVIOUS = NOW - timedelta(seconds=10)
EPOCH = "2026-10-13 10:00:00.000000"
EPOCH_AT = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
DATABASE = "ql_demo"


def _statement(query_id, canonic_query, calls, rows, total_ms, **extra):
    return {
        "query_id": query_id, "query_text": canonic_query, "canonic_query": canonic_query,
        "schema_name": "public", "database_name": DATABASE,
        "execution_count": calls, "rows_returned": rows, "total_time_ms": total_ms,
        "mean_time_ms": total_ms / calls, "counters_epoch": EPOCH, **extra,
    }


def _payload(statements, **sections):
    return SnapshotPayload.model_validate({
        "db_id": "db_test", "statements": statements, "top_impact_queries": statements,
        "locks": [], "active_queries": [], "indexes": [], "tables": [], "columns": [],
        "server_start_timestamp": [{"server_start_time": "2026-10-01 00:00:00"}],
        **sections,
    })


def _previous(query_id, calls, rows, total_ms, **extra):
    return StatementHistory(
        query_id=str(query_id), captured_at=PREVIOUS, execution_count=calls, total_time_ms=total_ms,
        rows_returned=rows, counters_epoch=EPOCH_AT, **extra,
    )


def _with_base(snapshot, total_time_ms=None):
    """Los armadores de las otras pruebas no ponen la base ni mueven el tiempo
    total: se completa aca, en statements y en los candidatos."""
    for row in [*snapshot.statements, *snapshot.top_impact_queries]:
        row.database_name = DATABASE
        if total_time_ms is not None:
            row.total_time_ms = total_time_ms
    return snapshot


# ------------------------------------------------- un hallazgo de cada regla


def ap01_findings():
    # 100 ejecuciones en 2.000 ms (20 ms cada una) contra una mediana de 1 ms
    snapshot = _payload([_statement(77, "SELECT c FROM t WHERE k = $1", 1100, 1100, 3000.0)])
    history = {"77": _previous(
        77, 1000, 1000, 1000.0,
        window_means_ms=[1.0] * 60,
        window_ends_at=[PREVIOUS - timedelta(seconds=10 * i) for i in range(60, 0, -1)],
    )}
    return detect_baseline_degradation(snapshot, history, NOW), 2000.0


def ap02_findings():
    # la sentencia de las pruebas de AP-04, con 800 ms acumulados contra 500
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = _with_base(
        scans._snapshot(scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation]), 800.0,
    )
    return detect_avoidable_full_scans(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW), 300.0


def ap03_findings():
    snapshot = _with_base(
        scans._snapshot(scans.PG_QUERY_ID, scans.LOWER_NOTE, scans.FULL_SCAN_NOTE, indexes=[scans._index("note")]),
        800.0,
    )
    return detect_non_sargable_predicates(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW), 300.0


def ap04_findings():
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = _with_base(
        scans._snapshot(scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation]), 800.0,
    )
    return detect_missing_indexes(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW), 300.0


def ap05_findings():
    # idx_pad sin uso y k_1_dup redundante; la consulta asociada paso de 60 a 100 ms
    snapshot = _with_base(ap05._snapshot(ap05._sysbench_indexes(), [ap05._scan("sbtest1", "k_1", "sbtest1")]))
    return ap05._detect(snapshot), 40.0


def ap06_findings():
    # 2 ejecuciones del padre devuelven 100 clientes y la hija corre 100 veces.
    # Impacto: la hija (2500 - 2000 = 500 ms) mas el padre (90 - 80 = 10 ms)
    parent = _statement(11, "SELECT id, name FROM customers WHERE region = $1", 22, 1100, 90.0)
    child = _statement(12, "SELECT id, total FROM orders WHERE customer_id = $1", 1100, 3300, 2500.0)
    snapshot = _payload([parent, child], foreign_keys=[{
        "schema_name": "public", "table_name": "orders", "column_name": "customer_id",
        "referenced_schema_name": "public", "referenced_table_name": "customers",
        "referenced_column_name": "id", "constraint_name": "orders_customer_fk", "position": 1,
    }])
    history = {"11": _previous(11, 20, 1000, 80.0), "12": _previous(12, 1000, 3000, 2000.0)}
    return detect_n_plus_one(snapshot, history, NOW), 510.0


def ap07_findings():
    # 2 ejecuciones en 5.000 ms que escribieron 400 bloques temporales
    statement = _statement(
        88, "SELECT c, COUNT(*) FROM t GROUP BY c ORDER BY c", 12, 1200, 6000.0, disk_spill_indicator=500,
    )
    history = {"88": _previous(88, 10, 1000, 1000.0, disk_spill_indicator=100)}
    return detect_disk_spill(_payload([statement]), history, NOW), 5000.0


def ap08_findings():
    # la sentencia de las pruebas de AP-08: 500 ms acumulados contra 300
    snapshot = _with_base(ap08._snapshot(output_rows=8, actual_rows=20_000))
    return ap08._detect(snapshot), 200.0


RULES = {
    "baseline_degradation": ap01_findings,
    "avoidable_full_scan": ap02_findings,
    "non_sargable_predicate": ap03_findings,
    "missing_index": ap04_findings,
    "unused_index": ap05_findings,
    "n_plus_one": ap06_findings,
    "disk_spill": ap07_findings,
    "cardinality_misestimate": ap08_findings,
}


@pytest.fixture(params=sorted(RULES))
def rule(request):
    findings, impact_ms = RULES[request.param]()
    assert findings, f"{request.param}: el snapshot de prueba no produjo hallazgos"
    assert {finding.antipatron for finding in findings} == {request.param}
    return findings, impact_ms


# ------------------------------------------------------------------- riesgo


def test_every_finding_declares_the_risk_of_its_recommendation(rule):
    findings, _ = rule
    for finding in findings:
        assert len(finding.riesgo.strip()) > 40, finding.antipatron
        # el riesgo es del arreglo, no una copia de la recomendacion ni de la explicacion
        assert finding.riesgo not in (finding.recomendacion, finding.explicacion)


def test_ap03_risk_follows_the_subtype():
    function_on_column, = ap03_findings()[0]
    assert function_on_column.evidencia["subtipo"] == "function_on_column"
    assert "indice de expresion" in function_on_column.riesgo

    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "((customer_id)::numeric = $1)"}
    snapshot = scans._snapshot(
        scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE customer_id = $1", [operation],
        indexes=[scans._index("customer_id")],
    )
    cast, = detect_non_sargable_predicates(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW)
    assert cast.evidencia["subtipo"] == "implicit_cast"
    assert "tipo" in cast.riesgo and cast.riesgo != function_on_column.riesgo


def test_ap04_risk_adds_the_cost_of_one_index_per_or_branch():
    single, = ap04_findings()[0]
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "((note = $1) OR (status = $2))"}
    snapshot = scans._snapshot(
        scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1 OR status = $2", [operation],
    )
    per_branch, = detect_missing_indexes(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW)
    assert per_branch.evidencia["indice_por_rama"] is True
    assert per_branch.riesgo.startswith(single.riesgo) and "varios indices" in per_branch.riesgo
    assert "varios indices" not in single.riesgo


def test_ap05_risk_follows_what_is_recommended():
    by_index = ap05._by_index(ap05_findings()[0])
    unused = by_index[("sbtest1", "idx_pad")]
    redundant = by_index[("sbtest1", "k_1_dup")]
    # eliminar un indice sin uso y eliminar uno duplicado no arriesgan lo mismo
    assert "consulta poco frecuente" in unused.riesgo
    assert redundant.riesgo != unused.riesgo and "no se deshace" in redundant.riesgo


def test_ap07_risk_follows_what_spilled():
    general, = ap07_findings()[0]
    assert "work_mem" in general.riesgo
    # MySQL: solo el ordenamiento fue a disco
    statement = _statement(
        "ql_demo/s1", "SELECT c FROM t ORDER BY c", 12, 1200, 6000.0,
        disk_spill_indicator=0, sort_merge_passes=30,
    )
    history = {"ql_demo/s1": _previous("ql_demo/s1", 10, 1000, 1000.0, disk_spill_indicator=0, sort_merge_passes=10)}
    sort_only, = detect_disk_spill(_payload([statement]), history, NOW)
    assert sort_only.evidencia["tipo_vertido"] == "ordenamiento"
    assert "sort_buffer_size" in sort_only.riesgo and sort_only.riesgo != general.riesgo


# ------------------------------------------------------------------ impacto


def test_impact_is_the_database_time_of_the_statement_in_the_window(rule):
    findings, impact_ms = rule
    for finding in findings:
        assert finding.impacto_ms == pytest.approx(impact_ms), finding.antipatron


def test_evidence_keeps_the_value_to_recompute_the_impact(rule):
    findings, _ = rule
    keys = {
        "baseline_degradation": "impact_ms",
    }
    for finding in findings:
        key = keys.get(finding.antipatron, "window_time_ms")
        assert finding.evidencia[key] == pytest.approx(finding.impacto_ms), finding.antipatron


def test_ap06_impact_adds_the_parent_to_the_child():
    finding, = ap06_findings()[0]
    assert finding.evidencia["child_window_time_ms"] == pytest.approx(500.0)
    assert finding.evidencia["parent_window_time_ms"] == pytest.approx(10.0)
    assert finding.impacto_ms == pytest.approx(510.0)


def test_ap06_without_the_parent_time_the_impact_is_the_child():
    parent = {**_statement(11, "SELECT id, name FROM customers WHERE region = $1", 22, 1100, 90.0),
              "total_time_ms": None}
    child =_statement(12, "SELECT id, total FROM orders WHERE customer_id = $1", 1100, 3300, 2500.0)
    snapshot = _payload([parent, child], foreign_keys=[{
        "schema_name": "public", "table_name": "orders", "column_name": "customer_id",
        "referenced_schema_name": "public", "referenced_table_name": "customers",
        "referenced_column_name": "id", "constraint_name": "orders_customer_fk", "position": 1,
    }])
    history = {"11": _previous(11, 20, 1000, 80.0), "12": _previous(12, 1000, 3000, 2000.0)}
    finding, = detect_n_plus_one(snapshot, history, NOW)
    assert finding.evidencia["parent_window_time_ms"] is None
    assert finding.impacto_ms == pytest.approx(500.0)


def test_ap05_impact_is_the_time_of_its_associated_query():
    for finding in ap05_findings()[0]:
        associated, = [
            query for query in finding.evidencia["queries_que_filtran_la_tabla"]
            if query["query_id"] == finding.query_id
        ]
        assert finding.impacto_ms == associated["window_time_ms"] == 40.0


def test_impact_of_a_query_new_in_the_window_is_all_its_time():
    # sin fila anterior y con el contador recien empezado: todo lo acumulado es de esta ventana
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = scans._snapshot(scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation])
    started = NOW - timedelta(seconds=4)
    for row in [*snapshot.statements, *snapshot.top_impact_queries]:
        row.counters_epoch = started.replace(tzinfo=None).isoformat(sep=" ")
    finding, = detect_missing_indexes(snapshot, {}, NOW)
    assert finding.evidencia["query_new_in_window"] is True
    assert finding.impacto_ms == 500.0


def test_time_never_decides_whether_there_is_a_finding():
    # el tiempo total retrocede (no deberia): el hallazgo sale igual, con impacto 0
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = _with_base(
        scans._snapshot(scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation]), 100.0,
    )
    finding, = detect_missing_indexes(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW)
    assert finding.impacto_ms == 0.0

    # el snapshot no trae el tiempo: el hallazgo sale igual, sin impacto
    for row in [*snapshot.statements, *snapshot.top_impact_queries]:
        row.total_time_ms = None
    finding, = detect_missing_indexes(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW)
    assert finding.impacto_ms is None and finding.evidencia["window_time_ms"] is None


def test_ap01_impact_is_the_window_time_not_the_extra_time():
    # la severidad sale del tiempo de mas (1.900 ms); el impacto, de todo el tiempo de la sentencia
    finding, = ap01_findings()[0]
    assert finding.evidencia["extra_time_ms"] == pytest.approx(1900.0)
    assert finding.impacto_ms == pytest.approx(2000.0)


# --------------------------------------------------- base del hallazgo (#78)


def test_antipattern_findings_carry_the_database_of_their_statement(rule):
    findings, _ = rule
    assert {finding.database_name for finding in findings} == {DATABASE}


def test_database_is_none_when_the_statement_does_not_say():
    operation = {**scans.FULL_SCAN_NOTE[0], "predicate": "(note = $1)"}
    snapshot = scans._snapshot(scans.PG_QUERY_ID, "SELECT id FROM ql_pt_orders WHERE note = $1", [operation])
    finding, = detect_missing_indexes(snapshot, scans._history(scans.PG_QUERY_ID, 30, 30), NOW)
    assert finding.database_name is None
