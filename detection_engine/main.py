import time

from sqlalchemy import text
from sqlalchemy.engine import Engine

from models import Hallazgo, Snapshot
from detection_engine.anti_patterns import detect_all
from querylens_connection import get_connection_querylens_db
from logger import get_logger

logger = get_logger(__name__)

QUEUE_NAME = "analyze_job" # Cola de la que el Telemetry Pipeline publica los snapshots a analizar
POLL_INTERVAL_SECONDS = 5 # Tiempo de espera si la cola esta vacia
VISIBILITY_TIMEOUT_SECONDS = 30 # Tiempo que el mensaje queda oculto mientras se procesa


# Lee el siguiente mensaje disponible en la cola sin eliminarlo
def read_next_job(engine: Engine) -> dict | None:
    with engine.begin() as conn:
        # El mensaje queda invisible por VISIBILITY_TIMEOUT_SECONDS para que otro consumidor no lo tome mientras se procesa
        result = conn.execute(
            text("SELECT * FROM pgmq.read(:queue, :vt, :qty)"),
            {"queue": QUEUE_NAME, "vt": VISIBILITY_TIMEOUT_SECONDS, "qty": 1},
        )
        row = result.mappings().first()

    return dict(row) if row else None


# Marca un mensaje como procesado, moviendolo al historico en vez de borrarlo
def archive_job(engine: Engine, msg_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pgmq.archive(:queue, :msg_id)"),
            {"queue": QUEUE_NAME, "msg_id": msg_id},
        )


# Convierte el mensaje en un Snapshot, corre las reglas y reporta los hallazgos
def process_job(job: dict) -> list[Hallazgo]:
    msg_id = job["msg_id"]
    payload = job["message"]
    snapshot = Snapshot.from_dict(payload)
    
    # Obtiene hallazgos de la regla y los reporta en logs y stdout
    findings = detect_all(snapshot, rule="disk_spill") # Por ahora solo se corre la regla de disk spill

    logger.info(
        "Job %s recibido | source=%s | enqueued_at=%s",
        msg_id, snapshot.source, job.get("enqueued_at"),
    )
    logger.info(
        "  statements=%d | tables=%d | indexes=%d | active_queries=%d | locks=%d",
        len(snapshot.statements),
        len(snapshot.tables),
        len(snapshot.indexes),
        len(snapshot.active_queries),
        len(snapshot.locks),
    )
    logger.info(
        "  canonic_explains=%d | top_impact_queries=%d | non_explainable_candidates=%d",
        len(snapshot.canonic_explains),
        len(snapshot.top_impact_queries),
        len(snapshot.non_explainable_candidates),
    )

    if not snapshot.statements:
        logger.warning("  Job %s no trae 'statements'. Snapshot vacio?", msg_id)

    logger.info("  hallazgos=%d", len(findings))
    print(f"\n--- RESULTADOS (msg_id={msg_id}) ---")
    print(f"Source: {snapshot.source}")
    print(f"Hallazgos: {len(findings)}")
    for finding in findings:
        print(
            f"- {finding.antipatron} | severidad={finding.severidad} "
            f"| query_id={finding.query_id} | table={finding.table_name}"
        )
        logger.warning(
            "  hallazgo=%s | severidad=%s | query_id=%s | table=%s",
            finding.antipatron,
            finding.severidad,
            finding.query_id,
            finding.table_name,
        )
    print("----------------------------------\n")

    return findings


# Loop principal: hace polling continuo de la cola y procesa cada job
def main() -> None:
    logger.info("Detection Engine iniciado. Escuchando cola '%s'...", QUEUE_NAME)
    engine = get_connection_querylens_db()

    try:
        while True:
            # Lee el siguiente mensaje disponible en la cola
            job = read_next_job(engine)

            # Sin mensajes: esperar y volver a preguntar
            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            try:
                # Flujo normal: procesar el job y archivarlo solo si no hubo errores
                process_job(job)
                archive_job(engine, job["msg_id"])
                logger.info("Job %s procesado y archivado en a_%s.", job["msg_id"], QUEUE_NAME)
            except Exception:
                # No se archiva: el mensaje vuelve a quedar visible para reintentar
                logger.exception(
                    "Error procesando job %s. Se deja en la cola para reintento "
                    "(vuelve a ser visible en %ss).",
                    job["msg_id"], VISIBILITY_TIMEOUT_SECONDS,
                )
    except KeyboardInterrupt:
        logger.info("Detenido por el usuario.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
