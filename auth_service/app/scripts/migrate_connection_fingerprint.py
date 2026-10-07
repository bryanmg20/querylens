# Migración única para bases de datos que ya existían antes de que la columna
# connection_fingerprint se agregara a registered_databases (ver
# app/security.py::compute_connection_fingerprint y app/routers/databases.py).
#
# El schema en querylens_database/registered_databases.sql solo se aplica en
# un volumen de Postgres nuevo (docker-entrypoint-initdb.d no reejecuta sobre
# datos existentes), asi que en un ambiente ya desplegado hay que correr esto
# a mano una sola vez:
#
#   docker compose run --rm auth-service python -m app.scripts.migrate_connection_fingerprint
#
# Se recomienda correrlo con `docker compose run` (contenedor efímero) ANTES
# de reiniciar el auth-service que sirve trafico real, para no dejar una
# ventana en la que el código nuevo intente leer/escribir una columna que
# todavía no existe.
#
# Qué hace:
#   1. Agrega la columna connection_fingerprint (nullable) si no existe.
#   2. Descifra host/port/db_user de cada fila y calcula su fingerprint.
#   3. Si hay filas duplicadas (mismo fingerprint), conserva la más reciente
#      (created_at) y borra las demás.
#   4. Deja la columna NOT NULL + UNIQUE.
from sqlalchemy import text

from app.database import engine
from app.security import compute_connection_fingerprint, decrypt_secret

CONSTRAINT_NAME = "registered_databases_connection_fingerprint_key"


def run() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE registered_databases "
                "ADD COLUMN IF NOT EXISTS connection_fingerprint VARCHAR(64)"
            )
        )

        rows = conn.execute(
            text(
                "SELECT id, engine, host, port, db_user, database_name, created_at "
                "FROM registered_databases "
                "ORDER BY created_at ASC"
            )
        ).mappings().all()

        # Se recorren de la mas antigua a la mas reciente: si dos filas
        # comparten fingerprint, la mas reciente reemplaza a la anterior como
        # la que se conserva, y la anterior queda marcada para borrar.
        fingerprint_by_id: dict[str, str] = {}
        keep_id_by_fingerprint: dict[str, str] = {}
        duplicate_ids: list[str] = []

        for row in rows:
            row_id = str(row["id"])
            fingerprint = compute_connection_fingerprint(
                engine=row["engine"],
                host=decrypt_secret(row["host"]),
                port=int(decrypt_secret(row["port"])),
                username=decrypt_secret(row["db_user"]),
                database_name=row["database_name"],
            )
            fingerprint_by_id[row_id] = fingerprint

            previous_id = keep_id_by_fingerprint.get(fingerprint)
            if previous_id is not None:
                duplicate_ids.append(previous_id)
            keep_id_by_fingerprint[fingerprint] = row_id

        for row_id, fingerprint in fingerprint_by_id.items():
            conn.execute(
                text(
                    "UPDATE registered_databases "
                    "SET connection_fingerprint = :fingerprint WHERE id = :id"
                ),
                {"fingerprint": fingerprint, "id": row_id},
            )

        if duplicate_ids:
            conn.execute(
                # Espacio antes de "::" a proposito: SQLAlchemy no reconoce
                # bien un bind param seguido inmediatamente de "::" (cast).
                text("DELETE FROM registered_databases WHERE id = ANY(:ids ::uuid[])"),
                {"ids": duplicate_ids},
            )
            print(f"Filas duplicadas eliminadas ({len(duplicate_ids)}): {duplicate_ids}")
        else:
            print("No se encontraron filas duplicadas.")

        conn.execute(
            text(
                "ALTER TABLE registered_databases "
                "ALTER COLUMN connection_fingerprint SET NOT NULL"
            )
        )

        constraint_exists = conn.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
            {"name": CONSTRAINT_NAME},
        ).first()
        if not constraint_exists:
            conn.execute(
                text(
                    f"ALTER TABLE registered_databases "
                    f"ADD CONSTRAINT {CONSTRAINT_NAME} UNIQUE (connection_fingerprint)"
                )
            )

    print("Migración completa.")


if __name__ == "__main__":
    run()
