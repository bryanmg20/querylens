# Migración única para bases de datos que ya existían antes de que la columna
# monitoring_enabled se agregara a registered_databases.
#
# El schema en querylens_database/registered_databases.sql solo se aplica en
# un volumen de Postgres nuevo, asi que en un ambiente ya desplegado hay que
# correr esto a mano una sola vez, ANTES de reiniciar el auth-service y el
# pipeline:
#
#   docker compose run --rm auth-service python -m app.scripts.migrate_monitoring_enabled
#
# Agrega la columna monitoring_enabled (BOOLEAN NOT NULL DEFAULT TRUE) si no
# existe; las filas existentes quedan con el monitoreo habilitado.
from sqlalchemy import text

from app.database import engine


def run() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE registered_databases "
                "ADD COLUMN IF NOT EXISTS monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE"
            )
        )
    print("Migración completa.")


if __name__ == "__main__":
    run()
