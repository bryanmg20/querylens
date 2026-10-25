import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from querylens_contract import SnapshotPayload
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from models import Hallazgo
from anti_patterns.statement_history import STALE_AFTER, next_statement_histories
from anti_patterns.engine import detect_all
from deadlocks import run_contention
from querylens_connection import get_connection_querylens_db
from storage import (
    ensure_hallazgos_table,
    ensure_statement_samples_table,
    load_statement_history,
    save_findings,
    save_statement_histories,
)
from logger import get_logger

logger = get_logger(__name__)

QUEUE_NAME = "analyze_job" # Cola de la que el Telemetry Pipeline publica los snapshots a analizar
POLL_INTERVAL_SECONDS = 5 # Tiempo de espera si la cola esta vacia
VISIBILITY_TIMEOUT_SECONDS = 30 # Tiempo que el mensaje queda oculto mientras se procesa
MAX_RECONNECT_BACKOFF_SECONDS = 60 # Tope de espera entre reintentos con la base caida


# Marca de vida para el healthcheck de docker-compose: se toca cada vez que la
# cola responde. Sin HEARTBEAT_FILE (fuera del contenedor) no hace nada.
def beat() -> None:
    heartbeat_file = os.getenv("HEARTBEAT_FILE")
    if heartbeat_file:
        Path(heartbeat_file).touch()


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


# Valida el mensaje contra el contrato, corre las reglas y reporta los hallazgos
def process_job(engine: Engine, job: dict) -> list[Hallazgo]:
    msg_id = job["msg_id"]
    payload = job["message"]
    # lanza ValidationError si el mensaje no cumple el contrato del pipeline
    snapshot = SnapshotPayload.model_validate(payload)
    # el snapshot no trae hora de captura; enqueued_at es lo mas cercano y es
    # estable entre reintentos del mismo job
    captured_at = job.get("enqueued_at") or datetime.now(timezone.utc)

    # la linea base necesita el estado que dejaron los jobs anteriores; la
    # tabla se indexa por db_id, sin el no hay a que asociar la historia
    history = load_statement_history(engine, snapshot.db_id) if snapshot.db_id else {}
    if not snapshot.db_id:
        logger.warning("  Job %s sin db_id: se omite la linea base (AP-01).", msg_id)

    # Corre las reglas y los reporta en logs y stdout
    findings = detect_all(snapshot, history=history, captured_at=captured_at)
    # contencion (CT-01 a CT-05) con su propio estado; sus hallazgos se guardan con los demas
    findings += run_contention(engine, snapshot, fallback_captured_at=captured_at)

    logger.info(
        "Job %s recibido | db_id=%s | enqueued_at=%s",
        msg_id, snapshot.db_id, job.get("enqueued_at"),
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
    print(f"db_id: {snapshot.db_id}")
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

    save_findings(engine, snapshot.db_id, findings)
    # despues de detectar: si se guardara antes, la ventana actual entraria en su propia linea base
    if snapshot.db_id:
        save_statement_histories(
            engine,
            snapshot.db_id,
            captured_at,
            next_statement_histories(snapshot, history, captured_at),
            stale_before=captured_at - STALE_AFTER,
        )

    return findings


# Loop principal: hace polling continuo de la cola y procesa cada job
def main(oneshot: bool = False) -> None:
    logger.info("Detection Engine iniciado. Escuchando cola '%s'...", QUEUE_NAME)
    engine = get_connection_querylens_db()
    ensure_hallazgos_table(engine)
    ensure_statement_samples_table(engine)

    backoff = POLL_INTERVAL_SECONDS
    try:
        while True:
            # Lee el siguiente mensaje disponible en la cola
            try:
                job = read_next_job(engine)
            except OperationalError as error:
                # Base caida o reiniciandose: esperar (doblando, con tope) y
                # reintentar, en vez de terminar el proceso
                if oneshot:
                    raise
                logger.warning(
                    "Sin conexion a la base (%s). Reintento en %ss.",
                    type(error.orig).__name__ if error.orig else type(error).__name__, backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_SECONDS)
                continue
            backoff = POLL_INTERVAL_SECONDS
            beat()

            # Sin mensajes: esperar y volver a preguntar
            if job is None:
                # oneshot: cortar el loop en vez de seguir esperando mensajes
                if oneshot:
                    logger.info("No hay mensajes disponibles en '%s'.", QUEUE_NAME)
                    return
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            try:
                # Flujo normal: procesar el job y archivarlo solo si no hubo errores
                process_job(engine, job)
                archive_job(engine, job["msg_id"])
                logger.info("Job %s procesado y archivado en a_%s.", job["msg_id"], QUEUE_NAME)
                if oneshot:
                    return
            except ValidationError as error:
                # Un mensaje que no cumple el contrato tampoco lo va a cumplir en
                # el reintento: se archiva para no reintentarlo para siempre
                logger.error(
                    "Job %s no cumple el contrato del snapshot; se archiva sin procesar: %s",
                    job["msg_id"], error,
                )
                archive_job(engine, job["msg_id"])
                if oneshot:
                    return
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
    # Permite correr un solo job desde la terminal con --oneshot, util para pruebas
    parser = argparse.ArgumentParser(description="Consume jobs del Detection Engine.")
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Procesa un mensaje disponible y termina; no activa polling continuo.",
    )
    main(oneshot=parser.parse_args().oneshot)
