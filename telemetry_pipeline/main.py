from datetime import datetime, timezone

from pydantic import ValidationError

from config.connections import get_connection_querylens_db
from config.registered import Target, load_registered_targets
from collectors.factory import Engine_Factory
from enqueue import send_to_queue
from health import writer as health_writer
from health.catalog import CATALOG
from health.report import HealthReport
from logger import get_logger
from models.snapshot import SnapshotPayload
from orchestrator import Orchestrator
from stages.enrich import DB_ID

logger = get_logger(__name__)

# Observabilidad del tamaño del payload, no un gate: el limite real de retencion
# es del consumidor. Ver PIPELINE_FLOW ("Sin consumidor la cola crece").
PAYLOAD_WARN_BYTES = 1_000_000
_payload_warned: set[str] = set()


def _log_payload_size(dialect: str, db_id: str | None, payload_json: str) -> None:
    """DEBUG siempre; WARNING una vez por db_id al cruzar PAYLOAD_WARN_BYTES.

    Transicion y no repeticion (mismo patron que runner._report_state): un
    warning por ciclo serian ~8 640 lineas al dia mientras el estado persiste.
    """
    label = db_id or dialect
    size_bytes = len(payload_json.encode("utf-8"))
    logger.debug(f"{dialect} | payload | db_id={label} | {size_bytes} bytes")
    if size_bytes > PAYLOAD_WARN_BYTES and label not in _payload_warned:
        _payload_warned.add(label)
        logger.warning(
            f"{dialect} | payload | db_id={label} | {size_bytes} bytes (>"
            f"{PAYLOAD_WARN_BYTES}); inviable la retencion larga sin consumidor"
        )


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


def _health_db_id(dialect: str, db_id: str | None) -> str:
    """Identidad en pipeline_health. Sin fila registrada (sandbox) los dos
    motores comparten la constante de enrich: se separan por dialecto para que
    uno no pise el last_run del otro."""
    return db_id or f"{DB_ID}:{dialect}"


def _flush_health(querylens_engine, health_db_id: str, report: HealthReport, started_at) -> None:
    """Vuelca el report aunque la corrida no haya llegado a crear el engine de
    QueryLens (el factory del target lanzo antes): en ese caso abre uno propio
    solo para escribir y lo dispone. Si tampoco se puede, queda en el log."""
    engine = querylens_engine
    if engine is None:
        try:
            engine = get_connection_querylens_db()
        except Exception as e:
            logger.debug(f"health | sin engine de QueryLens | {type(e).__name__}: {e}")
            return
    try:
        health_writer.flush(engine, health_db_id, report, started_at)
    finally:
        if querylens_engine is None:
            _dispose(engine)


def _raise_already_recorded(report: HealthReport) -> bool:
    """Los unicos puntos que registran su code y relanzan: la conexion del
    target (Orchestrator._connect) y el encolado (_cycle)."""
    return report.has("ENQUEUE_FAILED") or any(
        CATALOG[code].scope == "connection" for code, _ in report.issues
    )


def run_engine(dialect: str, connection_factory, db_id: str | None = None):
    """Collect -> validate -> enqueue para UN solo motor. Devuelve msg_id o None.

    Quien llama decide la credencial (el factory) y la identidad (db_id): este
    modulo no decide contra que bases correr. db_id=None respeta la constante
    de enrich. Este wrapper es el que cuida el ciclo de vida: crea los dos
    engines y los dispone al terminar, exitoso o no. Tambien vuelca la salud
    de la corrida a pipeline_health, incluso cuando la corrida lanza.
    """
    target_engine = None
    querylens_engine = None
    report = HealthReport()
    started_at = datetime.now(timezone.utc)
    # Si se llego aqui, la fila registrada se descifro bien.
    report.checked("credentials")
    try:
        target_engine = connection_factory()
        querylens_engine = get_connection_querylens_db()
        return _cycle(dialect, target_engine, querylens_engine, db_id, report)
    except Exception:
        # Conexion y encolado ya dejaron su code al lanzar; cualquier otra
        # excepcion es una falla interna, aunque la corrida ya tuviera otro
        # issue blocking (p. ej. sin pg_stat_statements): no se enmascara.
        if not _raise_already_recorded(report):
            report.add("PIPELINE_FAILED")
        raise
    finally:
        _flush_health(querylens_engine, _health_db_id(dialect, db_id), report, started_at)
        if target_engine is not None:
            _dispose(target_engine)
        if querylens_engine is not None:
            _dispose(querylens_engine)


def _cycle(
    dialect: str,
    target_engine,
    querylens_engine,
    db_id: str | None = None,
    report: HealthReport | None = None,
):
    report = report if report is not None else HealthReport()
    creator = Engine_Factory()
    collector = creator.create_collector(dialect, target_engine)
    payload = Orchestrator(collector, report).run_pipeline()
    report.checked("pipeline")

    # El database_identifier de la fila registrada manda sobre la constante de
    # enrich; se pisa antes de validar para que SnapshotPayload lo arrastre.
    if db_id:
        payload["db_id"] = db_id

    try:
        snapshot = SnapshotPayload.from_snapshot(payload)
    except ValidationError as e:
        logger.error(f"{dialect} | snapshot_validation | {e}")
        report.add("SNAPSHOT_VALIDATION_FAILED")
        return None

    payload_json = snapshot.to_json()
    _log_payload_size(dialect, db_id, payload_json)
    size_bytes = len(payload_json.encode("utf-8"))
    if size_bytes > PAYLOAD_WARN_BYTES:
        report.add("PAYLOAD_TOO_LARGE", size_bytes=size_bytes)
    try:
        msg_id = send_to_queue(payload_json, querylens_engine)
    except Exception:
        report.add("ENQUEUE_FAILED")
        raise
    report.msg_id = msg_id
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
