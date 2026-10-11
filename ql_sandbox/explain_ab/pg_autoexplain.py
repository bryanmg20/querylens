"""
PROTOTIPO: planes de auto_explain (version 4, feature/pg-auto-explain) en lugar
del EXPLAIN del snapshot.

La v4 solo prepara el servidor (auto_explain con log_analyze=off, log JSON y las
funciones querylens.log_files() / querylens.read_log() para el rol de monitoreo);
el pipeline todavia no lee el log. Este script hace esa lectura por fuera, sobre
los snapshots de una corrida de run_ab.py, para ver que aportarian esos planes.

Por cada caso:
  1. lee el log JSON con el rol de monitoreo (la misma via que usaria el pipeline)
  2. toma las entradas de auto_explain con el query_id del caso y dentro de la
     ventana del caso (started_at / ended_at del resultado)
  3. agrupa los planes registrados por forma (metodo de acceso y filas estimadas
     de cada scan) y corre la regla del patron con cada forma distinta
  4. el resultado del caso usa el ULTIMO plan registrado (lo mas parecido a "el
     plan con el que se esta ejecutando ahora"); el desglose por forma queda en
     el resultado para ver cuanto depende de esa eleccion

Cada entrada es una ejecucion real de 50 ms o mas, con su valor real y filas
ESTIMADAS (log_analyze=off). Las consultas mas rapidas no dejan plan.

USO (desde la raiz, con el python del venv de telemetry_pipeline/; el sandbox
tiene que estar levantado con el docker-compose.yml de la v4)
  telemetry_pipeline/venv/Scripts/python.exe ql_sandbox/explain_ab/pg_autoexplain.py \\
      ap02 --from-version v4 --version v4_autoexplain --pipeline-dir ../querylens-v4/telemetry_pipeline
"""

import argparse
import copy
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS_DIR = HERE / "results"
READ_CHUNK = 16 * 1024 * 1024  # maximo que acepta querylens.read_log


def find_pattern(name: str) -> Path:
    matches = [p for p in (HERE / "patterns").iterdir()
               if p.is_dir() and (p.name == name or p.name.startswith(name + "_"))]
    if len(matches) != 1:
        raise SystemExit(f"Patron '{name}' no encontrado o ambiguo")
    return matches[0]


def read_log_entries(conn) -> tuple[list[dict], int]:
    """Entradas de auto_explain de todos los .json del log: (query_id, instante,
    duracion, plan). Devuelve tambien los bytes leidos."""
    from sqlalchemy import text
    files = conn.execute(text("SELECT name, size FROM querylens.log_files()")).all()
    entries, total = [], 0
    for name, size in files:
        raw = b""
        offset = 0
        while offset < size:
            chunk = conn.execute(
                text("SELECT querylens.read_log(:f, :o, :m)"), {"f": name, "o": offset, "m": READ_CHUNK}
            ).scalar() or b""
            if not chunk:
                break
            raw += bytes(chunk)
            offset += len(chunk)
        total += len(raw)
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if '"duration:' not in line or "plan:" not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # linea cortada al final del archivo
            message = event.get("message") or ""
            head, _, body = message.partition("plan:")
            try:
                plan = json.loads(body)
            except json.JSONDecodeError:
                continue
            entries.append({
                "query_id": str(event.get("query_id")),
                "at": datetime.strptime(event["timestamp"], "%Y-%m-%d %H:%M:%S.%f UTC").replace(tzinfo=timezone.utc),
                "duration_ms": float(head.replace("duration:", "").replace("ms", "").strip() or 0),
                "plan": plan,
            })
    return entries, total


def scans_of(canonical_plan: dict) -> list[dict]:
    return [
        {key: op.get(key) for key in ("relation", "access_method", "index_name", "estimated_rows", "predicate")}
        for op in canonical_plan.get("physical_operations", [])
        if op.get("type") == "scan"
    ]


def shape_key(canonical_plan: dict) -> str:
    return json.dumps([
        (s["relation"], s["access_method"], s["estimated_rows"]) for s in scans_of(canonical_plan)
    ] + [canonical_plan["estimates"].get("output_rows")])


def classify(expected, detected: bool) -> str:
    if expected is None:
        return "detecta" if detected else "no_detecta"
    if expected:
        return "VP" if detected else "FN"
    return "FP" if detected else "VN"


def evaluate(rule: str, snapshot_file: Path) -> list[dict]:
    engine_python = str(ROOT / "detection_engine" / "venv" / "Scripts" / "python.exe")
    out = subprocess.run(
        [engine_python, str(HERE / "evaluate.py"), str(ROOT / "detection_engine"), rule, str(snapshot_file)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def with_plan(snapshot: dict, query_id: str, canonical_plan: dict) -> dict:
    snapshot = copy.deepcopy(snapshot)
    for explain in snapshot["canonic_explains"]:
        if str(explain.get("query_id")) == query_id:
            explain["canonical_plan"] = canonical_plan
            explain["explain_source"] = "auto_explain"
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Prototipo: planes de auto_explain (v4).")
    parser.add_argument("pattern")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--pipeline-dir", type=Path, required=True)
    opts = parser.parse_args()

    pattern_dir = find_pattern(opts.pattern)
    out_dir = RESULTS_DIR / pattern_dir.name
    source = json.loads((out_dir / f"postgres_{opts.from_version}.json").read_text(encoding="utf-8"))
    snapshots_dir = out_dir / "snapshots" / opts.version
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    pipeline_dir = opts.pipeline_dir.resolve()
    sys.path.insert(0, str(pipeline_dir))
    cwd = os.getcwd()
    os.chdir(pipeline_dir)
    from config.connections import get_connection_postgres
    from stages.explain_normalizer import PostgresExplainNormalizer
    os.chdir(cwd)

    engine = get_connection_postgres()
    try:
        with engine.connect() as conn:
            entries, log_bytes = read_log_entries(conn)
    finally:
        engine.dispose()
    normalizer = PostgresExplainNormalizer()
    print(f"log: {log_bytes / 1e6:.1f} MB, {len(entries)} planes de auto_explain")

    cases = []
    for case in source["cases"]:
        result = copy.deepcopy(case)
        result.pop("findings", None)
        if not case.get("query_id") or not case.get("started_at") or case["outcome"] in ("no_encontrada",):
            result["autoexplain"] = {"motivo": "sin query_id o sin ventana"}
            cases.append(result)
            continue
        start = datetime.fromisoformat(case["started_at"])
        end = datetime.fromisoformat(case["ended_at"])
        mine = sorted(
            (e for e in entries if e["query_id"] == case["query_id"] and start <= e["at"] <= end),
            key=lambda e: e["at"],
        )
        if not mine:
            result.update({"outcome": "sin_explain", "plan_scans": [], "findings": [],
                           "autoexplain": {"planes": 0, "motivo": "ninguna ejecucion de 50 ms o mas en el log"}})
            print(f"  {case['id']:<28} {case['outcome']:<11} -> sin_explain (sin planes en el log)")
            cases.append(result)
            continue

        snapshot = json.loads((HERE / case["snapshot"]).read_text(encoding="utf-8"))
        plans = []
        for entry in mine:
            stats = normalizer.normalize({"query_explain": [{"query_id": 0, "plan": entry["plan"]}]})
            plans.append(stats["canonic_explains"][0]["canonical_plan"])

        # una evaluacion por forma de plan distinta
        shapes = Counter(shape_key(p) for p in plans)
        first_of_shape = {}
        for plan in plans:
            first_of_shape.setdefault(shape_key(plan), plan)
        detected_by_shape = {}
        for index, (key, plan) in enumerate(first_of_shape.items()):
            tmp = snapshots_dir / f"_tmp_{case['id']}_{index}.json"
            tmp.write_text(json.dumps(with_plan(snapshot, case["query_id"], plan), default=str), encoding="utf-8")
            findings = [f for f in evaluate(source["rule"], tmp) if str(f.get("query_id")) == case["query_id"]]
            detected_by_shape[key] = bool(findings)
            tmp.unlink()

        last = plans[-1]
        new_snapshot = snapshots_dir / Path(case["snapshot"]).name
        new_snapshot.write_text(json.dumps(with_plan(snapshot, case["query_id"], last), default=str), encoding="utf-8")
        findings = [f for f in evaluate(source["rule"], new_snapshot) if str(f.get("query_id")) == case["query_id"]]
        detecting = sum(n for key, n in shapes.items() if detected_by_shape[key])
        texts = Counter(e["plan"].get("Query Text", "")[:120] for e in mine)
        result.update({
            "snapshot": str(new_snapshot.relative_to(HERE)),
            "explain_source": "auto_explain",
            "plan_output_rows": last["estimates"].get("output_rows"),
            "plan_scans": scans_of(last),
            "findings": findings,
            "outcome": classify(case["expected"], bool(findings)),
            "autoexplain": {
                "planes": len(plans),
                "planes_que_detectan": detecting,
                "formas": [
                    {"veces": n, "detecta": detected_by_shape[key], "scans": scans_of(first_of_shape[key]),
                     "output_rows": first_of_shape[key]["estimates"].get("output_rows")}
                    for key, n in shapes.most_common()
                ],
                "textos": [{"veces": n, "query_text": t} for t, n in texts.most_common(5)],
            },
        })
        print(f"  {case['id']:<28} {case['outcome']:<11} -> {result['outcome']:<11} "
              f"planes={len(plans):>4} detectan={detecting:>4} formas={len(shapes)}")
        cases.append(result)

    report = {**source, "version": opts.version, "cases": cases,
              "prototipo": f"pg_autoexplain.py sobre postgres_{opts.from_version}.json",
              "log_bytes": log_bytes}
    out_file = out_dir / f"postgres_{opts.version}.json"
    out_file.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"resultado: {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
