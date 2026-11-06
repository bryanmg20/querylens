"""Funciones puras de la medicion v2: sin I/O, sin red, sin reloj.

Unidad de analisis: el PAR de bloques (uno de cada brazo, orden aleatorio).
Dentro de cada par se comparan las mismas plantillas de query; entre pares se
remuestrea para el intervalo de confianza (bootstrap por pares), que respeta
la deriva temporal del host mejor que remuestrear queries sueltas.
"""
import math
import random

UMBRAL_PCT = 5.0
MIN_SAMPLES_PER_TEMPLATE = 50
BOOTSTRAP_ITERS = 2000
CI_LEVEL = 0.95

CUMPLE, NO_CUMPLE, INCONCLUSO, SIN_DATO = "CUMPLE", "NO CUMPLE", "INCONCLUSO", "SIN_DATO"
EXIT_CODES = {CUMPLE: 0, SIN_DATO: 1, NO_CUMPLE: 2, INCONCLUSO: 3}


# --- percentiles ---------------------------------------------------------------

def percentile(sorted_vals, pct):
    """Interpolacion lineal (mismo criterio que measure_overhead.percentile)."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def summarize(durations_ms):
    """n / p50 / p95 / media de una lista de duraciones (ms)."""
    if not durations_ms:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    s = sorted(durations_ms)
    return {"n": len(s), "p50_ms": percentile(s, 50), "p95_ms": percentile(s, 95),
            "mean_ms": sum(s) / len(s)}


# --- calendario ------------------------------------------------------------------

def schedule(n_pairs, seed, first="A", second="B"):
    """Orden de cada par. Mitad AB y mitad BA (con n impar sobra uno al azar),
    barajado con la semilla: la deriva del host no cae siempre del mismo lado."""
    rng = random.Random(seed)
    half = n_pairs // 2
    orders = [(first, second)] * half + [(second, first)] * half
    if n_pairs % 2:
        orders.append(rng.choice([(first, second), (second, first)]))
    rng.shuffle(orders)
    return orders


def interleave(arms, seed):
    """Calendario de la sesion: los pares de TODOS los brazos mezclados al azar.

    arms: [(brazo, n_pares, base, tratamiento)]. Devuelve [(brazo, par, orden)].
    Correr brazo por brazo deja un evento pasajero del host (el primer
    checkpoint tras levantar el contenedor, por ejemplo) entero dentro de un
    solo brazo; mezclados, lo reparte al azar."""
    rng = random.Random(seed)
    units = []
    for arm, n_pairs, base, treat in arms:
        for pair, order in enumerate(schedule(n_pairs, rng.randrange(1 << 30), base, treat)):
            units.append((arm, pair, order))
    rng.shuffle(units)
    return units


# --- muestras por bloque ----------------------------------------------------------

def samples_in_block(samples, start_ns, end_ns, settle_ns):
    """Queries OK que EMPEZARON despues de start+settle y TERMINARON antes de end.

    samples: iterable de (t0_ns, t1_ns, template_id, ok). Una query que cruza
    el borde no se cuenta en ningun bloque: su latencia mezcla dos brazos.
    """
    lo = start_ns + settle_ns
    return [s for s in samples if s[3] and s[0] >= lo and s[1] <= end_ns]


def failures_in_block(samples, start_ns, end_ns):
    return sum(1 for s in samples if not s[3] and start_ns <= s[0] and s[1] <= end_ns)


def by_template(block_samples):
    """{template_id: [duracion_ms, ...]}"""
    out = {}
    for t0, t1, tid, _ok in block_samples:
        out.setdefault(tid, []).append((t1 - t0) / 1e6)
    return out


def overlaps(sample, cycles):
    """True si la query se solapa con algun ciclo del pipeline [(t0, t1), ...]."""
    t0, t1 = sample[0], sample[1]
    return any(c0 < t1 and t0 < c1 for c0, c1 in cycles)


# --- sobrecosto ---------------------------------------------------------------------

def _pool(pairs, side):
    """Junta las duraciones por plantilla del lado 'base' o 'treat' de los pares."""
    pooled = {}
    for pair in pairs:
        for tid, vals in pair[side].items():
            pooled.setdefault(tid, []).extend(vals)
    return pooled


def _stat(vals, metric):
    if not vals:
        return None
    if metric == "mean":
        return sum(vals) / len(vals)
    return percentile(sorted(vals), 95 if metric == "p95" else 50)


def geomean_ratio_pct(base, treat, metric="p95", min_n=1):
    """Sobrecosto en % = media geometrica de stat(treat)/stat(base) por
    plantilla, menos 1. Cada plantilla pesa igual: una mezcla distinta de
    queries entre bloques no mueve el resultado. Plantillas con menos de min_n
    muestras en algun lado quedan fuera."""
    logs = []
    for tid in sorted(set(base) & set(treat)):
        b, t = base[tid], treat[tid]
        if len(b) < min_n or len(t) < min_n:
            continue
        sb, st = _stat(b, metric), _stat(t, metric)
        if sb is None or st is None or sb <= 0 or st <= 0:
            continue
        logs.append(math.log(st / sb))
    if not logs:
        return None
    return (math.exp(sum(logs) / len(logs)) - 1.0) * 100.0


def throughput_overhead_pct(pairs):
    """Caida de throughput (queries OK/s) de treat respecto de base, en %."""
    nb = sum(p["base_n"] for p in pairs)
    nt = sum(p["treat_n"] for p in pairs)
    sb = sum(p["base_s"] for p in pairs)
    st = sum(p["treat_s"] for p in pairs)
    if not sb or not st or not nb:
        return None
    qb, qt = nb / sb, nt / st
    return (qb - qt) / qb * 100.0


def bootstrap_ci(pairs, fn, iters=BOOTSTRAP_ITERS, seed=0, level=CI_LEVEL):
    """IC por bootstrap de pares: remuestrea pares completos con reposicion y
    aplica fn(pairs_remuestreados). Devuelve (lo, hi) o (None, None)."""
    if len(pairs) < 2:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        v = fn(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < iters * 0.9:
        return None, None
    vals.sort()
    alpha = (1.0 - level) / 2.0
    return percentile(vals, alpha * 100), percentile(vals, (1 - alpha) * 100)


def min_template_n(pairs, side):
    pooled = _pool(pairs, side)
    return min((len(v) for v in pooled.values()), default=0)


def analyze_arm(pairs, seed=0, iters=BOOTSTRAP_ITERS, min_n=MIN_SAMPLES_PER_TEMPLATE):
    """Resultado de un brazo: punto + IC de p95, media y throughput.

    pairs: [{"base": {tid: [ms]}, "treat": {tid: [ms]},
             "base_n", "treat_n", "base_s", "treat_s"}, ...]
    """
    def p95(ps):
        return geomean_ratio_pct(_pool(ps, "base"), _pool(ps, "treat"), "p95")

    def mean(ps):
        return geomean_ratio_pct(_pool(ps, "base"), _pool(ps, "treat"), "mean")

    out = {"pairs": len(pairs),
           "min_template_n_base": min_template_n(pairs, "base"),
           "min_template_n_treat": min_template_n(pairs, "treat")}
    for name, fn in (("p95", p95), ("mean", mean), ("throughput", throughput_overhead_pct)):
        lo, hi = bootstrap_ci(pairs, fn, iters=iters, seed=seed)
        out[f"{name}_pct"] = fn(pairs)
        out[f"{name}_ci"] = [lo, hi]
    out["enough_samples"] = min(out["min_template_n_base"], out["min_template_n_treat"]) >= min_n
    return out


def verdict(point, ci, enough_samples, umbral=UMBRAL_PCT):
    """Tri-estado sobre el IC: CUMPLE solo si toda la banda queda bajo el umbral."""
    lo, hi = ci if ci else (None, None)
    if not enough_samples or point is None or lo is None or hi is None:
        return SIN_DATO
    if hi < umbral:
        return CUMPLE
    if lo >= umbral:
        return NO_CUMPLE
    return INCONCLUSO


def detected(ci, umbral=UMBRAL_PCT):
    """Control positivo: el arnes lo ve si lo hubiera declarado NO CUMPLE."""
    lo = (ci or (None, None))[0]
    if lo is None:
        return None
    return lo >= umbral


def noise_ok(ci, umbral=UMBRAL_PCT):
    """El A/A tiene que caer entero dentro de +-umbral: si no, el host no
    resuelve diferencias del tamano del umbral."""
    lo, hi = ci if ci else (None, None)
    if lo is None or hi is None:
        return None
    return -umbral < lo and hi < umbral
