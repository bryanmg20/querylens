import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import text

PIPELINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE)
from config.connections import get_connection_querylens_db

PY = os.path.join(PIPELINE, "venv", "Scripts", "python.exe")
RESULTS_DIR = os.path.join(PIPELINE, "results")


def op_of(query_text):
    head = (query_text or "").lstrip().split(None, 1)[0].upper()
    if head in ("SELECT", "UPDATE", "INSERT", "DELETE", "WITH", "REPLACE", "MERGE"):
        return head
    return "OTHER"


def engine_of(payload):
    for c in payload.get("top_impact_queries") or []:
        if isinstance(c.get("query_id"), int):
            return "postgres"
        if isinstance(c.get("query_id"), str):
            return "mysql"
    return "unknown"


def activity_found_ids(payload):
    """V1 (version antigua): la query real se encontraba cruzando el query_id
    del candidato contra la tabla de actividad (pg_stat_activity /
    performance_schema.events_statements_current) capturada en active_queries.
    Se reproduce la regla del select_explain_ready anterior a c730da1, que
    marcaba real_query_found=True si el query_id estaba presente en la ventana
    de actividad del snapshot (sin depender de los logs del servidor)."""
    return {
        a.get("query_id")
        for a in (payload.get("active_queries") or [])
        if a.get("query_id") is not None
    }


def new_bucket(total, v1, v2, both, solo_v1, solo_v2, ninguno):
    return {
        "total": total,
        "v1_found": v1,
        "v2_found": v2,
        "both": both,
        "solo_v1": solo_v1,
        "solo_v2": solo_v2,
        "ninguno": ninguno,
    }


def per_op_count(stats_by_mech, mech, op, found):
    o = stats_by_mech[mech].setdefault(op, {"total": 0, "found": 0})
    o["total"] += 1
    if found:
        o["found"] += 1


def run_sample(sample_i, reps, noise):
    os.environ["QL_LOG_SINCE"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    subprocess.run(
        ["docker", "exec", "ql_sysbench", "bash", "/scripts/battery.sh", reps, noise],
        check=True,
    )
    subprocess.run([PY, os.path.join(PIPELINE, "main.py")], check=True)

    with get_connection_querylens_db().connect() as conn:
        rows = conn.execute(
            text("SELECT msg_id, message FROM pgmq.q_analyze_job ORDER BY msg_id DESC LIMIT 2")
        ).mappings().all()

    print(f"\n=== Muestra {sample_i} ===", flush=True)
    for r in rows:
        payload = r["message"]
        if isinstance(payload, (str, bytes, bytearray)):
            payload = json.loads(payload)

        e = engine_of(payload)
        active_ids = activity_found_ids(payload)

        total = both = solo_v1 = solo_v2 = ninguno = v1 = v2 = 0
        op = {"v1": {}, "v2": {}}
        solo_v1_list = []
        solo_v2_list = []

        for cand in payload.get("top_impact_queries") or []:
            total += 1
            q = cand.get("query_text") or ""
            opname = op_of(q)
            qid = cand.get("query_id")

            v1_hit = qid in active_ids
            v2_hit = bool(cand.get("real_query_found"))

            if v1_hit:
                v1 += 1
            if v2_hit:
                v2 += 1

            if v1_hit and v2_hit:
                both += 1
            elif v1_hit:
                solo_v1 += 1
                solo_v1_list.append((opname, qid, q.replace("\n", " ")[:70]))
            elif v2_hit:
                solo_v2 += 1
                solo_v2_list.append((opname, qid, q.replace("\n", " ")[:70]))
            else:
                ninguno += 1

            per_op_count(op, "v1", opname, v1_hit)
            per_op_count(op, "v2", opname, v2_hit)

        expl = len(payload.get("canonic_explains") or [])
        print(
            f"  msg_id={r['msg_id']} {e}: v1(activity)={v1}/{total} "
            f"v2(logs)={v2}/{total} | both={both} solo_v1={solo_v1} "
            f"solo_v2={solo_v2} ninguno={ninguno} explains={expl}",
            flush=True,
        )

        yield {
            "sample": sample_i,
            "engine": e,
            "msg_id": r["msg_id"],
            "buckets": new_bucket(total, v1, v2, both, solo_v1, solo_v2, ninguno),
            "op": op,
            "solo_v1_list": solo_v1_list,
            "solo_v2_list": solo_v2_list,
            "explains": expl,
        }


def stats_holder():
    return {
        "samples": [],
        "op": {"v1": {}, "v2": {}},
        "solo_v1": [],
        "solo_v2": [],
        "buckets": new_bucket(0, 0, 0, 0, 0, 0, 0),
    }


def _merge_op(dst, src):
    for mech, ops in src.items():
        for opname, o in ops.items():
            d = dst[mech].setdefault(opname, {"total": 0, "found": 0})
            d["total"] += o["total"]
            d["found"] += o["found"]


def _merge_buckets(dst, src):
    for k in src:
        dst[k] += src[k]


def aggregate(stats_by_engine, raw_muestras):
    agg = {}
    for e, holder in stats_by_engine.items():
        buckets = holder["buckets"]
        v1_found = buckets["v1_found"]
        v2_found = buckets["v2_found"]
        total = buckets["total"]
        agg[e] = {
            "totales": {
                "top_impact_candidates": total,
                "v1_found": v1_found,
                "v2_found": v2_found,
                "both": buckets["both"],
                "solo_v1": buckets["solo_v1"],
                "solo_v2": buckets["solo_v2"],
                "ninguno": buckets["ninguno"],
                "v1_rate": 100 * v1_found / max(1, total),
                "v2_rate": 100 * v2_found / max(1, total),
            },
            "por_muestra": [s["buckets"] for s in raw_muestras if s["engine"] == e],
            "varianza_v1_pct_por_muestra": [
                100 * b["v1_found"] / max(1, b["total"]) for b in holder["samples"]
            ],
            "varianza_v2_pct_por_muestra": [
                100 * b["v2_found"] / max(1, b["total"]) for b in holder["samples"]
            ],
            "por_operacion": holder["op"],
            "solo_v1_candidatos": holder["solo_v1"],
            "solo_v2_candidatos": holder["solo_v2"],
        }
    return agg


def build_report(agg, args):
    lines = []
    lines.append("=" * 72)
    lines.append(f"COMPARATIVA REAL QUERY  V1(actividad) vs V2(logs)  "
                 f"({args.samples} muestras x {args.reps} reps, noise={args.noise})")
    lines.append("=" * 72)

    for e, st in agg.items():
        t = st["totales"]
        lines.append(f"\n-- {e} --")
        lines.append(f"  top_impact_candidates {t['top_impact_candidates']}")

        lines.append("  1) RESULTADO GLOBAL")
        lines.append(f"     V1 actvidades: {t['v1_found']} ({t['v1_rate']:.1f}%)   "
                     f"V2 logs: {t['v2_found']} ({t['v2_rate']:.1f}%)   "
                     f"diferencia={t['v2_rate'] - t['v1_rate']:+.1f} p.p.")
        lines.append(f"     both={t['both']}  solo_v1={t['solo_v1']}  "
                     f"solo_v2={t['solo_v2']}  ninguno={t['ninguno']}")

        lines.append("  2) VARIANZA ENTRE MUESTRAS")
        for mech, key in (("V1 actividad", "varianza_v1_pct_por_muestra"),
                          ("V2 logs", "varianza_v2_pct_por_muestra")):
            rates = st[key]
            if rates:
                lines.append(f"     {mech:<13} mean={statistics.mean(rates):.1f}%  "
                             f"min={min(rates):.0f}% max={max(rates):.0f}%  "
                             f"stdev={statistics.pstdev(rates):.1f}  "
                             f"al 100%={sum(1 for r in rates if r == 100)}/{len(rates)}")

        lines.append("  3) MISSES POR OPERACION  (v1 vs v2)")
        for opname in sorted(set(st["por_operacion"]["v1"]) | set(st["por_operacion"]["v2"]),
                             key=lambda o: -st["por_operacion"]["v1"].get(o, {"total": 0}).get("total", 0)):
            v1 = st["por_operacion"]["v1"].get(opname, {"total": 0, "found": 0})
            v2 = st["por_operacion"]["v2"].get(opname, {"total": 0, "found": 0})
            p1 = 100 * v1["found"] / max(1, v1["total"])
            p2 = 100 * v2["found"] / max(1, v2["total"])
            lines.append(f"     {opname:<8} v1={v1['found']}/{v1['total']} ({p1:.0f}%)   "
                         f"v2={v2['found']}/{v2['total']} ({p2:.0f}%)")

        lines.append("  4) CANDIDATOS QUE SOLO RECUPERA UNA MECANICA")
        if st["solo_v1_candidatos"]:
            lines.append(f"     solo-V1 (actividad): {len(st['solo_v1_candidatos'])}")
            for opname, qid, preview in st["solo_v1_candidatos"][:6]:
                lines.append(f"       [{opname}] id={qid} {preview}")
        else:
            lines.append("     solo-V1 (actividad): ninguno")
        if st["solo_v2_candidatos"]:
            lines.append(f"     solo-V2 (logs): {len(st['solo_v2_candidatos'])}")
            for opname, qid, preview in st["solo_v2_candidatos"][:6]:
                lines.append(f"       [{opname}] id={qid} {preview}")
        else:
            lines.append("     solo-V2 (logs): ninguno")

    lines.append("\n" + "=" * 72)
    return "\n".join(lines)


def save_results(agg, muestras, report, args, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "meta": {
            "script": os.path.basename(__file__),
            "muestras": args.samples,
            "reps": args.reps,
            "noise": args.noise,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "v1": "select_explain_ready antiguo: query_id en active_queries "
                  "(pg_stat_activity / events_statements_current)",
            "v2": "LogsBackfillStage actual: firma canonica del query en "
                  "pg_csvlog / slow log de MySQL",
        },
        "por_engine": agg,
        "muestras_raw": muestras,
    }
    json_path = os.path.join(out_dir, "compare_real_query_mechanics.json")
    txt_path = os.path.join(out_dir, "compare_real_query_mechanics.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    return json_path, txt_path


def main():
    parser = argparse.ArgumentParser(description=(
        "Compara la mecanica V1 (tabla de actividad: query_id en active_queries) "
        "contra V2 (logs del servidor) para recuperar la query real."))
    parser.add_argument("samples", nargs="?", type=int, default=3, help="numero de muestras")
    parser.add_argument("reps", nargs="?", default="10", help="reps de la bateria")
    parser.add_argument("noise", nargs="?", default="150", help="queries de ruido tras la bateria")
    parser.add_argument("--out", default=RESULTS_DIR, help="directorio de salida (json + txt)")
    args = parser.parse_args()

    assert args.samples >= 1

    stats_by_engine = {}
    muestras = []
    for i in range(1, args.samples + 1):
        try:
            for entry in run_sample(i, args.reps, args.noise):
                e = entry["engine"]
                holder = stats_by_engine.setdefault(e, stats_holder())
                holder["samples"].append(entry["buckets"])
                _merge_op(holder["op"], entry["op"])
                holder["solo_v1"] += entry["solo_v1_list"]
                holder["solo_v2"] += entry["solo_v2_list"]
                _merge_buckets(holder["buckets"], entry["buckets"])
                muestras.append(entry)
        except Exception as ex:
            print(f"  ERROR en muestra {i}: {ex}")

    agg = aggregate(stats_by_engine, muestras)
    report = build_report(agg, args)
    print("\n" + report)
    json_path, txt_path = save_results(agg, muestras, report, args, args.out)
    print(f"\nResultados guardados:\n  {json_path}\n  {txt_path}")


if __name__ == "__main__":
    main()