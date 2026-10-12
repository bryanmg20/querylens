"""Estadistica de la medicion de sobrecosto v2 (overhead/stats.py).

Sin datos => SIN_DATO; un IC que cruza el umbral => INCONCLUSO. No se asume CUMPLE.
"""
import pytest

from overhead import stats as st

pytestmark = pytest.mark.unit

S = 1_000_000_000  # 1 s en ns


def _pair(base, treat, base_s=10.0, treat_s=10.0):
    return {"base": base, "treat": treat,
            "base_n": sum(len(v) for v in base.values()),
            "treat_n": sum(len(v) for v in treat.values()),
            "base_s": base_s, "treat_s": treat_s}


def _const(n, ms, templates=(0, 1)):
    return {t: [ms * (t + 1)] * n for t in templates}


def test_umbral_es_el_del_primer_informe():
    assert st.UMBRAL_PCT == 5.0


# --- percentiles --------------------------------------------------------------------

@pytest.mark.parametrize("vals,pct,esperado", [
    ([10.0], 50, 10.0),
    ([1.0, 2.0, 3.0, 4.0], 50, 2.5),
    ([5.0, 10.0, 15.0, 20.0, 25.0], 95, 24.0),
    ([], 50, None),
])
def test_percentile(vals, pct, esperado):
    assert st.percentile(vals, pct) == (pytest.approx(esperado) if esperado else None)


def test_summarize_vacio_no_inventa():
    assert st.summarize([]) == {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}


# --- calendario ---------------------------------------------------------------------

@pytest.mark.parametrize("n", [2, 6, 10])
def test_schedule_balancea_ab_y_ba(n):
    orders = st.schedule(n, seed=7)
    assert len(orders) == n
    assert orders.count(("A", "B")) == orders.count(("B", "A")) == n // 2


def test_schedule_impar_suma_un_par_y_es_determinista():
    assert len(st.schedule(5, seed=1)) == 5
    assert st.schedule(5, seed=1) == st.schedule(5, seed=1)


def test_schedule_usa_las_etiquetas():
    assert set(st.schedule(4, seed=3, first="off", second="on")) == {("off", "on"), ("on", "off")}


def test_interleave_cubre_todos_los_pares_y_mezcla_brazos():
    arms = [("aa", 3, "A1", "A2"), ("pipeline", 10, "off", "on"), ("instrumentation", 6, "off", "on")]
    units = st.interleave(arms, seed=5)
    assert len(units) == 19
    for arm, n, base, treat in arms:
        mine = [u for u in units if u[0] == arm]
        assert sorted(p for _, p, _ in mine) == list(range(n))
        assert all(set(order) == {base, treat} for _, _, order in mine)
    # No corre brazo por brazo: los primeros pares no son todos del mismo brazo.
    assert len({arm for arm, _, _ in units[:6]}) > 1
    assert st.interleave(arms, seed=5) == units


# --- muestras por bloque --------------------------------------------------------------

def test_samples_in_block_descarta_settle_bordes_y_fallos():
    samples = [
        (1 * S, 2 * S, 0, True),     # dentro del settle
        (6 * S, 7 * S, 0, True),     # cuenta
        (9 * S, 11 * S, 1, True),    # cruza el fin del bloque
        (7 * S, 8 * S, 1, False),    # fallo
        (5 * S, 6 * S, 1, True),     # empieza justo al terminar el settle
    ]
    got = st.samples_in_block(samples, 0, 10 * S, 5 * S)
    assert got == [(6 * S, 7 * S, 0, True), (5 * S, 6 * S, 1, True)]
    assert st.failures_in_block(samples, 0, 10 * S) == 1


def test_by_template_en_ms():
    assert st.by_template([(0, 2_000_000, 3, True), (0, 500_000, 3, True)]) == {3: [2.0, 0.5]}


@pytest.mark.parametrize("sample,esperado", [
    ((5, 12, 0, True), True),    # cruza el inicio del ciclo
    ((12, 15, 0, True), True),   # dentro
    ((20, 25, 0, True), False),  # empieza justo cuando el ciclo termina
    ((0, 10, 0, True), False),   # termina justo cuando el ciclo empieza
])
def test_overlaps_bordes(sample, esperado):
    assert st.overlaps(sample, [(10, 20)]) is esperado


# --- sobrecosto -----------------------------------------------------------------------

def test_geomean_igual_peso_por_plantilla():
    base = {0: [10.0] * 10, 1: [100.0] * 10}
    treat = {0: [11.0] * 10, 1: [100.0] * 10}  # +10 % y 0 %
    assert st.geomean_ratio_pct(base, treat, "mean") == pytest.approx(
        ((1.1 * 1.0) ** 0.5 - 1) * 100)


def test_geomean_ignora_plantillas_con_pocas_muestras():
    base = {0: [10.0] * 10, 1: [10.0]}
    treat = {0: [10.0] * 10, 1: [50.0]}
    assert st.geomean_ratio_pct(base, treat, "mean", min_n=5) == pytest.approx(0.0)
    assert st.geomean_ratio_pct({}, {}, "p95") is None


def test_throughput_overhead():
    pairs = [_pair(_const(100, 1.0, (0,)), _const(90, 1.0, (0,)))]
    assert st.throughput_overhead_pct(pairs) == pytest.approx(10.0)


def test_bootstrap_determinista_y_con_menos_de_dos_pares_sin_ic():
    pairs = [_pair(_const(60, 10.0), _const(60, 10.0 + i)) for i in range(4)]
    fn = lambda ps: st.geomean_ratio_pct(st._pool(ps, "base"), st._pool(ps, "treat"), "mean")
    assert st.bootstrap_ci(pairs, fn, iters=200, seed=1) == st.bootstrap_ci(pairs, fn, iters=200, seed=1)
    assert st.bootstrap_ci(pairs[:1], fn, iters=200) == (None, None)


def test_analyze_arm_sin_costo_cumple():
    pairs = [_pair(_const(60, 10.0), _const(60, 10.0)) for _ in range(4)]
    res = st.analyze_arm(pairs, iters=200)
    assert res["p95_pct"] == pytest.approx(0.0)
    assert res["enough_samples"] is True
    assert st.verdict(res["p95_pct"], res["p95_ci"], res["enough_samples"]) == st.CUMPLE


def test_analyze_arm_costo_claro_no_cumple():
    pairs = [_pair(_const(60, 10.0), _const(60, 12.0 + 0.1 * i)) for i in range(4)]
    res = st.analyze_arm(pairs, iters=200)
    assert res["p95_ci"][0] >= 5.0
    assert st.verdict(res["p95_pct"], res["p95_ci"], res["enough_samples"]) == st.NO_CUMPLE


def test_analyze_arm_pocas_muestras_es_sin_dato():
    pairs = [_pair(_const(10, 10.0), _const(10, 10.0)) for _ in range(3)]
    res = st.analyze_arm(pairs, iters=100)
    assert res["enough_samples"] is False
    assert st.verdict(res["p95_pct"], res["p95_ci"], res["enough_samples"]) == st.SIN_DATO


# --- veredicto --------------------------------------------------------------------------

@pytest.mark.parametrize("point,ci,enough,esperado", [
    (1.0, (-2.0, 4.9), True, st.CUMPLE),
    (3.0, (0.5, 5.0), True, st.INCONCLUSO),   # la cota superior toca el umbral
    (6.0, (5.0, 7.0), True, st.NO_CUMPLE),    # la cota inferior en el umbral
    (4.0, (2.0, 8.0), True, st.INCONCLUSO),
    (1.0, (-1.0, 2.0), False, st.SIN_DATO),
    (None, (None, None), True, st.SIN_DATO),
    (1.0, (None, None), True, st.SIN_DATO),
])
def test_verdict_tri_estado(point, ci, enough, esperado):
    assert st.verdict(point, ci, enough) == esperado


@pytest.mark.parametrize("ci,esperado", [
    ((-3.0, 3.0), True),
    ((-6.0, 1.0), False),
    ((0.0, 5.0), False),
    ((None, None), None),
])
def test_noise_ok(ci, esperado):
    assert st.noise_ok(ci) is esperado


def test_exit_codes():
    assert st.EXIT_CODES == {st.CUMPLE: 0, st.SIN_DATO: 1, st.NO_CUMPLE: 2, st.INCONCLUSO: 3}


@pytest.mark.parametrize("ci,esperado", [
    ((37.0, 68.0), True),
    ((5.0, 9.0), True),
    ((4.9, 30.0), False),
    ((None, None), None),
])
def test_control_positivo_detectado_si_seria_no_cumple(ci, esperado):
    assert st.detected(ci) is esperado
