"""Un snapshot de UN motor, encolado con el db_id de la corrida de medicion.

Igual que ql_sandbox/scenarios/pipeline_one_engine.py, pero con el db_id como
argumento: cada corrida tiene su propia historia (statement_samples) y sus
propios hallazgos. measure.py lo copia al servicio pipeline del compose:

  python /tmp/pipeline_snapshot.py <postgres|mysql> <db_id>

Imprime `msg_id=<n>`.
"""

import json
import sys

sys.path.insert(0, "/app")

from collectors.factory import Engine_Factory  # noqa: E402
from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)
from enqueue import send_to_queue  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

CONNECTIONS = {"postgres": get_connection_postgres, "mysql": get_connection_mysql}


def main(dialect, db_id):
    collector = Engine_Factory().create_collector(dialect, CONNECTIONS[dialect]())
    payload = Orchestrator(collector).run_pipeline()
    payload["db_id"] = db_id
    msg_id = send_to_queue(json.dumps(payload, default=str), get_connection_querylens_db())
    print(f"msg_id={msg_id}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in CONNECTIONS:
        sys.exit("uso: python pipeline_snapshot.py <postgres|mysql> <db_id>")
    main(sys.argv[1], sys.argv[2])
