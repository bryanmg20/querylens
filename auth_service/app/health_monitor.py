import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import settings
from app.connection_tester import test_connection
from app.database import SessionLocal
from app.models import RegisteredDatabase
from app.schemas import ConnectionParams
from app.security import decrypt_secret

logger = logging.getLogger(__name__)

MAX_PARALLEL_CHECKS = 10


def _is_reachable(record: RegisteredDatabase) -> bool | None:
    """True/False según responda la base de datos; None si no se pudo evaluar
    (por ejemplo, credenciales que no se pueden descifrar)."""

    try:
        params = ConnectionParams(
            engine=record.engine,
            host=decrypt_secret(record.host),
            port=int(decrypt_secret(record.port)),
            username=decrypt_secret(record.db_user),
            password=decrypt_secret(record.encrypted_password),
            database_name=record.database_name,
        )
    except ValueError:
        logger.exception("No se pudieron leer las credenciales de %s", record.database_identifier)
        return None
    return test_connection(params).success


def check_all_databases() -> None:
    """Prueba la conexión de cada base registrada y actualiza is_active."""

    with SessionLocal() as db:
        records = db.query(RegisteredDatabase).all()
        if not records:
            return

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHECKS) as pool:
            results = list(pool.map(_is_reachable, records))

        for record, reachable in zip(records, results):
            # Solo se escribe cuando cambia el estado, para no tocar updated_at
            # en cada revisión.
            if reachable is not None and record.is_active != reachable:
                record.is_active = reachable
                logger.info(
                    "%s ahora está %s",
                    record.database_identifier,
                    "disponible" if reachable else "no disponible",
                )
        db.commit()


async def run_health_monitor() -> None:
    while True:
        try:
            await asyncio.to_thread(check_all_databases)
        except Exception:
            # Un fallo puntual (por ejemplo, Postgres reiniciándose) no debe
            # detener el monitoreo.
            logger.exception("Falló la revisión de disponibilidad")
        await asyncio.sleep(settings.health_check_interval_seconds)
