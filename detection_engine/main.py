"""
Detection Engine - esqueleto (Paso 1)
======================================

Este servicio hace polling de la cola `analyze_job` en querylens_database
(Postgres + extension pgmq), donde el Telemetry Pipeline deja los snapshots
recolectados de PostgreSQL/MySQL (ver telemetry_pipeline/main.py, que hace
pgmq.send al mismo queue_name usado aqui).

Por ahora este archivo NO implementa ninguna regla de deteccion. Su unico
objetivo es validar el extremo a extremo:

    Telemetry Pipeline -> pgmq.analyze_job -> Detection Engine

Cuando esto funcione (veas los logs con los conteos de cada snapshot),
el siguiente paso es reemplazar `process_job` por el motor de reglas real.
"""

import time

from sqlalchemy import text
from sqlalchemy.engine import Engine

from querylens_connection import get_connection_querylens_db
from logger import get_logger

logger = get_logger(__name__)

# Mismo nombre de cola que usa telemetry_pipeline/main.py al hacer pgmq.send
QUEUE_NAME = "analyze_job"
POLL_INTERVAL_SECONDS = 5
VISIBILITY_TIMEOUT_SECONDS = 30


def read_next_job(engine: Engine) -> dict | None:
    """
    Lee (sin borrar) el siguiente mensaje disponible en la cola analyze_job.

    pgmq.read(queue, vt, qty) hace invisible el mensaje durante `vt` segundos.
    Si el proceso falla antes de llamar a pgmq.delete, el mensaje vuelve a
    quedar visible automaticamente y otro intento puede procesarlo.

    `engine.begin()` abre una transaccion y hace commit solo si el bloque
    termina sin excepciones; pgmq.read modifica el estado interno del mensaje
    (su vt), asi que necesita ese commit para que el cambio quede persistido.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT * FROM pgmq.read(:queue, :vt, :qty)"),
            {"queue": QUEUE_NAME, "vt": VISIBILITY_TIMEOUT_SECONDS, "qty": 1},
        )
        row = result.mappings().first()

    return dict(row) if row else None


def archive_job(engine: Engine, msg_id: int) -> None:
    """
    Confirma el procesamiento exitoso moviendo el mensaje de q_analyze_job
    a a_analyze_job (en vez de borrarlo con pgmq.delete).

    Esto nos deja gratis el historial completo de snapshots procesados:
    - Trazabilidad: cada hallazgo puede apuntar al msg_id exacto que lo
      origino, y ese snapshot completo sigue disponible para auditoria.
    - Linea base: mas adelante podemos leer a_analyze_job para calcular
      la evolucion de mean_time_ms por query_id a lo largo del tiempo,
      sin haber tenido que disenar nosotros una tabla de historico crudo.
    """
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pgmq.archive(:queue, :msg_id)"),
            {"queue": QUEUE_NAME, "msg_id": msg_id},
        )


def process_job(job: dict) -> None:
    """
    Punto de entrada del analisis.

    `job["message"]` ya llega como dict: psycopg2 parsea columnas jsonb a
    dict/list de Python automaticamente. Las claves coinciden con lo que
    arma collectors/base.py + collectors/postgres/collector.py: source,
    statements, tables, indexes, active_queries, locks, query_explain,
    canonic_explains, high_impact_statements, unstable_statements,
    disk_spill_statements, explain_candidates, non_explainable_candidates.

    Por ahora solo lo inspeccionamos. Aqui es donde, en el siguiente paso, se
    llamara al motor de reglas y al modulo de contencion sobre `payload`.
    """
    msg_id = job["msg_id"]
    payload = job["message"]

    logger.info(
        "Job %s recibido | source=%s | enqueued_at=%s",
        msg_id, payload.get("source"), job.get("enqueued_at"),
    )
    logger.info(
        "  statements=%d | tables=%d | indexes=%d | active_queries=%d | locks=%d",
        len(payload.get("statements", [])),
        len(payload.get("tables", [])),
        len(payload.get("indexes", [])),
        len(payload.get("active_queries", [])),
        len(payload.get("locks", [])),
    )
    logger.info(
        "  query_explain=%d | canonic_explains=%d | disk_spill_statements=%d | unstable_statements=%d",
        len(payload.get("query_explain", [])),
        len(payload.get("canonic_explains", [])),
        len(payload.get("disk_spill_statements", [])),
        len(payload.get("unstable_statements", [])),
    )

    if not payload.get("statements"):
        logger.warning("  Job %s no trae 'statements'. Snapshot vacio?", msg_id)


def main() -> None:
    logger.info("Detection Engine iniciado. Escuchando cola '%s'...", QUEUE_NAME)
    engine = get_connection_querylens_db()

    try:
        while True:
            job = read_next_job(engine)

            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            try:
                process_job(job)
                archive_job(engine, job["msg_id"])
                logger.info("Job %s procesado y archivado en a_%s.", job["msg_id"], QUEUE_NAME)
            except Exception:
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
