from pydantic import ValidationError

from config.connections import get_connection_querylens_db
from config.registered import Target, load_registered_targets
from collectors.factory import Engine_Factory
from enqueue import send_to_queue
from logger import get_logger
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator

logger = get_logger(__name__)


def _dispose(engine) -> None:
    """Cierra el pool de un engine.

    run_engine crea dos engines por llamada (el del target y el de la cola),
    asi que un proceso que cicla como runner.py debe soltarlos: sin dispose,
    cada ciclo deja pools abiertos y a las horas se agota max_connections.
    Un engine ya dispuesto y vuelto a crear por el factory no pierde nada.
    """
    try:
        engine.dispose()
    except Exception as e:
        logger.debug(f"dispose | {type(e).__name__}: {e}")


def run_engine(dialect: str, connection_factory, db_id: str | None = None):
    """Collect -> validate -> enqueue para UN solo motor. Devuelve msg_id o None.

    Quien llama decide la credencial (el factory) y la identidad (db_id): este
    modulo no decide contra que bases correr. db_id=None respeta la constante
    de enrich. Este wrapper es el que cuida el ciclo de vida: crea los dos
    engines y los dispone al terminar, exitoso o no.
    """
    target_engine = connection_factory()
    querylens_engine = get_connection_querylens_db()
    try:
        return _cycle(dialect, target_engine, querylens_engine, db_id)
    finally:
        _dispose(target_engine)
        _dispose(querylens_engine)


def _cycle(dialect: str, target_engine, querylens_engine, db_id: str | None = None):
    creator = Engine_Factory()
    collector = creator.create_collector(dialect, target_engine)
    payload = Orchestrator(collector).run_pipeline()

    # El database_identifier de la fila registrada manda sobre la constante de
    # enrich; se pisa antes de validar para que SnapshotPayload lo arrastre.
    if db_id:
        payload["db_id"] = db_id

    try:
        snapshot = SnapshotPayload.from_snapshot(payload)
    except ValidationError as e:
        logger.error(f"{dialect} | snapshot_validation | {e}")
        return None

    payload_json = snapshot.to_json()
    msg_id = send_to_queue(payload_json, querylens_engine)
    print(f"Diccionario encolado con ID: {msg_id}")
    return msg_id


def run_targets(targets) -> None:
    """Corre cada target aislando fallos: una base que no conecta no tumbar el resto."""
    for target in targets:
        try:
            run_engine(target.dialect, target.factory, target.db_id)
        except Exception as e:
            label = target.db_id or target.dialect
            logger.error(f"{label} | engine_failed | {e}")


def main():
    """Entrada de integracion: un snapshot por base registrada y activa.

    Las credenciales salen de registered_databases; si no hay ninguna usable
    no se extrae nada y el motivo queda en el log. El par postgres/mysql del
    sandbox y de CI esta en main_sandbox.py.
    """
    targets = load_registered_targets()
    if not targets:
        logger.warning("main | sin bases registradas activas | nada que extraer")
        return
    logger.info(f"main | {len(targets)} base(s) registrada(s) a extraer")
    run_targets(targets)


if __name__ == "__main__":
    main()
