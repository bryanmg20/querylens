import argparse
import json
import sys

from sqlalchemy import text

sys.path.insert(0, r"C:\Users\bryan\OneDrive\Escritorio\querylens\telemetry_pipeline")
from config.connections import get_connection_querylens_db


def _engine_of(payload):
    for stmt in payload.get("top_impact_queries") or []:
        qid = stmt.get("query_id")
        if isinstance(qid, int):
            return "postgres"
        if isinstance(qid, str):
            return "mysql"
    return "unknown"


def _short(text, n=75):
    text = (text or "").replace("\n", " ").strip()
    return text[:n] + ("..." if len(text) > n else "")


def summarize(payload):
    candidates = payload.get("top_impact_queries") or []
    explains = payload.get("canonic_explains") or []
    explained_ids = {e.get("query_id") for e in explains}

    found = 0
    missed = []
    explained_but_missing_plan = []
    for c in candidates:
        if c.get("real_query_found"):
            found += 1
            if c.get("query_id") not in explained_ids:
                explained_but_missing_plan.append(c)
        else:
            missed.append(c)

    return {
        "candidates": len(candidates),
        "found": found,
        "canonic_explains": len(explains),
        "found_no_plan": len(explained_but_missing_plan),
        "missed": missed,
        "no_plan_items": explained_but_missing_plan,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6)
    args = ap.parse_args()

    engine = get_connection_querylens_db()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT msg_id, enqueued_at, message "
                "FROM pgmq.q_analyze_job ORDER BY msg_id DESC LIMIT :n"
            ),
            {"n": args.limit},
        ).mappings().all()

    if not rows:
        print("No hay mensajes en pgmq.q_analyze_job")
        return

    for row in rows:
        payload = row["message"]
        if isinstance(payload, (str, bytes, bytearray)):
            payload = json.loads(payload)
        s = summarize(payload)
        engine = _engine_of(payload)
        print(f"msg_id={row['msg_id']} {str(row['enqueued_at'])[:19]} | {engine} | "
              f"candidates={s['candidates']} found={s['found']} "
              f"canonic_explains={s['canonic_explains']} "
              f"found_no_plan={s['found_no_plan']}")

        for c in s["missed"]:
            print(f"    MISS    {c.get('query_id')} | {_short(c.get('query_text'))}")
        for c in s["no_plan_items"]:
            print(f"    !NO_PLAN {c.get('query_id')} | {_short(c.get('query_text'))}")
        print()


if __name__ == "__main__":
    main()