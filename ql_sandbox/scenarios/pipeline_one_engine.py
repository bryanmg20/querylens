"""
Corre telemetry_pipeline para UN solo motor y encola el snapshot con un db_id
propio de ese motor. Lo usa run_scenario.py; no hace falta correrlo a mano.

Hace lo mismo que telemetry_pipeline/main.py (mismas clases, mismo encolado),
con dos diferencias pensadas solo para las pruebas:
  - toma el snapshot de un motor, no de los dos;
  - reemplaza el db_id fijo (querylens-db-01, igual en ambos motores) por
    "<db_id>-<motor>", para que statement_samples y hallazgos de cada motor no
    se mezclen mientras el pipeline no lo resuelva por su cuenta.

Se ejecuta con el python del venv de telemetry_pipeline:
  python pipeline_one_engine.py <postgres|mysql>
o, con run_scenario.py --docker, dentro del servicio pipeline del compose.

Conexion al motor: con MONITOR_PG_HOST / MONITOR_MY_HOST definidas (venv, CI)
se usan esas variables; si no (el servicio pipeline ya no las tiene: toma las
bases de registered_databases), la base registrada y activa de ese motor,
igual que el runner.
"""

import json
import os
import sys
from pathlib import Path

# QL_PIPELINE_DIR: con run_scenario.py --docker este archivo se copia al
# servicio pipeline del compose, donde el codigo esta en /app
PIPELINE_DIR = Path(os.getenv("QL_PIPELINE_DIR") or Path(__file__).resolve().parents[2] / "telemetry_pipeline")
sys.path.insert(0, str(PIPELINE_DIR))

from collectors.factory import Engine_Factory  # noqa: E402
from config.connections import (  # noqa: E402
    get_connection_mysql,
    get_connection_postgres,
    get_connection_querylens_db,
)
from config.registered import load_registered_targets  # noqa: E402
from enqueue import send_to_queue  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402

CONNECTIONS = {
    "postgres": get_connection_postgres,
    "mysql": get_connection_mysql,
}
HOST_VARIABLES = {"postgres": "MONITOR_PG_HOST", "mysql": "MONITOR_MY_HOST"}


def connection_for(dialect: str):
    if os.getenv(HOST_VARIABLES[dialect]):
        return CONNECTIONS[dialect]()
    for target in load_registered_targets() or []:
        if target.dialect == dialect:
            return target.factory()
    sys.exit(
        f"{dialect}: no hay {HOST_VARIABLES[dialect]} ni una base registrada y activa de ese motor"
    )


def main(dialect: str) -> None:
    collector = Engine_Factory().create_collector(dialect, connection_for(dialect))
    payload = Orchestrator(collector).run_pipeline()
    payload["db_id"] = f"{payload.get('db_id')}-{dialect}"

    msg_id = send_to_queue(json.dumps(payload, default=str), get_connection_querylens_db())
    print(f"{dialect} | db_id={payload['db_id']} | encolado con ID: {msg_id}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in CONNECTIONS:
        sys.exit("uso: python pipeline_one_engine.py <postgres|mysql>")
    main(sys.argv[1])
