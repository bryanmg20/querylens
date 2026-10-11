"""
Toma UN snapshot de un motor con el telemetry_pipeline que se le indique y lo
escribe en un archivo JSON. Lo usa run_ab.py; no hace falta correrlo a mano.

No encola nada ni toca querylens_db: el snapshot va directo al archivo para
que run_ab.py lo evalue con los detectores. Corre el Orchestrator del pipeline
tal cual, asi que el EXPLAIN sale de la forma que tenga esa rama:
  - feature/detection_engine (version 1): texto de active_queries
  - feature/detector-engine-api-rest (version 3): GENERIC_PLAN / QUERY_SAMPLE_TEXT

Se ejecuta con el python del venv del pipeline:
  python snapshot_once.py <pipeline_dir> <postgres|mysql> <archivo_salida>
"""

import json
import os
import sys
from pathlib import Path


def main(pipeline_dir: Path, dialect: str, out_file: Path) -> None:
    # el pipeline importa sus modulos como top-level y escribe logs relativos a su carpeta
    sys.path.insert(0, str(pipeline_dir))
    os.chdir(pipeline_dir)

    from collectors.factory import Engine_Factory
    from config.connections import get_connection_mysql, get_connection_postgres
    from orchestrator import Orchestrator

    connections = {"postgres": get_connection_postgres, "mysql": get_connection_mysql}
    engine = connections[dialect]()
    try:
        collector = Engine_Factory().create_collector(dialect, engine)
        payload = Orchestrator(collector).run_pipeline()
    finally:
        engine.dispose()

    out_file.write_text(json.dumps(payload, default=str), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[2] not in ("postgres", "mysql"):
        sys.exit("uso: python snapshot_once.py <pipeline_dir> <postgres|mysql> <archivo_salida>")
    main(Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve())
