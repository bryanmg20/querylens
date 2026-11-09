"""Clave por problema (storage.finding_key) y guardado en public.problemas +
public.detecciones (storage.save_findings).

Dos detecciones con la misma clave son el mismo problema: van a la misma fila
de public.problemas y cada una agrega una fila en public.detecciones. La clave
no cambia con lo que varia de una ventana a otra sin que cambie el problema
(severidad, consulta asociada en AP-05, tabla del plan en AP-08) ni con el
query_id que Postgres le da a la misma consulta al recrear la tabla.
"""
import json
from datetime import timedelta
from unittest import mock

from models import Hallazgo
from storage import finding_key, save_findings
from test_unused_index import CAPTURED_AT, QUERY_ID, _detect, _index, _scan, _snapshot, _sysbench_indexes


def _finding(antipatron, severidad="medium", query_id="q1", table_name="orders", **evidencia):
    return Hallazgo(
        antipatron=antipatron,
        severidad=severidad,
        evidencia=evidencia,
        explicacion="e",
        recomendacion="r",
        query_id=query_id,
        table_name=table_name,
    )


# ----------------------------------------------------------------- AP-05


def _ap05(index_name, subtipo="no_usado", query_id="Q1", severidad="low", **extra):
    # como lo arma unused_index: la tabla y el schema van tambien en la evidencia
    return Hallazgo(
        antipatron="unused_index", severidad=severidad, explicacion="e", recomendacion="r",
        evidencia={"subtipo": subtipo, "schema_name": "ql_demo", "table_name": "ql_scn",
                   "index_name": index_name, **extra},
        query_id=query_id, table_name="ql_scn",
    )


def test_ap05_key_is_the_index_and_its_subtype_not_the_query():
    a = _ap05("ql_scn_pad", query_id="Q1")
    b = _ap05("ql_scn_pad", query_id="Q2", severidad="high")
    assert finding_key(a) == finding_key(b) == "ql_demo.ql_scn|ql_scn_pad|no_usado"


def test_ap05_two_indexes_of_the_same_table_and_query_are_two_problems():
    pad = _ap05("ql_scn_pad")
    c_k = _ap05("ql_scn_c_k")
    redundant = _ap05("ql_scn_pad", subtipo="redundante")
    assert len({finding_key(pad), finding_key(c_k), finding_key(redundant)}) == 3


def test_ap05_real_detector_gives_one_key_per_index():
    # el escenario del informe: idx_pad sin uso y k_1_dup redundante, en la misma tabla
    findings = _detect(_snapshot(_sysbench_indexes(), [_scan("sbtest1", "k_1", "sbtest1")]))
    keys = {f.evidencia["index_name"]: finding_key(f) for f in findings}
    assert keys == {
        "idx_pad": "ql_demo.sbtest1|idx_pad|no_usado",
        "k_1_dup": "ql_demo.sbtest1|k_1_dup|redundante",
    }
    assert all(f.query_id == QUERY_ID for f in findings)


def test_ap05_real_detector_same_key_when_severity_changes():
    # en una ventana la consulta filtra por otra columna (baja); en otra recorre
    # la tabla filtrando por la del indice (alta). Es el mismo indice sin uso
    indexes = [_index("k_1", "k", 0)]
    low = _detect(_snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan",
                                            predicate="(c = 5)")],
                            canonic_query="SELECT k FROM sbtest1 WHERE c = $1"))
    high = _detect(_snapshot(indexes, [_scan("sbtest1", table_name="sbtest1", access_method="full_table_scan",
                                             predicate="(k = 5)")]))
    assert [f.severidad for f in low + high] == ["low", "high"]
    assert finding_key(low[0]) == finding_key(high[0])


# ----------------------------------------------------- patrones por consulta
#
# La consulta se identifica por su texto normalizado (canonic_query del
# pipeline o, si no lo hay, el query_text del motor), no por el query_id: en
# Postgres el query_id cambia al borrar y crear la tabla.

SELECT_C = "SELECT id, pad FROM ql_scn WHERE c = $1"
SELECT_K = "SELECT pad FROM ql_scn WHERE k = $1"
# todos los patrones que identifican el problema por la consulta; AP-01 solo trae query_text
QUERY_PATTERNS = {
    "baseline_degradation": {"query_text": SELECT_C},  # AP-01
    "avoidable_full_scan": {"canonic_query": SELECT_C, "query_text": SELECT_C},  # AP-02
    "non_sargable_predicate": {"canonic_query": SELECT_C, "query_text": SELECT_C, "column": "c"},  # AP-03
    "missing_index": {"canonic_query": SELECT_C, "query_text": SELECT_C},  # AP-04
    "n_plus_one": {"canonic_query": SELECT_C, "query_text": SELECT_C},  # AP-06
    "disk_spill": {"canonic_query": SELECT_C, "query_text": SELECT_C},  # AP-07
    "cardinality_misestimate": {"canonic_query": SELECT_C, "query_text": SELECT_C},  # AP-08
}


def _query_finding(antipatron, query_id="q1", table_name="ql_scn", schema_name="ql_demo", **overrides):
    evidencia = {"schema_name": schema_name, **QUERY_PATTERNS[antipatron], **overrides}
    return _finding(antipatron, query_id=query_id, table_name=table_name, **evidencia)


def test_same_text_with_another_query_id_is_the_same_problem():
    # la tabla se borro y se volvio a crear: Postgres le da otro query_id a la misma consulta
    for antipatron in QUERY_PATTERNS:
        before = _query_finding(antipatron, query_id="6084543559720382261")
        after = _query_finding(antipatron, query_id="-2802381776385179170")
        assert finding_key(before) == finding_key(after), antipatron


def test_another_text_is_another_problem():
    for antipatron in QUERY_PATTERNS:
        other = {"query_text": SELECT_K} if antipatron == "baseline_degradation" else {
            "canonic_query": SELECT_K, "query_text": SELECT_K}
        assert finding_key(_query_finding(antipatron)) != finding_key(_query_finding(antipatron, **other)), antipatron


def test_ap03_one_problem_per_column():
    k = _query_finding("non_sargable_predicate", column="k")
    c = _query_finding("non_sargable_predicate", column="c")
    assert finding_key(k) != finding_key(c)


def test_ap02_ap04_ap06_table_is_part_of_the_key():
    for antipatron in ("avoidable_full_scan", "missing_index", "n_plus_one"):
        scn = _query_finding(antipatron)
        assert finding_key(scn).endswith("|ql_demo.ql_scn")
        assert finding_key(_query_finding(antipatron, table_name="items")) != finding_key(scn)


def test_ap01_ap07_ap08_ignore_the_plan_table_but_not_the_schema():
    for antipatron in ("baseline_degradation", "disk_spill", "cardinality_misestimate"):
        one = _query_finding(antipatron, table_name="orders")
        other_node = _query_finding(antipatron, table_name="items")  # otro nodo del plan, misma consulta
        other_schema = _query_finding(antipatron, schema_name="ventas")  # mismo texto en otro schema
        assert finding_key(one) == finding_key(other_node)
        assert finding_key(one) != finding_key(other_schema)


def test_severity_is_not_part_of_the_key():
    medium = _query_finding("n_plus_one", severidad="medium")
    high = _query_finding("n_plus_one", severidad="high")
    assert finding_key(medium) == finding_key(high)


def test_canonic_text_not_available_falls_back_to_engine_text():
    # canonicalizers deja "Not available" cuando no pudo normalizar
    with_engine_text = _query_finding("avoidable_full_scan", canonic_query="Not available")
    other_engine_text = _query_finding("avoidable_full_scan", canonic_query="Not available", query_text=SELECT_K)
    assert finding_key(with_engine_text) != finding_key(other_engine_text)
    assert finding_key(with_engine_text) == finding_key(
        _query_finding("avoidable_full_scan", canonic_query=None, query_text=SELECT_C, query_id="otro"))


def test_without_any_text_falls_back_to_query_id():
    finding = _finding("avoidable_full_scan", query_id=123, table_name="orders")
    assert finding_key(finding) == "123|orders"


def test_key_stays_short_for_long_queries():
    long_text = "SELECT " + ", ".join(f"col_{i}" for i in range(2000)) + " FROM t WHERE a = $1"
    finding = _query_finding("cardinality_misestimate", canonic_query=long_text, query_text=long_text)
    assert len(long_text) > 10_000
    assert len(finding_key(finding)) < 64  # va en un indice unico de Postgres


def test_unknown_pattern_falls_back_to_query_and_table():
    finding = _finding("otro_patron", schema_name="s", canonic_query=SELECT_C)
    assert finding_key(finding).endswith("|s.orders")
    assert finding_key(finding) == finding_key(
        _finding("otro_patron", query_id="q9", schema_name="s", canonic_query=SELECT_C))


# ----------------------------------------------------------------- guardado


def _fake_engine():
    conn = mock.MagicMock()
    engine = mock.MagicMock()
    engine.begin.return_value.__enter__.return_value = conn
    return engine, conn


def test_save_findings_sends_one_row_per_detection_with_its_key():
    engine, conn = _fake_engine()
    first = _ap05("ql_scn_pad", query_id="Q1", severidad="medium", detectado=CAPTURED_AT)
    second = _ap05("ql_scn_pad", query_id="Q2", severidad="high", detectado=CAPTURED_AT + timedelta(seconds=10))

    save_findings(engine, "db-1", [first, second])

    statement, rows = conn.execute.call_args.args
    sql = str(statement)
    assert "INSERT INTO public.problemas" in sql and "INSERT INTO public.detecciones" in sql
    assert "ON CONFLICT (db_id, antipatron, finding_key)" in sql
    assert [r["finding_key"] for r in rows] == ["ql_demo.ql_scn|ql_scn_pad|no_usado"] * 2
    assert [(r["query_id"], r["severidad"]) for r in rows] == [("Q1", "medium"), ("Q2", "high")]
    assert all(r["db_id"] == "db-1" for r in rows)
    # default=str: un datetime en la evidencia no rompe el guardado
    assert json.loads(rows[1]["evidencia"])["detectado"].startswith("2026-11-02 12:00:10")


def test_save_findings_without_findings_does_not_touch_the_database():
    engine, conn = _fake_engine()
    save_findings(engine, "db-1", [])
    engine.begin.assert_not_called()
    conn.execute.assert_not_called()
