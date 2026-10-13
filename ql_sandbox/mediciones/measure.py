"""
QUERYLENS — medicion de deteccion de AP-07, AP-01 y AP-06 con muchos casos.

Por cada antipatron y motor corre ~100 consultas patologicas distintas (cada una
con su propio query_id) y ~40 consultas sanas AL MISMO TIEMPO, toma un snapshot
real cada 60 s (telemetry_pipeline en querylens_pipeline, detection_engine en
querylens_detection_engine) y compara los hallazgos contra lo que de verdad paso
en cada ventana, reconstruido desde los snapshots archivados en PGMQ.

USO (desde la raiz del repo; solo libreria estandar)
  python ql_sandbox/mediciones/measure.py ap07 --engine postgres
  python ql_sandbox/mediciones/measure.py all --engine both
  python ql_sandbox/mediciones/measure.py ap01 --engine mysql --smoke   # prueba corta

Ver README.md (metodo, criterios de validez y lectura del reporte).
"""

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MED_DIR = Path(__file__).resolve().parent
RESULTS_DIR = MED_DIR / "resultados"

IMAGE = "querylens-pipeline"
DRIVER = "ql_meas_driver"
PIPELINE_CONTAINER = "querylens_pipeline"
QUERYLENS_CONTAINER = "querylens_db"
MYSQL_CONTAINER = "ql_mysql"

ANTIPATRON = {"ap07": "disk_spill", "ap01": "baseline_degradation", "ap06": "n_plus_one"}
NOMBRE = {"ap07": "AP-07 vertido a disco", "ap01": "AP-01 degradacion respecto de la linea base",
          "ap06": "AP-06 patron N+1"}

# ciclos (snapshots) por medicion; el 1 solo deja la historia
PLAN = {
    # warmup s, ciclos, ciclo tras el cual se inyecta
    "ap07": (40, 5, None),
    "ap06": (40, 5, None),
    # 1 (historia) + 10 de linea base + 3 despues de la degradacion
    "ap01": (20, 14, 11),
}
# AP-01: el informe pide el hallazgo en <= 2 ventanas despues de la degradacion
AP01_MAX_WINDOWS = 2

# AP-07 en MySQL, como el escenario del informe: con temptable_use_mmap = ON la
# tabla temporal que no cabe en RAM va a archivos mmap y SUM_CREATED_TMP_DISK_TABLES
# no la cuenta; con OFF va a InnoDB en disco y si
SERVER_SETUP = {("ap07", "mysql"): "SET GLOBAL temptable_use_mmap = OFF;"}
SERVER_TEARDOWN = {("ap07", "mysql"): "SET GLOBAL temptable_use_mmap = ON;"}


# ------------------------------------------------------------------ utilidades

def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(cmd, input_text=None, check=True):
    result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"Fallo: {' '.join(cmd[:8])}\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def querylens_sql(sql):
    return run(["docker", "exec", "-i", QUERYLENS_CONTAINER, "psql", "-U", "ql_user", "-d", "ql_demo", "-At",
                "-v", "ON_ERROR_STOP=1"], input_text=sql).strip()


def queue_length():
    return int(querylens_sql("SELECT queue_length FROM pgmq.metrics('analyze_job');") or 0)


def wait_queue_empty(timeout):
    deadline = time.monotonic() + timeout
    while queue_length() and time.monotonic() < deadline:
        time.sleep(1)
    return queue_length() == 0


def _posix(path):
    return str(path).replace("\\", "/")


def driver(args, out_rel, detach=False):
    cmd = ["docker", "run", "--rm"]
    if detach:
        cmd += ["-d", "--name", DRIVER]
    cmd += ["-v", f"{_posix(MED_DIR)}:/m:ro", "-v", f"{_posix(RESULTS_DIR)}:/out", IMAGE,
            "python", "-u", "/m/driver.py", args[0], args[1], args[2], f"/out/{out_rel}", *args[3:]]
    if detach:
        return run(cmd).strip()
    output = run(cmd)
    for line in output.splitlines():
        if line.startswith("[driver"):
            print("   " + line, flush=True)
    return output


def mysql_root(sql):
    return run(["docker", "exec", "-i", MYSQL_CONTAINER, "mysql", "-uroot", "-pql_root", "-N"], input_text=sql)


def take_snapshot(engine, db_id):
    output = run(["docker", "exec", "-w", "/app", PIPELINE_CONTAINER, "python", "/tmp/pipeline_snapshot.py",
                  engine, db_id])
    for line in output.splitlines():
        if line.startswith("msg_id="):
            return int(line.split("=", 1)[1])
    raise RuntimeError(f"el pipeline no devolvio msg_id:\n{output[-2000:]}")


def wilson(successes, total, z=1.96):
    if total == 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [round(center - half, 4), round(center + half, 4)]


def pct(a, b):
    return f"{100 * a / b:.1f} %" if b else "—"


# ------------------------------------------------------------------ datos de la corrida

def fetch_findings(db_id, antipatron):
    raw = querylens_sql(
        "SELECT coalesce(json_agg(t ORDER BY t.detectado_en), '[]') FROM ("
        " SELECT antipatron, severidad, query_id, table_name, evidencia, detectado_en"
        f" FROM public.hallazgos WHERE db_id = '{db_id}' AND antipatron = '{antipatron}') t;")
    return json.loads(raw or "[]")


def fetch_snapshot_rows(msg_id, qids):
    """Contadores de las consultas de la corrida en un snapshot archivado."""
    array = ",".join("'" + q.replace("'", "") + "'" for q in qids)
    raw = querylens_sql(
        "SELECT json_build_object("
        " 'enqueued_at', a.enqueued_at,"
        " 'statements', (SELECT coalesce(json_agg(json_build_object("
        "     'qid', s->>'query_id', 'calls', s->>'execution_count', 'total_ms', s->>'total_time_ms',"
        "     'rows', s->>'rows_returned', 'spill', s->>'disk_spill_indicator', 'merge', s->>'sort_merge_passes',"
        "     'epoch', s->>'counters_epoch')), '[]')"
        f"   FROM jsonb_array_elements(a.message->'statements') s WHERE lower(s->>'query_id') = ANY(ARRAY[{array}])),"
        " 'top_impact', (SELECT coalesce(json_agg(lower(t->>'query_id')), '[]')"
        "   FROM jsonb_array_elements(a.message->'top_impact_queries') t))"
        f" FROM pgmq.a_analyze_job a WHERE a.msg_id = {msg_id};")
    if not raw:
        return None
    data = json.loads(raw)
    counters = {}
    for row in data["statements"]:
        qid = row["qid"].lower()
        entry = counters.setdefault(qid, {"calls": 0, "total_ms": 0.0, "rows": 0, "spill": 0, "merge": 0,
                                          "epoch": row["epoch"]})
        for key, cast in (("calls", int), ("total_ms", float), ("rows", int), ("spill", int), ("merge", int)):
            if row[key] is not None:
                entry[key] += cast(float(row[key]))
    return {"enqueued_at": data["enqueued_at"], "counters": counters, "top_impact": set(data["top_impact"] or [])}


def windows(snapshots, qid):
    """{ciclo: delta} entre el snapshot anterior y el del ciclo (desde el 2)."""
    out = {}
    for cycle in range(2, len(snapshots) + 1):
        prev, cur = snapshots[cycle - 2], snapshots[cycle - 1]
        if prev is None or cur is None:
            continue
        a, b = prev["counters"].get(qid), cur["counters"].get(qid)
        if b is None:
            continue
        if a is None:
            a = {"calls": 0, "total_ms": 0.0, "rows": 0, "spill": 0, "merge": 0, "epoch": b["epoch"]}
        if a["epoch"] != b["epoch"] and a["calls"]:
            continue  # contadores reiniciados
        delta = {key: b[key] - a[key] for key in ("calls", "total_ms", "rows", "spill", "merge")}
        if delta["calls"] < 0:
            continue
        delta["mean_ms"] = delta["total_ms"] / delta["calls"] if delta["calls"] else None
        out[cycle] = delta
    return out


def parse_ts(value):
    # psql: "2026-10-12 23:44:07.12+00"; json de Postgres: "2026-10-12T23:44:07.12+00:00"
    text = str(value).replace("T", " ")
    if len(text) >= 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    return datetime.fromisoformat(text)


def finding_cycles(findings, cycles):
    """query_id -> {ciclo: hallazgo}, por la hora del hallazgo dentro de cada ciclo."""
    out = {}
    spans = [(c["cycle"], parse_ts(c["start"]), parse_ts(c["end"])) for c in cycles]
    for finding in findings:
        at = parse_ts(finding["detectado_en"])
        cycle = None
        for number, start, end in spans:
            if start <= at <= end:
                cycle = number
        if cycle is None:
            earlier = [number for number, start, _ in spans if start <= at]
            cycle = earlier[-1] if earlier else 0
        out.setdefault(str(finding["query_id"]).lower(), {})[cycle] = finding
    return out


# ------------------------------------------------------------------ evaluacion

def evaluate_ap07(cases, snapshots, by_qid, n_cycles, engine):
    measured = range(2, n_cycles + 1)
    results = []
    for case in cases:
        qid = case["query_ids"]["target"].lower()
        w = windows(snapshots, qid)
        # vertido en la ventana: cualquiera de los indicadores que lee AP-07
        # (merge passes solo existe en MySQL)
        spilled = sorted(c for c, d in w.items() if c in measured and (d["spill"] > 0 or d["merge"] > 0))
        detected = sorted(c for c in by_qid.get(qid, {}) if c in measured)
        results.append({
            "cid": case["cid"], "role": case["role"], "category": case["category"], "qid": qid,
            "query": case["queries"]["target"], "meta": case["meta"],
            "executed_windows": sorted(c for c, d in w.items() if c in measured and d["calls"] > 0),
            "spilled_windows": spilled, "detected_windows": detected,
            "detected_cycle1": 1 in by_qid.get(qid, {}),
            "in_top_impact": [c for c in range(1, n_cycles + 1)
                              if snapshots[c - 1] and qid in snapshots[c - 1]["top_impact"]],
            "valid": bool(spilled), "detected": bool(detected),
            "detected_when_spilled": sorted(set(spilled) & set(detected)),
            "false_windows": sorted(set(detected) - set(spilled)),
            # falso positivo: marcado en una ventana en la que no derramo
            "fp": bool(set(detected) - set(spilled)),
        })
    return results


def evaluate_ap01(cases, snapshots, by_qid, n_cycles, inject_after, engine):
    after = range(inject_after + 1, n_cycles + 1)
    allowed = range(inject_after + 1, inject_after + 1 + AP01_MAX_WINDOWS)
    results = []
    for case in cases:
        qid = case["query_ids"]["target"].lower()
        w = windows(snapshots, qid)
        means = []  # ventanas validas (>= 30 ejecuciones) en orden, como statement_history
        rule = {}
        for cycle in sorted(w):
            d = w[cycle]
            if cycle in after and d["calls"] >= 30 and len(means) >= 10:
                base = sorted(means[-10:])
                median = (base[4] + base[5]) / 2
                rule[cycle] = {"mean_ms": round(d["mean_ms"], 3), "median_ms": round(median, 3),
                               "ratio": round(d["mean_ms"] / median, 2) if median else None,
                               "calls": d["calls"],
                               "degradada": bool(median and d["mean_ms"] >= 2 * median and d["mean_ms"] - median >= 1)}
            if d["calls"] >= 30:
                means.append(d["mean_ms"])
        detections = by_qid.get(qid, {})
        before = sorted(c for c in detections if c <= inject_after)
        after_det = sorted(c for c in detections if c in after)
        baseline_ok = sum(1 for c in range(2, inject_after + 1) if c in w and w[c]["calls"] >= 30)
        # marcado con la regla cumplida en sus contadores (degradacion real, p. ej. por la
        # contencion de la propia inyeccion) o sin cumplirla (falso positivo del detector)
        met = sorted(c for c in after_det if rule.get(c, {}).get("degradada"))
        not_met = sorted(c for c in after_det if not rule.get(c, {}).get("degradada"))
        results.append({
            "cid": case["cid"], "role": case["role"], "category": case["category"], "qid": qid,
            "query": case["queries"]["target"], "meta": case["meta"],
            "baseline_windows": baseline_ok,
            "baseline_mean_ms": round(sum(means[:10]) / max(1, len(means[:10])), 3) if means else None,
            "after": rule,
            "valid": any(rule.get(c, {}).get("degradada") for c in allowed),
            "detected": any(c in allowed for c in after_det),
            "detected_late": bool(after_det) and not any(c in allowed for c in after_det),
            "detected_windows": sorted(detections),
            "first_window_after": (after_det[0] - inject_after) if after_det else None,
            "false_before_injection": before,
            "flagged_rule_met": met,
            "flagged_rule_not_met": not_met,
            "fp": bool(before or not_met),
            "calls_per_window": [w[c]["calls"] for c in sorted(w)],
        })
    return results


def evaluate_ap06(cases, snapshots, by_qid, n_cycles, findings, engine):
    measured = range(2, n_cycles + 1)
    known = {}
    for case in cases:
        for name, qid in case["query_ids"].items():
            known[qid.lower()] = (case["cid"], name, case["role"])
    results = []
    for case in cases:
        qid = case["query_ids"]["target"].lower()
        pqid = case["query_ids"].get("parent", "").lower() or None
        wc = windows(snapshots, qid)
        wp = windows(snapshots, pqid) if pqid else {}
        valid_windows = []
        detail = {}
        for cycle in measured:
            c, p = wc.get(cycle), wp.get(cycle)
            if not c:
                continue
            entry = {"child_calls": c["calls"]}
            if p and p["rows"]:
                dev = abs(c["calls"] - p["rows"]) / p["rows"]
                entry.update(parent_calls=p["calls"], parent_rows=p["rows"], desvio=round(dev, 3))
                if c["calls"] >= 100 and p["rows"] >= 2 * p["calls"] and dev <= 0.20:
                    valid_windows.append(cycle)
            detail[cycle] = entry
        detections = {cyc: f for cyc, f in by_qid.get(qid, {}).items() if cyc in measured}
        parents_found = sorted({str((f.get("evidencia") or {}).get("parent_query_id")).lower()
                                for f in detections.values()})
        results.append({
            "cid": case["cid"], "role": case["role"], "category": case["category"], "qid": qid,
            "parent_qid": pqid, "query": case["queries"]["target"], "parent_query": case["queries"].get("parent"),
            "meta": case["meta"], "windows": detail,
            "valid_windows": valid_windows, "detected_windows": sorted(detections),
            "valid": bool(valid_windows), "detected": bool(detections),
            "pairing_ok": (pqid in parents_found) if (pqid and detections) else None,
            "fp": bool(detections),
            "parents_found": [known.get(p, (p, "?", "?"))[0] for p in parents_found],
        })
    # hallazgos sobre consultas que no son la hija de ningun caso positivo o extra
    child_qids = {c["query_ids"]["target"].lower() for c in cases if c["role"] in ("positivo", "extra")}
    others = [{"query_id": f["query_id"], "caso": known.get(str(f["query_id"]).lower(), ("desconocida",))[0],
               "consulta": (f.get("evidencia") or {}).get("query_text")}
              for f in findings if str(f["query_id"]).lower() not in child_qids]
    return results, others


# ------------------------------------------------------------------ reporte

def summarize(med, engine, results, extra=None):
    pos = [r for r in results if r["role"] == "positivo"]
    ctl = [r for r in results if r["role"] == "control"]
    ext = [r for r in results if r["role"] == "extra"]
    det = sum(r["detected"] for r in pos)
    valid = [r for r in pos if r["valid"]]
    det_valid = sum(r["detected"] for r in valid)
    fp = [r for r in ctl if r["fp"]]
    flagged = [r for r in ctl if r["detected"]]
    summary = {
        "positivos": len(pos), "detectados": det, "recall": round(det / len(pos), 4) if pos else None,
        "recall_ic95": wilson(det, len(pos)),
        "positivos_validos": len(valid), "detectados_validos": det_valid,
        "recall_validos": round(det_valid / len(valid), 4) if valid else None,
        "recall_validos_ic95": wilson(det_valid, len(valid)),
        "controles": len(ctl), "controles_marcados": len(flagged), "falsos_positivos": len(fp), "tasa_fp": round(len(fp) / len(ctl), 4) if ctl else None,
        "tasa_fp_ic95": wilson(len(fp), len(ctl)),
        "extra": len(ext), "extra_detectados": sum(r["detected"] for r in ext),
        "cumple_90": bool(pos) and det / len(pos) >= 0.90,
    }
    by_cat = {}
    for r in pos + ext:
        entry = by_cat.setdefault(r["category"], {"casos": 0, "validos": 0, "detectados": 0})
        entry["casos"] += 1
        entry["validos"] += r["valid"]
        entry["detectados"] += r["detected"]
    summary["por_categoria"] = by_cat
    ctl_cat = {}
    for r in ctl:
        entry = ctl_cat.setdefault(r["category"], {"casos": 0, "marcados": 0, "falsos_positivos": 0})
        entry["casos"] += 1
        entry["marcados"] += r["detected"]
        entry["falsos_positivos"] += r["fp"]
    summary["controles_por_categoria"] = ctl_cat
    if med == "ap07":
        spilled = sum(len(r["spilled_windows"]) for r in pos)
        hit = sum(len(r["detected_when_spilled"]) for r in pos)
        summary["ventanas_con_spill"] = spilled
        summary["ventanas_detectadas"] = hit
        summary["recall_por_ventana"] = round(hit / spilled, 4) if spilled else None
        summary["controles_que_derramaron"] = sum(bool(r["spilled_windows"]) for r in ctl)
        summary["fp_en_ventana_sin_spill"] = sum(bool(r["false_windows"]) for r in ctl)
    if med == "ap01":
        summary["fp_antes_de_degradar"] = sum(bool(r["false_before_injection"]) for r in pos + ctl)
        summary["controles_degradados_de_verdad"] = sum(bool(r["flagged_rule_met"]) and not r["fp"] for r in ctl)
        summary["detectados_tarde"] = sum(r["detected_late"] for r in pos)
        lat = [r["first_window_after"] for r in pos if r["detected"]]
        summary["latencia_ventanas"] = {str(k): lat.count(k) for k in sorted(set(lat))}
    if med == "ap06":
        valid_w = sum(len(r["valid_windows"]) for r in pos)
        hit_w = sum(len(set(r["valid_windows"]) & set(r["detected_windows"])) for r in pos)
        summary["ventanas_validas"] = valid_w
        summary["ventanas_detectadas"] = hit_w
        summary["recall_por_ventana"] = round(hit_w / valid_w, 4) if valid_w else None
        paired = [r for r in pos if r["pairing_ok"] is not None]
        summary["padre_correcto"] = f"{sum(r['pairing_ok'] for r in paired)}/{len(paired)}"
        summary["hallazgos_fuera_de_casos"] = extra or []
    return summary


def markdown(med, engine, run_id, summary, results, meta):
    s = summary
    lines = [f"# {NOMBRE[med]} — {engine}", "",
             f"Corrida `{run_id}` · db_id `{meta['db_id']}` · {meta['cycles']} snapshots cada {meta['interval']} s"
             f" · inicio {meta['started']}"
             + (f" · temptable_max_ram {meta['temptable_max_ram'] // 1048576} MiB durante la corrida"
                if meta.get("temptable_max_ram") else ""), ""]
    ic = s["recall_ic95"]
    lines += [
        "## Resultado", "",
        "| Métrica | Valor |", "|---|---|",
        f"| Positivos detectados | **{s['detectados']}/{s['positivos']} ({pct(s['detectados'], s['positivos'])})**"
        f"{f' · IC 95 % {ic[0]*100:.1f}–{ic[1]*100:.1f} %' if ic else ''} |",
        f"| ¿Cumple ≥ 90 %? | {'sí' if s['cumple_90'] else '**no**'} |",
        f"| Positivos donde la patología se verificó en los contadores | {s['positivos_validos']}/{s['positivos']} |",
        f"| Detección entre los verificados | {s['detectados_validos']}/{s['positivos_validos']} "
        f"({pct(s['detectados_validos'], s['positivos_validos'])}) |",
        f"| Falsos positivos (controles marcados sin la patología) | {s['falsos_positivos']}/{s['controles']} "
        f"({pct(s['falsos_positivos'], s['controles'])}) |",
        f"| Controles marcados en total | {s['controles_marcados']}/{s['controles']} |",
    ]
    if s["extra"]:
        lines.append(f"| Casos fuera de la regla detectados | {s['extra_detectados']}/{s['extra']} |")
    if med == "ap07":
        lines += [f"| Detección por ventana con spill | {s['ventanas_detectadas']}/{s['ventanas_con_spill']} "
                  f"({pct(s['ventanas_detectadas'], s['ventanas_con_spill'])}) |",
                  f"| Controles que sí derramaron (no son FP) | {s['controles_que_derramaron']} |"]
    if med == "ap01":
        lines += [f"| Marcados antes de degradar | {s['fp_antes_de_degradar']} |",
                  f"| Detectados después de {AP01_MAX_WINDOWS} ventanas | {s['detectados_tarde']} |",
                  f"| Controles marcados que sí se degradaron (contención de la inyección) | "
                  f"{s['controles_degradados_de_verdad']} |",
                  f"| Ventana de detección (1 = la primera tras degradar) | "
                  f"{', '.join(f'{k}: {v}' for k, v in s['latencia_ventanas'].items()) or '—'} |"]
    if med == "ap06":
        lines += [f"| Detección por ventana válida | {s['ventanas_detectadas']}/{s['ventanas_validas']} "
                  f"({pct(s['ventanas_detectadas'], s['ventanas_validas'])}) |",
                  f"| Padre identificado correctamente | {s['padre_correcto']} |"]
    lines += ["", "## Por categoría", "", "| Categoría | Casos | Patología verificada | Detectados |", "|---|---|---|---|"]
    for cat, e in sorted(s["por_categoria"].items()):
        lines.append(f"| {cat} | {e['casos']} | {e['validos']} | {e['detectados']} ({pct(e['detectados'], e['casos'])}) |")
    lines += ["", "## Controles", "", "| Categoría | Casos | Marcados | Falsos positivos |", "|---|---|---|---|"]
    for cat, e in sorted(s["controles_por_categoria"].items()):
        lines.append(f"| {cat} | {e['casos']} | {e['marcados']} | {e['falsos_positivos']} |")
    missed = [r for r in results if r["role"] == "positivo" and not r["detected"]]
    if missed:
        lines += ["", "## Positivos no detectados", ""]
        for r in missed:
            lines.append(f"- `{r['cid']}` ({r['category']}, patología {'verificada' if r['valid'] else 'NO verificada'}): "
                         f"`{r['query'][:110]}` — {miss_reason(med, r)}")
    fps = [r for r in results if r["role"] == "control" and r["detected"]]
    if fps:
        lines += ["", "## Controles marcados", ""]
        for r in fps:
            note = "falso positivo" if r["fp"] else "la patología ocurrió (ver contadores)"
            if med == "ap01":
                note += "; " + "; ".join(f"v{c}: {d['mean_ms']} ms vs {d['median_ms']} ms (x{d['ratio']})"
                                         for c, d in r["after"].items() if c in r["detected_windows"])
            lines.append(f"- `{r['cid']}` ({r['category']}, {note}): `{r['query'][:110]}`")
    if med == "ap06" and s.get("hallazgos_fuera_de_casos"):
        lines += ["", "## Hallazgos sobre consultas que no son hijas de un caso", ""]
        for o in s["hallazgos_fuera_de_casos"][:40]:
            lines.append(f"- `{o['caso']}`: `{(o['consulta'] or '')[:110]}`")
    return "\n".join(lines) + "\n"


def miss_reason(med, r):
    if med == "ap07":
        return (f"ventanas ejecutada {r['executed_windows']}, con spill {r['spilled_windows']}, "
                f"en top_impact {r['in_top_impact']}")
    if med == "ap01":
        after = "; ".join(f"v{c}: {d['mean_ms']} ms vs {d['median_ms']} ms (x{d['ratio']}, {d['calls']} ejec.)"
                          for c, d in r["after"].items())
        return f"línea base {r['baseline_windows']} ventanas válidas; después: {after or 'sin ventanas medibles'}"
    windows_txt = "; ".join(
        f"v{c}: hija {d['child_calls']}" + (f", filas padre {d['parent_rows']} (desvío {d['desvio']})" if 'parent_rows' in d else "")
        for c, d in r["windows"].items())
    return windows_txt or "sin ventanas medibles"


# ------------------------------------------------------------------ una medicion

def measure(med, engine, args):
    warmup, n_cycles, inject_after = PLAN[med]
    interval = args.interval
    # --temptable-max-ram-mib: RAM de TempTable solo durante la corrida (MySQL); se
    # restaura el valor que tenia el servidor al terminar, aunque la corrida falle
    max_ram_mib = args.temptable_max_ram_mib if engine == "mysql" else None
    run_id = (f"{med}-{engine}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
              f"{f'-ram{max_ram_mib}mib' if max_ram_mib else ''}{'-smoke' if args.smoke else ''}")
    db_id = f"medicion-{run_id}"
    out = RESULTS_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    log(f"===== {NOMBRE[med]} | {engine} | {run_id} =====")

    if not wait_queue_empty(180):
        raise RuntimeError("la cola analyze_job no se vacia: esta corriendo querylens_detection_engine?")
    run(["docker", "rm", "-f", DRIVER], check=False)
    run(["docker", "cp", str(MED_DIR / "pipeline_snapshot.py"), f"{PIPELINE_CONTAINER}:/tmp/pipeline_snapshot.py"])

    cycles = []
    started = querylens_sql("SELECT now();")
    original_max_ram = None
    try:
        if max_ram_mib:
            original_max_ram = int(mysql_root("SELECT @@temptable_max_ram;").strip())
            mysql_root(f"SET GLOBAL temptable_max_ram = {max_ram_mib * 1048576};")
            log(f"servidor: temptable_max_ram {original_max_ram} -> {max_ram_mib * 1048576} (solo durante la corrida)")
        if (med, engine) in SERVER_SETUP:
            log(f"servidor: {SERVER_SETUP[(med, engine)]}")
            mysql_root(SERVER_SETUP[(med, engine)])
        log("armando tablas...")
        driver(["setup", med, engine], run_id)
        log("query_id de cada caso" + (" y calibracion (una ejecucion de cada candidato)" if med == "ap07" else "") + "...")
        prepare_args = ["prepare", med, engine]
        if args.smoke:
            prepare_args += ["--limit", "8"]
        driver(prepare_args, run_id)
        cases = json.loads((out / "cases.json").read_text(encoding="utf-8"))["cases"]

        rate = 3.0 if (med == "ap01" and interval < 60) else 1.0
        total = warmup + n_cycles * interval + 180
        driver(["run", med, engine, str(total), "--rate", str(rate)], run_id, detach=True)
        log(f"carga iniciada: {len(cases)} casos; calentamiento {warmup}s")
        time.sleep(warmup)

        t0 = time.monotonic()
        for cycle in range(1, n_cycles + 1):
            target = t0 + (cycle - 1) * interval
            time.sleep(max(0.0, target - time.monotonic()))
            start_at = querylens_sql("SELECT now();")
            msg_id = take_snapshot(engine, db_id)
            if not wait_queue_empty(240):
                log("aviso: la cola no se vacio en 240 s")
            end_at = querylens_sql("SELECT now();")
            cycles.append({"cycle": cycle, "msg_id": msg_id, "start": start_at, "end": end_at})
            n_found = querylens_sql(
                f"SELECT count(*) FROM public.hallazgos WHERE db_id = '{db_id}' AND antipatron = '{ANTIPATRON[med]}'")
            log(f"ciclo {cycle}/{n_cycles} | msg_id {msg_id} | hallazgos {ANTIPATRON[med]} acumulados: {n_found}")
            if inject_after == cycle:
                log("degradando los positivos...")
                driver(["inject", med, engine], run_id)
    finally:
        log("deteniendo la carga...")
        run(["docker", "stop", "-t", "40", DRIVER], check=False)
        if (med, engine) in SERVER_TEARDOWN:
            mysql_root(SERVER_TEARDOWN[(med, engine)])
            log(f"servidor restaurado: {SERVER_TEARDOWN[(med, engine)]}")
        if original_max_ram is not None:
            mysql_root(f"SET GLOBAL temptable_max_ram = {original_max_ram};")
            now_value = mysql_root("SELECT @@temptable_max_ram;").strip()
            log(f"servidor restaurado: temptable_max_ram = {now_value}")
        if not args.keep:
            driver(["teardown", med, engine], run_id)

    meta = {"run_id": run_id, "db_id": db_id, "engine": engine, "measurement": med, "cycles": len(cycles),
            "interval": interval, "started": started, "cycle_times": cycles,
            "temptable_max_ram": max_ram_mib * 1048576 if max_ram_mib else None}
    return evaluate(med, engine, run_id, out, cases, meta)


def evaluate(med, engine, run_id, out, cases, meta):
    cycles, db_id = meta["cycle_times"], meta["db_id"]
    inject_after = PLAN[med][2]
    qids = sorted({qid.lower() for case in cases for qid in case["query_ids"].values()})
    snapshots = [fetch_snapshot_rows(c["msg_id"], qids) for c in cycles]
    findings = fetch_findings(db_id, ANTIPATRON[med])
    by_qid = finding_cycles(findings, cycles)
    extra = None
    if med == "ap07":
        results = evaluate_ap07(cases, snapshots, by_qid, len(cycles), engine)
    elif med == "ap01":
        results = evaluate_ap01(cases, snapshots, by_qid, len(cycles), inject_after, engine)
    else:
        results, extra = evaluate_ap06(cases, snapshots, by_qid, len(cycles), findings, engine)
    summary = summarize(med, engine, results, extra)
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "results": results},
                                                 indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "reporte.md").write_text(markdown(med, engine, run_id, summary, results, meta), encoding="utf-8")
    log(f"RESULTADO {NOMBRE[med]} {engine}: {summary['detectados']}/{summary['positivos']} detectados "
        f"({pct(summary['detectados'], summary['positivos'])}), FP {summary['falsos_positivos']}/{summary['controles']}"
        f" -> {out / 'reporte.md'}")
    return run_id, summary


def main():
    parser = argparse.ArgumentParser(description="Medicion de deteccion de AP-07, AP-01 y AP-06.")
    parser.add_argument("measurement", nargs="?", choices=["ap07", "ap01", "ap06", "all"])
    parser.add_argument("--reevaluar", metavar="CORRIDA",
                        help="recalcular el reporte de una corrida ya hecha (carpeta en resultados/)")
    parser.add_argument("--engine", choices=["postgres", "mysql", "both"], default="postgres")
    parser.add_argument("--interval", type=int, default=60, help="segundos entre snapshots (60 = ventana del informe)")
    parser.add_argument("--smoke", action="store_true", help="prueba corta: pocos casos")
    parser.add_argument("--keep", action="store_true", help="no borrar las tablas al terminar")
    parser.add_argument("--temptable-max-ram-mib", type=int, default=None,
                        help="MySQL: temptable_max_ram (MiB) solo durante la corrida; despues se restaura")
    args = parser.parse_args()
    if args.reevaluar:
        out = RESULTS_DIR / args.reevaluar
        meta = json.loads((out / "results.json").read_text(encoding="utf-8"))["meta"]
        cases = json.loads((out / "cases.json").read_text(encoding="utf-8"))["cases"]
        evaluate(meta["measurement"], meta["engine"], meta["run_id"], out, cases, meta)
        return 0
    if not args.measurement:
        parser.error("falta la medicion (ap07, ap01, ap06 o all)")
    meds = ["ap07", "ap06", "ap01"] if args.measurement == "all" else [args.measurement]
    engines = ["postgres", "mysql"] if args.engine == "both" else [args.engine]
    RESULTS_DIR.mkdir(exist_ok=True)
    done = []
    for engine in engines:
        for med in meds:
            done.append((med, engine, *measure(med, engine, args)))
    print("\n================ RESUMEN ================")
    for med, engine, run_id, s in done:
        print(f"{NOMBRE[med]:<46} {engine:<8} {s['detectados']:>3}/{s['positivos']:<3} "
              f"({pct(s['detectados'], s['positivos'])})  FP {s['falsos_positivos']}/{s['controles']}  "
              f"{'OK' if s['cumple_90'] else 'NO CUMPLE'}  {run_id}")


if __name__ == "__main__":
    sys.exit(main())
