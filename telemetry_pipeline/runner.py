import json

from config.connections import get_connection_mysql, get_connection_postgres, get_connection_querylens_db
from collectors.factory import Engine_Factory
from enqueue import send_to_queue
from orchestrator import Orchestrator

ENGINES = (
    ("postgres", get_connection_postgres),
    ("mysql", get_connection_mysql),
)


def main():
    creator = Engine_Factory()
    querylens_engine = get_connection_querylens_db()

    for dialect, engine_factory in ENGINES:
        collector = creator.create_collector(dialect, engine_factory())
        payload = Orchestrator(collector).run_pipeline()
        payload_json = json.dumps(payload, default=str)
        msg_id = send_to_queue(payload_json, querylens_engine)
        print(f"{dialect} | encolado | msg_id={msg_id}")


if __name__ == "__main__":
    main()