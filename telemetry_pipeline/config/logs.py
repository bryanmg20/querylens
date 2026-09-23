from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent

LOG_SOURCES = {
    "postgres": {
        "enabled": True,
        "path": str(PIPELINE_DIR.parent / "ql_sandbox" / "pg_logs" / "postgresql.csv"),
        "min_duration_ms": 0,
    },
    "mysql": {
        "enabled": True,
        "path": str(PIPELINE_DIR.parent / "ql_sandbox" / "mysql_logs" / "ql-slow.log"),
        "min_duration_seconds": 0,
    },
}