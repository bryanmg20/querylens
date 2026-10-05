"""Regenera los goldens desde los contenedores del sandbox.

Por que este script existe y no un volcado manual: los goldens anteriores se
generaron con las ventanas de stats sin resetear, asi que el pipeline
recolecto su propia huella. Las dos unicas candidatas de MySQL eran
`SELECT SCHEMA ( )` y `SELECT ?`, queries del driver, y sus dos explains
tenian cero operaciones. El invariante de test_snapshot_contract comparaba
0 contra 0 y pasaba sin exertir ninguna presion.

El orden importa: resetear stats, cargar trafico de aplicacion, recien ahi
recolectar. Resetear sin cargar deja al pipeline solo, y entonces su propia
huella es lo unico que hay.

USAGE (con los contenedores del sandbox arriba):
    python ci/regenerate_goldens.py
    python ci/regenerate_goldens.py --battery-reps 30
"""
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE))

GOLDEN_DIR = PIPELINE / "tests" / "golden"
REPO_ROOT = PIPELINE.parent

sysbench = ["docker", "exec", "ql_sysbench", "bash", "-c"]

RESET = {
    "postgres": "PGPASSWORD=ql_pass psql --host=postgres --port=5432 "
    "--username=ql_user --dbname=ql_demo --no-psqlrc --quiet -t "
    "-c 'SELECT pg_stat_statements_reset();'",
    "mysql": "mysql --host=mysql --port=3306 --user=root --password=ql_root "
    "-e 'TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;'",
}

# El fingerprint del pipeline: queries que emite el propio collector y el propio
# ExplainStage. No son bugs, pero tampoco son telemetria de aplicacion.
PIPELINE_FINGERPRINT = (
    "set names",
    "use ",
    "select schema",
    "select ?",
    "set `autocommit`",
    "rollback",
    "explain format = json",
    "begin",
    "commit",
)


def run(cmd, check=True):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if check and proc.returncode != 0:
        raise RuntimeError(f"fallo {' '.join(cmd)}: {proc.stderr.strip()}")
    return proc.stdout


def reset_stats():
    for dialect, sql in RESET.items():
        run(sysbench + [sql])
        print(f"{dialect}: stats reseteadas")


def run_battery(reps):
    """La bateria tambien resetea, pero se corre explicito para que el orden
    quede documentado en un solo lugar."""
    print(f"bateria: {reps} reps")
    run(sysbench + [f"bash /scripts/battery.sh {reps} 0"])


def collect(dialect, connection):
    from collectors.factory import Engine_Factory
    from orchestrator import Orchestrator

    stats = Orchestrator(
        Engine_Factory().create_collector(dialect, connection())
    ).run_pipeline()
    return stats


def pipeline_share(stats):
    statements = stats.get("statements") or []
    if not statements:
        return 1.0
    fingerprint = 0
    for stmt in statements:
        text = str(stmt.get("query_text") or "").strip().lower()
        if any(text.startswith(prefix) for prefix in PIPELINE_FINGERPRINT):
            fingerprint += 1
    return fingerprint / len(statements)


def report(name, stats):
    statements = stats.get("statements") or []
    candidates = stats.get("top_impact_queries") or []
    explains = stats.get("canonic_explains") or []

    shape = Counter()
    operations = 0
    for explain in explains:
        plan = explain["canonical_plan"]
        for field, value in plan["logical_shape"].items():
            shape[field] += value
        operations += len(plan["physical_operations"])

    print(f"\n--- {name} ---")
    print(f"  statements: {len(statements)}")
    print(f"  top_impact: {len(candidates)}")
    print(f"  canonic_explains: {len(explains)}")
    print(f"  operaciones canonicas: {operations}")
    print(f"  logical_shape: {dict(shape)}")
    print(f"  huella del pipeline en statements: {pipeline_share(stats):.0%}")

    untyped = [q for q in candidates if not str(q.get("query_text") or "").strip()]
    if untyped:
        print(f"  AVISO: {len(untyped)} candidatos sin query_text")


def main():
    reps = 30
    if "--battery-reps" in sys.argv:
        reps = int(sys.argv[sys.argv.index("--battery-reps") + 1])

    reset_stats()
    run_battery(reps)

    from config.connections import get_connection_mysql, get_connection_postgres

    engines = {
        "postgres": ("postgres_snapshot.json", get_connection_postgres),
        "mysql": ("mysql_snapshot.json", get_connection_mysql),
    }

    for dialect, (filename, connection) in engines.items():
        stats = collect(dialect, connection)
        report(dialect, stats)

        if not stats.get("top_impact_queries"):
            print(f"{dialect}: sin candidatos, no se escribe el golden")
            continue
        if not stats.get("canonic_explains"):
            print(f"{dialect}: sin explains, no se escribe el golden")
            continue

        with engine_path(dialect, filename).open("w", encoding="utf-8") as handle:
            json.dump(stats, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
        print(f"{dialect}: escrito {filename}")


def engine_path(dialect, filename):
    return GOLDEN_DIR / filename


if __name__ == "__main__":
    main()
