"""
Corre UN detector del detection_engine sobre un snapshot guardado en archivo e
imprime los hallazgos como JSON. Lo usa run_ab.py; no hace falta correrlo a mano.

Siempre se usa el detection_engine de esta rama: lo que se compara entre
versiones es el EXPLAIN que trae el snapshot, no las reglas.

Se ejecuta con el python del venv del detection_engine:
  python evaluate.py <engine_dir> <regla> <snapshot.json>
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main(engine_dir: Path, rule: str, snapshot_file: Path) -> None:
    sys.path.insert(0, str(engine_dir))
    os.chdir(engine_dir)

    from anti_patterns.engine import detect_all
    from models import Snapshot

    snapshot = Snapshot.from_dict(json.loads(snapshot_file.read_text(encoding="utf-8")))
    # sin historia: cada caso arranca con las estadisticas del motor reseteadas,
    # asi que los contadores acumulados ya son la ventana del caso
    findings = detect_all(snapshot, rule=rule, history={}, captured_at=datetime.now(timezone.utc))

    print(json.dumps(
        [
            {
                "antipatron": finding.antipatron,
                "query_id": finding.query_id,
                "table_name": finding.table_name,
                "evidencia": finding.evidencia,
            }
            for finding in findings
        ],
        default=str,
    ))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("uso: python evaluate.py <engine_dir> <regla> <snapshot.json>")
    main(Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve())
