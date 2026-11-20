"""
QUERYLENS — calculo automatico de las metricas M1 a M4 (SegundoInforme, Tabla 7).

Compara los hallazgos de cada corrida del banco (banco.py) con el manifiesto
de su escenario (manifiestos.py) y calcula, por combinacion patologia-motor:

  M1 exhaustividad  VP / (VP + FN). Cada repeticion de un escenario es una
                    inyeccion: VP si aparecieron todos los hallazgos esperados
                    de la patologia, FN si falto alguno.
  M2 precision      VP / (VP + FP). FP: hallazgos de esa patologia que ningun
                    manifiesto declara, en las corridas con inyeccion (de
                    cualquier escenario del motor) y en las de control. Un
                    problema que se detecta en varias ventanas seguidas de una
                    corrida cuenta una sola vez, igual que un VP cuenta una
                    vez por inyeccion.
  M3 contencion     de los ciclos o cadenas detectados, proporcion con las
                    transacciones y recursos que declaro el script de
                    inyeccion, y proporcion con el patron correcto.
  M4 latencia       desde el inicio de la inyeccion (o el abrazo; en CT-05,
                    desde que la raiz cumple 30 s) hasta que el hallazgo queda
                    guardado. En una patologia con varios esperados, hasta que
                    aparece el ultimo.
  M7 completitud    lista de chequeo sobre todos los problemas de la corrida:
                    causa (explicacion), evidencia, recomendacion, riesgo y
                    enlace con la instantanea. La evidencia y el enlace se
                    revisan en cada deteccion: todas tienen que guardar su
                    evidencia y su snapshot_msg_id, y esa instantanea tiene que
                    estar archivada en pgmq.a_analyze_job con el mismo db_id.
                    Una corrida anterior a esas columnas no trae el dato: M7
                    queda como no medible.

No necesita Docker: lee lo que dejo el banco en la carpeta de la corrida.

USO (desde la raiz del repo)
  python ql_sandbox/banco/metricas.py <carpeta de la corrida>
      vuelve a calcular metricas.json y reporte.md (p. ej. tras declarar un
      colateral en el manifiesto), sin volver a correr nada
"""

import argparse
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifiestos as mf  # noqa: E402

CASCADE = mf.ANTIPATRON["CT-05"]


def parse_ts(value) -> datetime:
    # psql: "2026-10-12 23:44:07.12+00"; json de Postgres: "2026-10-12T23:44:07.12+00:00"
    text = str(value).replace("T", " ")
    if len(text) >= 3 and text[-3] in "+-" and text[-2:].isdigit():
        text += ":00"
    return datetime.fromisoformat(text)


def wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    """Intervalo de confianza del 95 % de una proporcion (con 10 inyecciones
    el intervalo es ancho: se informa junto a la tasa)."""
    if total == 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def describe(finding: dict) -> str:
    """Una linea para reconocer el hallazgo en el reporte."""
    evidence = finding.get("evidencia") or {}
    parts = [str(finding.get("table_name"))] if finding.get("table_name") else []
    if evidence.get("index_name") and finding.get("antipatron") == "unused_index":
        parts.append(f"indice {evidence['index_name']} ({evidence.get('subtipo')})")
    text = evidence.get("canonic_query") or evidence.get("query_text")
    if text:
        parts.append(" ".join(str(text).split())[:110])
    return " | ".join(parts) or "(sin detalle)"


class _Combo:
    def __init__(self, codigo: str, engine: str):
        self.codigo, self.engine = codigo, engine
        self.vp = self.fn = self.fp = self.failed = 0
        self.latencies: list[float] = []
        self.detected = self.members_ok = self.class_ok = 0


def _m4_limit(codigo: str) -> int:
    if codigo == "CT-05":
        return mf.M4_CASCADE_S
    if codigo.startswith("CT"):
        return mf.M4_DEADLOCK_S
    return mf.M4_AP05_S if codigo == "AP-05" else mf.M4_AP_S


def compute(units: list[dict], reps: int | None = None) -> dict:
    """units: una por corrida del banco (inyeccion, control o bloque de
    contencion), como las escribe banco.py en corridas.jsonl. reps: las
    repeticiones pedidas, para marcar las combinaciones incompletas."""
    combos = {key: _Combo(*key) for key in mf.combinations()}
    false_negatives, false_positives, uncatalogued = [], [], []
    declared: dict[tuple, int] = {}
    controls = {engine: {"corridas": 0, "minutos": 0.0} for engine in mf.ENGINES}

    def add_false_positive(unit: dict, finding: dict) -> None:
        row = {
            "corrida": unit["id"], "tipo": unit["tipo"], "engine": unit["engine"],
            "codigo": mf.CODIGO.get(finding.get("antipatron")), "antipatron": finding.get("antipatron"),
            "severidad": finding.get("severidad"), "detecciones": finding.get("detecciones", 1),
            "detalle": describe(finding),
        }
        combo = combos.get((row["codigo"], unit["engine"]))
        if combo is None:
            # p. ej. un abrazo sin clasificar: no es de ninguna combinacion
            uncatalogued.append(row)
            return
        combo.fp += 1
        false_positives.append(row)

    def classify_plain(unit: dict, findings: list[dict], manifest) -> dict:
        """Clasifica los hallazgos de la corrida; devuelve, por indice del
        esperado, la primera vez que aparecio."""
        first_seen: dict[int, datetime] = {}
        injected = unit["tipo"] != "control"
        for finding in findings:
            kind, item = mf.classify(finding, unit["engine"], manifest, injected)
            if kind == "esperado":
                index = manifest.expected.index(item)
                seen = parse_ts(finding["primera_deteccion"])
                if index not in first_seen or seen < first_seen[index]:
                    first_seen[index] = seen
            elif kind == "no_declarado":
                add_false_positive(unit, finding)
            else:
                key = (kind, unit.get("scenario") or unit["tipo"], unit["engine"], item.descripcion)
                declared[key] = declared.get(key, 0) + 1
        return first_seen

    for unit in units:
        engine = unit["engine"]
        if unit["tipo"] == "inyeccion":
            manifest = mf.AP_MANIFESTS[unit["scenario"]]
            start = parse_ts(unit["inicio"])
            first_seen = classify_plain(unit, unit["hallazgos"], manifest)
            for codigo in manifest.codes():
                wanted = [i for i, e in enumerate(manifest.expected) if e.codigo == codigo and engine in e.engines]
                if not wanted:
                    continue
                combo = combos[(codigo, engine)]
                missing = [manifest.expected[i].descripcion for i in wanted if i not in first_seen]
                if missing:
                    combo.fn += 1
                    false_negatives.append({"corrida": unit["id"], "engine": engine, "codigo": codigo,
                                            "faltantes": missing})
                else:
                    combo.vp += 1
                    combo.latencies.append(round(max((first_seen[i] - start).total_seconds() for i in wanted), 1))
        elif unit["tipo"] == "control":
            classify_plain(unit, unit["hallazgos"], None)
            controls[engine]["corridas"] += 1
            controls[engine]["minutos"] += (parse_ts(unit["fin"]) - parse_ts(unit["inicio"])).total_seconds() / 60
        elif unit["tipo"] == "ct":
            _contention_block(unit, combos, false_negatives, add_false_positive, classify_plain)
        else:
            raise ValueError(f"tipo de corrida desconocido: {unit['tipo']}")

    rows = [_combo_row(combo, reps) for combo in combos.values()]
    evaluated = [row for row in rows if row["inyecciones"]]

    def verdict(key: str, subset=None) -> dict:
        subset = rows if subset is None else subset
        ok = [row for row in subset if row[key] is True]
        return {"cumplen": len(ok), "de": len(subset),
                "veredicto": "CUMPLE" if len(ok) == len(subset) else "NO CUMPLE"}

    contention = [row for row in rows if row["codigo"].startswith("CT")]
    completeness = _completeness(units)
    return {
        "combinaciones": rows,
        "resumen": {
            "combinaciones": len(rows),
            "evaluadas": len(evaluated),
            "completas": sum(1 for row in rows if row["completa"]),
            "M1": verdict("m1_ok"),
            "M2": verdict("m2_ok"),
            "M3": verdict("m3_ok", contention),
            "M4": verdict("m4_ok"),
            "M7": {"cumplen": completeness["completos"], "de": completeness["problemas"],
                   "veredicto": "NO MEDIBLE" if completeness["sin_dato_de_enlace"] else (
                       "CUMPLE" if completeness["completos"] == completeness["problemas"] else "NO CUMPLE")},
        },
        "completitud": completeness,
        "controles": {engine: {"corridas": c["corridas"], "minutos": round(c["minutos"], 1)}
                      for engine, c in controls.items()},
        "falsos_negativos": false_negatives,
        "falsos_positivos": false_positives,
        "sin_patologia_del_catalogo": uncatalogued,
        "declarados": [
            {"tipo": kind, "escenario": scenario, "engine": engine, "descripcion": text, "veces": count}
            for (kind, scenario, engine, text), count in sorted(declared.items())
        ],
    }


# Lo que M7 revisa en cada problema
M7_CHECKS = ("con_explicacion", "con_evidencia", "con_recomendacion", "con_riesgo", "con_enlace_a_instantanea")


def _completeness(units: list[dict]) -> dict:
    """M7: una vez por problema de cada corrida (no por deteccion)."""
    missing = {check: 0 for check in M7_CHECKS}
    incomplete, total, without_data = [], 0, 0
    for unit in units:
        for finding in unit["hallazgos"]:
            total += 1
            checks = {check: bool(finding.get(check)) for check in M7_CHECKS}
            # evidencia y enlace, en cada deteccion del problema dentro de la corrida
            checks["con_evidencia"] = bool(finding.get("evidencia")) and not finding.get("detecciones_sin_evidencia")
            if "detecciones_sin_enlace" in finding:
                checks["con_enlace_a_instantanea"] = finding["detecciones_sin_enlace"] == 0
            else:
                # corrida anterior al enlace: no hay dato que revisar
                del checks["con_enlace_a_instantanea"]
                without_data += 1
            failed = [check for check, ok in checks.items() if not ok]
            for check in failed:
                missing[check] += 1
            if failed:
                incomplete.append({"corrida": unit["id"], "antipatron": finding.get("antipatron"),
                                   "faltan": failed, "detalle": describe(finding)})
    return {"problemas": total, "completos": total - len(incomplete), "faltantes": missing,
            "incompletos": incomplete[:50], "sin_dato_de_enlace": without_data}


def _contention_block(unit, combos, false_negatives, add_false_positive, classify_plain) -> None:
    """Bloque de contencion de un motor: measure_ct.py ya emparejo cada
    abrazo o cascada inyectada con su evento y su hallazgo."""
    engine = unit["engine"]
    code_of_case = {case: codigo for codigo, case in mf.CT_CASES[engine].items()}
    contention = set(mf.ANTIPATRON[c] for c in mf.ANTIPATRON if c.startswith("CT")) | {mf.UNCLASSIFIED}

    # los hallazgos de antipatrones durante el bloque son de la carga base o FP
    classify_plain(unit, [f for f in unit["hallazgos"] if f.get("antipatron") not in contention], None)

    for row in unit["ct"]["results"]:
        codigo = code_of_case.get(row["case"]) if row.get("phase") == "positivo" else None
        if codigo is None:
            continue
        combo = combos[(codigo, engine)]
        expected = mf.ANTIPATRON[codigo]
        got = row.get("finding")
        if row["kind"] == "ct05":
            detected = got is not None
            members_ok = row.get("evidence_ok") is True
        else:
            if not row.get("deadlock"):
                # el script no llego a provocar el abrazo: no hubo inyeccion
                combo.failed += 1
                continue
            detected = bool(row.get("captured")) or got is not None
            # el hallazgo se empareja por el conjunto de pid del ciclo; con el
            # patron correcto, evidence_ok revisa ademas los recursos
            members_ok = got is not None and row.get("evidence_ok") is not False
        if got == expected:
            combo.vp += 1
            if row.get("saved_delay_s") is not None:
                combo.latencies.append(row["saved_delay_s"])
        else:
            combo.fn += 1
            false_negatives.append({
                "corrida": f"{unit['id']}|{row['case']}|{row['rep']}", "engine": engine, "codigo": codigo,
                "faltantes": [f"hallazgo {expected} (salio: {got or 'ninguno'})"],
            })
            other = combos.get((mf.CODIGO.get(got), engine))
            if other is not None:
                # clasificado con otro patron del catalogo: FP de ese patron
                add_false_positive(unit, {"antipatron": got, "severidad": row.get("severity"),
                                          "evidencia": {"query_text": f"caso {row['case']} #{row['rep']}"}})
        if detected:
            combo.detected += 1
            combo.members_ok += members_ok
            combo.class_ok += got == expected

    # hallazgos de contencion que no son de ninguna inyeccion
    by_detection = {}
    for finding in unit["hallazgos"]:
        for detection_id in finding.get("deteccion_ids") or []:
            by_detection[detection_id] = finding
    seen_problems = set()
    for stray in (unit["ct"].get("summary") or {}).get("hallazgos_sin_corrida") or []:
        finding = by_detection.get(stray["id"]) or {"antipatron": stray["antipatron"], "evidencia": {}}
        problem = finding.get("problema_id", ("sin_id", stray["id"]))
        if problem in seen_problems:
            continue
        seen_problems.add(problem)
        classify_plain(unit, [finding], None)


def _rate(ok: int, total: int) -> float | None:
    return round(ok / total, 4) if total else None


def _combo_row(combo: _Combo, reps: int | None) -> dict:
    injections = combo.vp + combo.fn
    m1 = _rate(combo.vp, injections)
    m2 = _rate(combo.vp, combo.vp + combo.fp)
    limit = _m4_limit(combo.codigo)
    within = sum(1 for value in combo.latencies if value <= limit)
    row = {
        "codigo": combo.codigo, "nombre": mf.NOMBRE[combo.codigo], "engine": combo.engine,
        "inyecciones": injections, "inyecciones_fallidas": combo.failed,
        "completa": bool(injections) and (reps is None or injections >= reps),
        "vp": combo.vp, "fn": combo.fn, "fp": combo.fp,
        "m1": m1, "m1_ic95": wilson(combo.vp, injections),
        "m1_ok": None if m1 is None else m1 >= mf.M1_MIN,
        "m2": m2, "m2_ok": None if m2 is None else m2 >= mf.M2_MIN,
        "m4": None, "m4_ok": None,
        "m3": None, "m3_ok": None,
    }
    if combo.latencies:
        median, worst = statistics.median(combo.latencies), max(combo.latencies)
        row["m4"] = {
            "n": len(combo.latencies), "limite_s": limit, "dentro": within,
            "mediana_s": round(median, 1), "max_s": round(worst, 1),
            "mediana_ventanas": round(median / mf.WINDOW_S, 1), "max_ventanas": round(worst / mf.WINDOW_S, 1),
        }
        row["m4_ok"] = within == len(combo.latencies)
    if combo.codigo.startswith("CT") and combo.detected:
        members = _rate(combo.members_ok, combo.detected)
        classified = _rate(combo.class_ok, combo.detected)
        row["m3"] = {"detectados": combo.detected, "miembros_ok": combo.members_ok, "miembros": members,
                     "clasificacion_ok": combo.class_ok, "clasificacion": classified}
        row["m3_ok"] = members >= mf.M3_MEMBERS_MIN and classified >= mf.M3_CLASS_MIN
    return row


# ------------------------------------------------------------------
# Reporte
# ------------------------------------------------------------------

def _mark(ok) -> str:
    return "—" if ok is None else ("cumple" if ok else "**NO**")


def _fraction(value, ok, total) -> str:
    return "—" if value is None else f"{value:.2f} ({ok}/{total})"


def markdown(metrics: dict, meta: dict) -> str:
    summary = metrics["resumen"]
    lines = [
        f"# Banco de pruebas — corrida `{meta.get('run_id', '')}`", "",
        f"Inicio {meta.get('inicio')} · fin {meta.get('fin') or 'sin terminar'} · "
        f"{meta.get('reps')} repeticiones por escenario · {meta.get('controles')} corridas de control de "
        f"{meta.get('minutos_control')} min por motor · carga base: sysbench oltp_read_write, 4 hilos, "
        f"{meta.get('tasa_carga_base')} tx/s · commit `{meta.get('commit', '?')}`"
        + (" · estadisticas de sentencias en cero al empezar cada motor" if meta.get("reiniciar_sentencias") else ""), "",
        "## Veredicto (Tabla 7)", "",
        "| Metrica | Umbral | Combinaciones que cumplen | Veredicto |", "|---|---|---|---|",
        f"| M1 exhaustividad | >= {mf.M1_MIN:.2f} en cada combinacion | {summary['M1']['cumplen']}/{summary['M1']['de']} | {summary['M1']['veredicto']} |",
        f"| M2 precision | >= {mf.M2_MIN:.2f} en cada combinacion | {summary['M2']['cumplen']}/{summary['M2']['de']} | {summary['M2']['veredicto']} |",
        f"| M3 contencion | >= {mf.M3_MEMBERS_MIN:.2f} miembros, >= {mf.M3_CLASS_MIN:.2f} clasificacion | {summary['M3']['cumplen']}/{summary['M3']['de']} | {summary['M3']['veredicto']} |",
        f"| M4 latencia | AP <= {mf.M4_AP_S} s; CT-01 a CT-04 <= {mf.M4_DEADLOCK_S} s; CT-05 <= {mf.M4_CASCADE_S} s | {summary['M4']['cumplen']}/{summary['M4']['de']} | {summary['M4']['veredicto']} |",
        f"| M7 completitud | 100 % de los problemas con causa, evidencia enlazada a su instantanea, recomendacion y riesgo | {summary['M7']['cumplen']}/{summary['M7']['de']} problemas | {summary['M7']['veredicto']} |",
        "",
        f"Combinaciones con inyecciones: {summary['evaluadas']}/{summary['combinaciones']}; con las "
        f"{meta.get('reps')} repeticiones completas: {summary['completas']}/{summary['combinaciones']}. "
        "Una combinacion sin inyecciones o sin hallazgos que medir no cumple.", "",
    ]
    for engine in mf.ENGINES:
        rows = [row for row in metrics["combinaciones"] if row["engine"] == engine]
        control = metrics["controles"][engine]
        lines += [
            f"## {engine}", "",
            f"Corridas de control: {control['corridas']} ({control['minutos']} min en total).", "",
            "| Patologia | Inyecciones | VP | FN | FP | M1 (IC 95 %) | M2 | M4 mediana / max (s) | M4 en ventanas | M4 dentro del limite |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for row in rows:
            interval = row["m1_ic95"]
            m1 = "—" if row["m1"] is None else (
                f"{row['m1']:.2f} ({interval[0]:.2f}–{interval[1]:.2f}) {_mark(row['m1_ok'])}")
            m2 = "—" if row["m2"] is None else f"{row['m2']:.2f} {_mark(row['m2_ok'])}"
            m4 = row["m4"]
            failed = f" (+{row['inyecciones_fallidas']} sin abrazo)" if row["inyecciones_fallidas"] else ""
            lines.append(
                f"| {row['codigo']} {row['nombre']} | {row['inyecciones']}{failed} | {row['vp']} | {row['fn']} | "
                f"{row['fp']} | {m1} | {m2} | "
                + (f"{m4['mediana_s']} / {m4['max_s']} | {m4['mediana_ventanas']} / {m4['max_ventanas']} | "
                   f"{m4['dentro']}/{m4['n']} de <= {m4['limite_s']} s {_mark(row['m4_ok'])} |" if m4 else "— | — | — |")
            )
        lines.append("")
        contention = [row for row in rows if row["m3"]]
        if contention:
            lines += ["M3, exactitud del analisis de contencion:", "",
                      "| Patologia | Detectados | Miembros y recursos correctos | Patron correcto | |",
                      "|---|---|---|---|---|"]
            for row in contention:
                m3 = row["m3"]
                lines.append(
                    f"| {row['codigo']} {row['nombre']} | {m3['detectados']} | "
                    f"{_fraction(m3['miembros'], m3['miembros_ok'], m3['detectados'])} | "
                    f"{_fraction(m3['clasificacion'], m3['clasificacion_ok'], m3['detectados'])} | {_mark(row['m3_ok'])} |")
            lines.append("")

    completeness = metrics["completitud"]
    lines += ["## M7, lista de chequeo", "",
              f"{completeness['problemas']} problemas en todas las corridas; faltantes por punto: "
              + ", ".join(f"{check.removeprefix('con_')} {count}" for check, count in completeness["faltantes"].items())
              + ".", ""]
    if completeness["sin_dato_de_enlace"]:
        lines += [f"Enlace con la instantanea: no medible ({completeness['sin_dato_de_enlace']} problemas sin "
                  "el dato). La corrida es anterior a public.detecciones.snapshot_msg_id (RNF-06).", ""]
    for item in completeness["incompletos"]:
        lines.append(f"- `{item['corrida']}` `{item['antipatron']}`: falta {', '.join(item['faltan'])} — {item['detalle']}")
    if completeness["incompletos"]:
        lines.append("")
    lines += ["## Falsos negativos", ""]
    if metrics["falsos_negativos"]:
        for item in metrics["falsos_negativos"]:
            lines.append(f"- `{item['corrida']}` {item['codigo']}: falto {'; '.join(item['faltantes'])}")
    else:
        lines.append("Ninguno.")
    lines += ["", "## Falsos positivos (hallazgos que ningun manifiesto declara)", ""]
    if metrics["falsos_positivos"]:
        lines += ["| Corrida | Patologia | Severidad | Detecciones | Hallazgo |", "|---|---|---|---|---|"]
        for item in metrics["falsos_positivos"]:
            lines.append(f"| `{item['corrida']}` | {item['codigo']} {mf.NOMBRE[item['codigo']]} | {item['severidad']} | "
                         f"{item['detecciones']} | {item['detalle']} |")
    else:
        lines.append("Ninguno.")
    if metrics["sin_patologia_del_catalogo"]:
        lines += ["", "Hallazgos no declarados que no son de ninguna combinacion (no entran en M2):", ""]
        for item in metrics["sin_patologia_del_catalogo"]:
            lines.append(f"- `{item['corrida']}`: `{item['antipatron']}` — {item['detalle']}")
    lines += ["", "## Hallazgos declarados en los manifiestos (no cuentan como VP ni como FP)", ""]
    if metrics["declarados"]:
        lines += ["| Tipo | Escenario | Motor | Declaracion | Corridas en las que salio |", "|---|---|---|---|---|"]
        for item in metrics["declarados"]:
            lines.append(f"| {item['tipo']} | {item['escenario']} | {item['engine']} | {item['descripcion']} | "
                         f"{item['veces']} |")
    else:
        lines.append("Ninguno.")
    return "\n".join(lines) + "\n"


def load_units(run_dir: Path) -> list[dict]:
    path = run_dir / "corridas.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_report(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    metrics = compute(load_units(run_dir), meta.get("reps"))
    (run_dir / "metricas.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False), encoding="utf-8")
    (run_dir / "reporte.md").write_text(markdown(metrics, meta), encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Metricas M1 a M4 de una corrida del banco de pruebas.")
    parser.add_argument("corrida", type=Path, help="carpeta de la corrida (ql_sandbox/banco/resultados/<corrida>)")
    args = parser.parse_args()
    metrics = write_report(args.corrida)
    for name in ("M1", "M2", "M3", "M4"):
        item = metrics["resumen"][name]
        print(f"{name}: {item['cumplen']}/{item['de']} combinaciones | {item['veredicto']}")
    print(f"-> {args.corrida / 'reporte.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
