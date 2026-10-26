-- Tabla de hallazgos detectados por detection_engine, en el schema public
-- de la base de QueryLens. evidencia es JSONB porque su forma cambia segun
-- el antipatron (cada detector trae sus propias claves).
CREATE TABLE IF NOT EXISTS public.hallazgos (
    id BIGSERIAL PRIMARY KEY,
    db_id TEXT,
    antipatron TEXT NOT NULL,
    severidad TEXT NOT NULL,
    query_id TEXT,
    table_name TEXT,
    evidencia JSONB NOT NULL,
    explicacion TEXT NOT NULL,
    recomendacion TEXT NOT NULL,
    detectado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- base donde ocurrio el hallazgo. db_id es el registro (en MySQL, todo el
-- servidor); en MySQL esto es el schema. NULL si el detector no lo sabe.
ALTER TABLE public.hallazgos ADD COLUMN IF NOT EXISTS database_name TEXT;
