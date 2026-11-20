"""Calculo de M1 a M4 (ql_sandbox/banco/metricas.py) y manifiestos del banco.

Las corridas son las que escribe banco.py en corridas.jsonl, armadas a mano:
no hace falta Docker ni el sandbox.

    python -m pytest ql_sandbox/banco/tests
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manifiestos as mf  # noqa: E402
import metricas  # noqa: E402

START = "2026-11-16 10:00:00+00"


def _at(seconds: float) -> str:
    return f"2026-11-16T10:{int(seconds // 60):02d}:{seconds % 60:06.3f}+00:00"


def _finding(antipatron, table_name=None, seconds=20.0, problema_id=1, **evidence):
    return {
        "problema_id": problema_id, "antipatron": antipatron, "codigo": mf.CODIGO.get(antipatron),
        "table_name": table_name, "severidad": "medium", "evidencia": evidence,
        "primera_deteccion": _at(seconds), "detecciones": 3, "deteccion_ids": [problema_id * 10],
        "con_explicacion": True, "con_recomendacion": True, "con_riesgo": True,
    }


def _ap04(seconds=20.0):
    return _finding("missing_index", "ql_scn", seconds, 1, equality_columns=["c"],
                    suggested_index="CREATE INDEX ON ql_scn (c)")


def _ap02(seconds=20.0):
    return _finding("avoidable_full_scan", "ql_scn", seconds, 2, access_method="full_table_scan", selectivity=1e-05)


def _injection(scenario, findings, engine="postgres", rep=1):
    return {"id": f"iny|{engine}|{scenario}|{rep}", "tipo": "inyeccion", "engine": engine, "scenario": scenario,
            "rep": rep, "inicio": START, "fin": _at(150), "hallazgos": findings}


def _control(findings, engine="postgres", rep=1, minutes=30):
    return {"id": f"control|{engine}|{rep}", "tipo": "control", "engine": engine, "rep": rep,
            "inicio": START, "fin": _at(minutes * 60 - 1) if minutes < 60 else START, "hallazgos": findings}


def _combo(metrics, codigo, engine="postgres"):
    row, = [r for r in metrics["combinaciones"] if (r["codigo"], r["engine"]) == (codigo, engine)]
    return row


# ---------------------------------------------------------------- manifiestos


def test_the_bench_has_22_scenarios_for_24_combinations():
    assert mf.scenario_count() == 22
    assert len(mf.combinations()) == 24
    # cada patologia de antipatrones tiene un escenario que la evalua
    evaluated = {codigo for manifest in mf.AP_MANIFESTS.values() for codigo in manifest.codes()}
    assert evaluated == {f"AP-0{n}" for n in range(1, 9)}
    assert set(mf.AP_ORDER) == set(mf.AP_MANIFESTS)


def test_criteria_conditions():
    finding = _finding("unused_index", "sbtest1", index_name="k_1", subtipo="no_usado", selectivity=0.1,
                       tables=["ql_scn"])
    assert mf.matches({"antipatron": "unused_index"}, finding)
    assert not mf.matches({"antipatron": "missing_index"}, finding)
    assert mf.matches({"table_name": {"prefijo": "sbtest"}}, finding)
    assert not mf.matches({"table_name": {"prefijo": "ql_"}}, finding)
    assert mf.matches({"evidencia": {"index_name": {"en": ["k_1", "k_2"]}}}, finding)
    assert not mf.matches({"evidencia": {"index_name": {"en": ["k_2"]}}}, finding)
    # max: en el limite cuenta; sin el dato, no
    assert mf.matches({"evidencia": {"selectivity": {"max": 0.1}}}, finding)
    assert not mf.matches({"evidencia": {"selectivity": {"max": 0.09}}}, finding)
    assert not mf.matches({"evidencia": {"no_esta": {"max": 1}}}, finding)
    # contiene: en un texto o en el JSON de una lista
    assert mf.matches({"evidencia": {"tables": {"contiene": "ql_scn"}}}, finding)
    assert not mf.matches({"evidencia": {"no_esta": {"contiene": "x"}}}, finding)
    assert mf.matches({"evidencia_contiene": "k_1"}, finding)
    with pytest.raises(ValueError):
        mf.matches({"table_name": {"parecido": "x"}}, finding)


# ------------------------------------------------------------------ M1 y M4


def test_expected_finding_is_a_true_positive_with_its_latency():
    metrics = metricas.compute([_injection("missing_index", [_ap04(18.5), _ap02(27.0)])])
    ap04, ap02 = _combo(metrics, "AP-04"), _combo(metrics, "AP-02")
    assert (ap04["vp"], ap04["fn"], ap04["m1"]) == (1, 0, 1.0)
    assert ap04["m4"]["max_s"] == 18.5 and ap04["m4"]["max_ventanas"] == 1.9
    assert ap02["m4"]["max_s"] == 27.0
    assert ap04["m4_ok"] is True
    # el otro motor no tuvo inyecciones
    assert _combo(metrics, "AP-04", "mysql")["inyecciones"] == 0
    assert _combo(metrics, "AP-04", "mysql")["m1_ok"] is None


def test_missing_expected_finding_is_a_false_negative():
    metrics = metricas.compute([_injection("missing_index", [_ap02()])])
    ap04 = _combo(metrics, "AP-04")
    assert (ap04["vp"], ap04["fn"], ap04["m1"], ap04["m1_ok"]) == (0, 1, 0.0, False)
    assert ap04["m4"] is None and ap04["m4_ok"] is None
    assert _combo(metrics, "AP-02")["vp"] == 1
    missing, = metrics["falsos_negativos"]
    assert missing["codigo"] == "AP-04" and "ql_scn.c" in missing["faltantes"][0]


def test_finding_that_does_not_meet_the_criterion_is_not_the_expected_one():
    # indice sugerido sobre otra columna: no es el hallazgo del manifiesto
    other = _finding("missing_index", "ql_scn", 20, 1, equality_columns=["pad"],
                     suggested_index="CREATE INDEX ON ql_scn (pad)")
    metrics = metricas.compute([_injection("missing_index", [other, _ap02()])])
    ap04 = _combo(metrics, "AP-04")
    assert (ap04["vp"], ap04["fn"], ap04["fp"]) == (0, 1, 1)


def test_pathology_with_several_expected_needs_all_of_them():
    orders = _finding("n_plus_one", "ql_n1_orders", 15, 1, direccion="uno_a_muchos")
    customers = _finding("n_plus_one", "ql_n1_customers", 35, 2, direccion="muchos_a_uno")
    only_one = metricas.compute([_injection("n_plus_one", [orders])])
    assert (_combo(only_one, "AP-06")["vp"], _combo(only_one, "AP-06")["fn"]) == (0, 1)
    both = metricas.compute([_injection("n_plus_one", [orders, customers])])
    ap06 = _combo(both, "AP-06")
    assert (ap06["vp"], ap06["fn"]) == (1, 0)
    # la latencia es hasta el ultimo esperado
    assert ap06["m4"]["max_s"] == 35.0


def test_latency_over_the_limit_fails_m4_but_still_counts_for_m1():
    metrics = metricas.compute([_injection("missing_index", [_ap04(125.0), _ap02(30.0)])])
    ap04 = _combo(metrics, "AP-04")
    assert (ap04["vp"], ap04["m1_ok"]) == (1, True)
    assert (ap04["m4"]["dentro"], ap04["m4"]["limite_s"], ap04["m4_ok"]) == (0, 120, False)
    assert _combo(metrics, "AP-02")["m4_ok"] is True


def test_m1_threshold_is_nine_of_ten():
    def run(detected):
        units = [_injection("missing_index", [_ap04(), _ap02()] if rep <= detected else [_ap02()], rep=rep)
                 for rep in range(1, 11)]
        return _combo(metricas.compute(units, reps=10), "AP-04")
    nine, eight = run(9), run(8)
    assert (nine["m1"], nine["m1_ok"], nine["completa"]) == (0.9, True, True)
    assert (eight["m1"], eight["m1_ok"]) == (0.8, False)
    assert nine["m1_ic95"][0] < 0.9 < nine["m1_ic95"][1]


def test_combination_with_fewer_repetitions_is_incomplete():
    metrics = metricas.compute([_injection("missing_index", [_ap04(), _ap02()])], reps=10)
    assert _combo(metrics, "AP-04")["completa"] is False
    assert metrics["resumen"]["completas"] == 0
    assert metrics["resumen"]["M1"]["veredicto"] == "NO CUMPLE"


# ------------------------------------------------------------------------ M2


def test_undeclared_finding_is_a_false_positive_of_its_own_pathology():
    spill = _finding("disk_spill", "sbtest1", 20, 9, tables=["sbtest1"], query_text="SELECT c FROM sbtest1")
    metrics = metricas.compute([
        _injection("missing_index", [_ap04(), _ap02(), spill]),
        _control([spill]),
        _control([spill], engine="mysql"),
    ])
    ap07 = _combo(metrics, "AP-07")
    # una vez por corrida (inyeccion y control), no por deteccion
    assert (ap07["vp"], ap07["fp"], ap07["m2"]) == (0, 2, 0.0)
    assert _combo(metrics, "AP-07", "mysql")["fp"] == 1
    # no afecta a la patologia del escenario
    assert (_combo(metrics, "AP-04")["fp"], _combo(metrics, "AP-04")["m2"]) == (0, 1.0)
    assert {item["corrida"] for item in metrics["falsos_positivos"]} == {
        "iny|postgres|missing_index|1", "control|postgres|1", "control|mysql|1"}
    assert metrics["controles"]["postgres"] == {"corridas": 1, "minutos": 30.0}


def test_m2_threshold():
    spill = _finding("disk_spill", "ql_scn", 20, 9, tables=["ql_scn"])
    good = [_injection("disk_spill", [spill], rep=rep) for rep in range(1, 10)]
    stray = _finding("disk_spill", "sbtest1", 20, 8, tables=["sbtest1"])
    one = metricas.compute(good + [_control([stray])])
    assert (_combo(one, "AP-07")["m2"], _combo(one, "AP-07")["m2_ok"]) == (0.9, True)
    two = metricas.compute(good + [_control([stray]), _control([stray], rep=2)])
    assert _combo(two, "AP-07")["m2_ok"] is False


def test_declared_collateral_is_neither_true_nor_false_positive(monkeypatch):
    misestimate = _finding("cardinality_misestimate", "ql_scn", 20, 7)
    manifest = mf.AP_MANIFESTS["missing_index"]
    collateral = mf.Collateral("MySQL estima 10 % fijo sin indice", {
        "antipatron": "cardinality_misestimate", "table_name": "ql_scn"}, engines=("mysql",))
    monkeypatch.setitem(mf.AP_MANIFESTS, "missing_index",
                        mf.Manifest(manifest.scenario, manifest.descripcion, manifest.expected, (collateral,)))
    units = [_injection("missing_index", [_ap04(), _ap02(), misestimate], engine=engine)
             for engine in ("mysql", "postgres")]
    metrics = metricas.compute(units)
    # declarado solo para MySQL: en Postgres es un FP
    assert _combo(metrics, "AP-08", "mysql")["fp"] == 0
    assert _combo(metrics, "AP-08", "postgres")["fp"] == 1
    assert _combo(metrics, "AP-08", "mysql")["vp"] == 0
    declared, = metrics["declarados"]
    assert (declared["tipo"], declared["engine"], declared["veces"]) == ("colateral", "mysql", 1)


def test_baseline_findings_are_not_false_positives(monkeypatch):
    unused = _finding("unused_index", "sbtest1", 20, 5, index_name="k_1", subtipo="no_usado")
    base = mf.Collateral("indices de sysbench que la carga no consulta",
                         {"antipatron": "unused_index", "table_name": {"prefijo": "sbtest"}})
    monkeypatch.setitem(mf.LINEA_BASE, "postgres", (base,))
    metrics = metricas.compute([_control([unused]), _injection("missing_index", [_ap04(), _ap02(), unused])])
    assert _combo(metrics, "AP-05")["fp"] == 0
    assert sorted(item["tipo"] for item in metrics["declarados"]) == ["linea_base", "linea_base"]


def test_sysbench_degraded_by_an_injection_is_collateral_but_not_in_controls():
    degraded = _finding("baseline_degradation", None, 20, 6, query_text="SELECT c FROM sbtest5 WHERE id BETWEEN $1 AND $2")
    degraded_my = _finding("baseline_degradation", None, 20, 7, query_text="DELETE FROM `sbtest3` WHERE `id` = ?")
    own = _finding("baseline_degradation", None, 20, 8, query_text="SELECT pad FROM ql_scn WHERE k = $1")
    metrics = metricas.compute([
        # escenario de antipatrones y bloque de contencion: colateral
        _injection("missing_index", [_ap04(), _ap02(), degraded]),
        _ct_unit([], findings=[degraded_my], engine="mysql"),
        # corrida de control, sin inyeccion: FP
        _control([degraded]),
    ])
    assert _combo(metrics, "AP-01")["fp"] == 1
    assert _combo(metrics, "AP-01", "mysql")["fp"] == 0
    assert sorted((d["tipo"], d["engine"]) for d in metrics["declarados"]) == [
        ("colateral", "mysql"), ("colateral", "postgres")]
    # solo sentencias de sysbench: un AP-01 sobre otra tabla sigue siendo FP
    other = metricas.compute([_injection("missing_index", [_ap04(), _ap02(), own])])
    assert _combo(other, "AP-01")["fp"] == 1


# ------------------------------------------------------------- contencion


def _ct_row(case, rep=1, finding=None, deadlock=True, captured=True, evidence_ok=None, saved=8.0, kind="deadlock"):
    return {"case": case, "phase": "positivo", "rep": rep, "kind": kind, "deadlock": deadlock,
            "captured": captured, "finding": finding, "evidence_ok": evidence_ok, "saved_delay_s": saved,
            "severity": "high"}


def _ct_unit(rows, engine="mysql", stray=(), findings=()):
    return {"id": f"ct|{engine}", "tipo": "ct", "engine": engine, "inicio": START, "fin": _at(600),
            "hallazgos": list(findings),
            "ct": {"results": rows, "summary": {"hallazgos_sin_corrida": list(stray)}}}


CT01, CT02, CT05 = mf.ANTIPATRON["CT-01"], mf.ANTIPATRON["CT-02"], mf.ANTIPATRON["CT-05"]


def test_contention_true_positive_with_m3_and_m4():
    metrics = metricas.compute([_ct_unit([
        _ct_row("ct01", 1, CT01, evidence_ok=True, saved=9.0),
        _ct_row("ct01", 2, CT01, evidence_ok=True, saved=16.0),
    ])])
    ct01 = _combo(metrics, "CT-01", "mysql")
    assert (ct01["vp"], ct01["fn"], ct01["m1"]) == (2, 0, 1.0)
    assert ct01["m3"] == {"detectados": 2, "miembros_ok": 2, "miembros": 1.0,
                          "clasificacion_ok": 2, "clasificacion": 1.0}
    assert ct01["m3_ok"] is True
    # 16 s supera los 15 s de CT-01 a CT-04
    assert (ct01["m4"]["limite_s"], ct01["m4"]["dentro"], ct01["m4_ok"]) == (15, 1, False)


def test_deadlock_classified_as_another_pattern():
    metrics = metricas.compute([_ct_unit([_ct_row("ct01", 1, CT02)])])
    ct01, ct02 = _combo(metrics, "CT-01", "mysql"), _combo(metrics, "CT-02", "mysql")
    # FN de CT-01 y FP de CT-02; los miembros se identificaron, el patron no
    assert (ct01["vp"], ct01["fn"]) == (0, 1)
    assert ct02["fp"] == 1
    assert (ct01["m3"]["miembros"], ct01["m3"]["clasificacion"], ct01["m3_ok"]) == (1.0, 0.0, False)


def test_unclassified_deadlock_is_a_false_negative_without_false_positive():
    metrics = metricas.compute([_ct_unit([_ct_row("ct01", 1, mf.UNCLASSIFIED)])])
    assert _combo(metrics, "CT-01", "mysql")["fn"] == 1
    assert metrics["falsos_positivos"] == []


def test_captured_deadlock_without_finding_counts_as_detected_with_wrong_members():
    metrics = metricas.compute([_ct_unit([_ct_row("ct02", 1, None, captured=True)])])
    ct02 = _combo(metrics, "CT-02", "mysql")
    assert ct02["fn"] == 1 and ct02["m3"]["miembros"] == 0.0


def test_wrong_resources_fail_the_members_proportion():
    metrics = metricas.compute([_ct_unit([_ct_row("ct01", 1, CT01, evidence_ok=False)])])
    ct01 = _combo(metrics, "CT-01", "mysql")
    assert ct01["vp"] == 1
    assert (ct01["m3"]["miembros"], ct01["m3"]["clasificacion"], ct01["m3_ok"]) == (0.0, 1.0, False)


def test_injection_without_deadlock_is_not_counted():
    metrics = metricas.compute([_ct_unit([_ct_row("ct01", 1, None, deadlock=False, captured=False)])])
    ct01 = _combo(metrics, "CT-01", "mysql")
    assert (ct01["inyecciones"], ct01["inyecciones_fallidas"]) == (0, 1)


def test_cascade_uses_its_own_limit_and_evidence():
    metrics = metricas.compute([_ct_unit([
        _ct_row("ct05", 1, CT05, evidence_ok=True, saved=40.0, kind="ct05", deadlock=False),
        _ct_row("ct05", 2, None, saved=None, kind="ct05", deadlock=False),
    ], engine="postgres")])
    ct05 = _combo(metrics, "CT-05", "postgres")
    assert (ct05["vp"], ct05["fn"]) == (1, 1)
    assert (ct05["m4"]["limite_s"], ct05["m4_ok"]) == (45, True)
    assert ct05["m3"]["detectados"] == 1


def test_cases_outside_the_scenarios_of_the_engine_are_ignored():
    rows = [_ct_row("ct01_bind", 1, CT01), {**_ct_row("ct01", 1, CT01), "phase": "carga"}]
    metrics = metricas.compute([_ct_unit(rows, engine="postgres")])
    assert _combo(metrics, "CT-01", "postgres")["inyecciones"] == 0


def test_stray_contention_finding_is_a_false_positive_unless_declared(monkeypatch):
    finding = {**_finding(CT01, "sbtest3", 30, 4, tables=["sbtest3"]), "deteccion_ids": [41, 42]}
    stray = [{"id": 41, "antipatron": CT01}, {"id": 42, "antipatron": CT01}]
    unit = _ct_unit([_ct_row("ct01", 1, CT01)], stray=stray, findings=[finding])
    # las dos detecciones son del mismo problema: un solo FP
    assert _combo(metricas.compute([unit]), "CT-01", "mysql")["fp"] == 1
    base = mf.Collateral("abrazos entre transacciones de sysbench", {"evidencia": {"tables": {"contiene": "sbtest"}}})
    monkeypatch.setitem(mf.LINEA_BASE, "mysql", (base,))
    assert _combo(metricas.compute([unit]), "CT-01", "mysql")["fp"] == 0


def test_stray_unclassified_deadlock_belongs_to_no_combination():
    unit = _ct_unit([], stray=[{"id": 7, "antipatron": mf.UNCLASSIFIED}])
    metrics = metricas.compute([unit])
    assert metrics["falsos_positivos"] == []
    assert [item["antipatron"] for item in metrics["sin_patologia_del_catalogo"]] == [mf.UNCLASSIFIED]


def test_antipattern_findings_during_the_contention_block_are_checked_too():
    spill = _finding("disk_spill", "sbtest1", 20, 9, tables=["sbtest1"])
    metrics = metricas.compute([_ct_unit([], findings=[spill], engine="postgres")])
    assert _combo(metrics, "AP-07", "postgres")["fp"] == 1


# -------------------------------------------------------------------- reporte


def test_report_and_files(tmp_path):
    units = [_injection("missing_index", [_ap04(), _ap02()]), _control([]),
             _ct_unit([_ct_row("ct01", 1, CT01, evidence_ok=True)])]
    import json
    (tmp_path / "corridas.jsonl").write_text("\n".join(json.dumps(u) for u in units) + "\n", encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({"run_id": "prueba", "reps": 1, "controles": 1,
                                                    "minutos_control": 30, "tasa_carga_base": 50}), encoding="utf-8")
    metrics = metricas.write_report(tmp_path)
    assert metrics["resumen"]["evaluadas"] == 3
    report = (tmp_path / "reporte.md").read_text(encoding="utf-8")
    assert "AP-04 indice ausente" in report and "CT-01 abrazo mortal por orden inconsistente" in report
    assert "NO CUMPLE" in report  # 21 combinaciones sin inyecciones
    assert json.loads((tmp_path / "metricas.json").read_text(encoding="utf-8"))["resumen"] == metrics["resumen"]


# ------------------------------------------------------------------------ M7


def test_m7_checklist_counts_each_problem_once_per_run():
    without_risk = {**_ap02(), "con_riesgo": False}
    metrics = metricas.compute([_injection("missing_index", [_ap04(), without_risk])])
    completeness = metrics["completitud"]
    assert (completeness["problemas"], completeness["completos"]) == (2, 1)
    assert completeness["faltantes"]["con_riesgo"] == 1
    assert completeness["incompletos"][0]["faltan"] == ["con_riesgo"]


def _linked(finding, without_link=0, without_evidence=0):
    return {**finding, "detecciones_sin_enlace": without_link, "detecciones_sin_evidencia": without_evidence}


def test_m7_is_not_measurable_in_a_run_older_than_the_snapshot_link():
    metrics = metricas.compute([_injection("missing_index", [_ap04(), _ap02()])])
    assert metrics["resumen"]["M7"]["veredicto"] == "NO MEDIBLE"
    assert metrics["completitud"]["sin_dato_de_enlace"] == 2


def test_m7_passes_when_every_detection_is_linked_to_its_snapshot():
    metrics = metricas.compute([_injection("missing_index", [_linked(_ap04()), _linked(_ap02())])])
    assert metrics["resumen"]["M7"]["veredicto"] == "CUMPLE"
    assert (metrics["completitud"]["problemas"], metrics["completitud"]["completos"]) == (2, 2)


def test_m7_fails_when_a_detection_has_no_snapshot_or_no_evidence():
    # una deteccion sin instantanea archivada, u otra sin su evidencia, deja incompleto al problema
    findings = [_linked(_ap04(), without_link=1), _linked(_ap02(), without_evidence=2)]
    metrics = metricas.compute([_injection("missing_index", findings)])
    completeness = metrics["completitud"]
    assert metrics["resumen"]["M7"]["veredicto"] == "NO CUMPLE"
    assert completeness["faltantes"]["con_enlace_a_instantanea"] == 1
    assert completeness["faltantes"]["con_evidencia"] == 1
    assert [item["faltan"] for item in completeness["incompletos"]] == [["con_enlace_a_instantanea"], ["con_evidencia"]]
