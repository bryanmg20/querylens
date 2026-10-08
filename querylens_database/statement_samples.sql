-- Estado por query_id que usan los detectores con ventana (AP-01 degradacion
-- respecto a linea base, AP-07 vertido a disco). Una sola fila por (db_id, query_id) que se actualiza en cada
-- snapshot, en vez de una fila por snapshot: el detector solo necesita los
-- ultimos contadores y las ultimas ventanas, asi que la tabla no crece con el
-- tiempo, solo con la cantidad de queries distintas.

-- Migracion unica: la version anterior guardaba una fila por snapshot (se
-- reconoce por la columna interval_calls). Solo tenia datos de prueba, asi que
-- se elimina y se crea con la forma nueva. Se puede quitar este bloque cuando
-- ninguna base tenga ya la version vieja.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'statement_samples'
          AND column_name = 'interval_calls'
    ) THEN
        DROP TABLE public.statement_samples;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.statement_samples (
    db_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    -- ultimo snapshot que actualizo la fila; sirve para ignorar reintentos de
    -- un job ya aplicado y para borrar queries que dejaron de ejecutarse
    captured_at TIMESTAMPTZ NOT NULL,
    -- desde cuando el motor acumula los contadores de esta query (stats_since
    -- en Postgres, FIRST_SEEN en MySQL): si cambia, los contadores se reiniciaron
    counters_epoch TIMESTAMPTZ,
    -- ultimos contadores acumulados, contra los que se resta el proximo snapshot
    execution_count BIGINT NOT NULL,
    total_time_ms DOUBLE PRECISION NOT NULL,
    -- ultimo disk_spill_indicator acumulado (bloques en Postgres, tablas
    -- temporales en disco en MySQL), para que AP-07 mire solo la ventana
    disk_spill_indicator BIGINT,
    -- latencia media de las ultimas ventanas validas (>= min_calls ejecuciones),
    -- de la mas vieja a la mas nueva, y la hora de cada una. La linea base usa
    -- solo las ultimas; el resto queda para la linea de tiempo de la interfaz
    window_means_ms DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    window_ends_at TIMESTAMPTZ[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (db_id, query_id)
);

-- columnas agregadas despues de crear la tabla: se suman sin perder la historia
ALTER TABLE public.statement_samples ADD COLUMN IF NOT EXISTS disk_spill_indicator BIGINT;

CREATE INDEX IF NOT EXISTS statement_samples_captured_at_idx
    ON public.statement_samples (captured_at);
