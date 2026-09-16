from pydantic import ValidationError

from config.connections import get_connection_mysql, get_connection_postgres, get_connection_querylens_db
from collectors.factory import Engine_Factory
from enqueue import send_to_queue
from logger import get_logger
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator

logger = get_logger(__name__)

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

        try:
            snapshot = SnapshotPayload.from_snapshot(payload)
        except ValidationError as e:
            logger.error(f"{dialect} | snapshot_validation | {e}")
            continue

        payload_json = snapshot.to_json()
        msg_id = send_to_queue(payload_json, querylens_engine)
        print(f"Diccionario encolado con ID: {msg_id}")


if __name__ == "__main__":
    main()