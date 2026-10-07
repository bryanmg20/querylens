-- Serie de tiempo por query_id para el antipatron de degradacion respecto a
-- linea base. Cada snapshot procesado deja una fila por statement con los
-- contadores acumulados tal como vienen del motor (execution_count,
-- total_time_ms) y el intervalo contra el sample anterior ya calculado
-- (interval_calls, interval_mean_ms), para no recalcular la serie en cada job.
CREATE TABLE IF NOT EXISTS public.statement_samples (
    id BIGSERIAL PRIMARY KEY,
    db_id TEXT,
    query_id TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    -- momento del ultimo reset/arranque del motor segun el snapshot: si cambia
    -- entre dos samples, los contadores se reiniciaron y el delta no es valido
    stats_reset TEXT,
    execution_count BIGINT NOT NULL,
    total_time_ms DOUBLE PRECISION NOT NULL,
    -- NULL cuando no hay sample anterior o hubo reset (delta negativo o
    -- stats_reset distinto): ese intervalo no se puede medir
    interval_calls BIGINT,
    interval_mean_ms DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS statement_samples_lookup_idx
    ON public.statement_samples (db_id, query_id, captured_at DESC);
