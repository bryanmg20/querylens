from sqlalchemy import text


def send_to_queue(payload_json, engine, queue_name="analyze_job"):
    with engine.begin() as conn:
        query = text("SELECT * FROM pgmq.send(:queue_name, CAST(:payload AS JSONB)) AS msg_id;")
        result = conn.execute(query, {"queue_name": queue_name, "payload": payload_json})
        return result.mappings().first()["msg_id"]