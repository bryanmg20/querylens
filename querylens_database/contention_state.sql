-- Estado del modulo de contencion (detection_engine/deadlocks): las raices de
-- cascada de bloqueo (CT-05) que cumplieron la regla en la ultima muestra y en
-- la anterior, y los abrazos mortales ya procesados (CT-01). Una sola fila por
-- db_id que se reemplaza en cada snapshot.

CREATE TABLE IF NOT EXISTS public.contention_state (
    db_id TEXT PRIMARY KEY,
    -- ultima muestra procesada; sirve para ignorar snapshots que llegan fuera
    -- de orden y para reconocer el reintento de un job ya aplicado
    captured_at TIMESTAMPTZ NOT NULL,
    -- [{pid, transaction_start_time, first_seen_at, reported}]
    roots JSONB NOT NULL DEFAULT '[]',
    -- la muestra anterior a captured_at: contra ella se recalcula un reintento
    prev_captured_at TIMESTAMPTZ,
    prev_roots JSONB NOT NULL DEFAULT '[]'
);

-- Abrazos mortales ya vistos y conteo por firma (DeadlockMemory):
-- {"seen": {"detected_at|pid de la victima": firma}, "signatures": {firma: {count, first_seen_at, last_seen_at}}}.
-- ADD COLUMN IF NOT EXISTS: la tabla ya existe en las bases creadas con CT-05.
ALTER TABLE public.contention_state ADD COLUMN IF NOT EXISTS deadlocks JSONB NOT NULL DEFAULT '{}';
-- lo mismo en la muestra anterior a captured_at, para recalcular un reintento
ALTER TABLE public.contention_state ADD COLUMN IF NOT EXISTS prev_deadlocks JSONB NOT NULL DEFAULT '{}';

-- Abrazos mortales que QueryLens vio: uno por informe unico (la clave es la
-- misma que la de la memoria, detected_at|pid de la victima), tenga o no
-- hallazgo. Sin detalle (complete = false) o sin patron, antipatron es NULL.
-- cli/share/report.sql los compara con el contador del motor
-- (pipeline_health.cycle_samples). CT-05 no es un abrazo mortal: no entra.
CREATE TABLE IF NOT EXISTS public.deadlock_events (
    db_id       TEXT NOT NULL,
    report_key  TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    -- postgres_log | innodb_status | mysql_error_log
    source      TEXT NOT NULL,
    complete    BOOLEAN NOT NULL,
    antipatron  TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (db_id, report_key)
);

CREATE INDEX IF NOT EXISTS ix_deadlock_events_detected
    ON public.deadlock_events (db_id, detected_at);
