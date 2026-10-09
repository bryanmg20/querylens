-- Estado por query_id que usan los detectores con ventana (AP-01 degradacion
-- respecto a linea base, AP-07 vertido a disco, AP-08 error de estimacion de
-- cardinalidad). Una sola fila por (db_id, query_id) que se actualiza en cada
-- snapshot, en vez de una fila por snapshot: el detector solo necesita los
-- ultimos contadores y las ultimas ventanas, asi que la tabla no crece con el
-- tiempo, solo con la cantidad de queries distintas.

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
    -- ultimas filas devueltas acumuladas, para que AP-08 compare la estimacion
    -- del EXPLAIN con las filas por ejecucion de la ventana
    rows_returned BIGINT,
    -- latencia media de las ultimas ventanas validas (>= min_calls ejecuciones),
    -- de la mas vieja a la mas nueva, y la hora de cada una. La linea base usa
    -- solo las ultimas; el resto queda para la linea de tiempo de la interfaz
    window_means_ms DOUBLE PRECISION[] NOT NULL DEFAULT '{}',
    window_ends_at TIMESTAMPTZ[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (db_id, query_id)
);

CREATE INDEX IF NOT EXISTS statement_samples_captured_at_idx
    ON public.statement_samples (captured_at);
