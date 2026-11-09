-- Hallazgos del detection_engine, en el schema public de la base de QueryLens.
--
-- Un problema que sigue presente se detecta en cada ventana (cada 10 s). Para
-- no repetir en cada deteccion la evidencia, la explicacion y la
-- recomendacion, se guardan una vez por problema, y cada deteccion solo anota
-- cuando ocurrio y con que severidad:
--
--   problemas    una fila por problema (db_id + antipatron + finding_key), con
--                la evidencia, la explicacion y la recomendacion de la ultima
--                deteccion
--   detecciones  una fila por deteccion: problema, hora y severidad
--   hallazgos    vista con las columnas de la tabla anterior, una fila por
--                deteccion. La leen los scripts de ql_sandbox; la evidencia es
--                la de la ultima deteccion del problema
--
-- finding_key identifica el problema dentro de su antipatron (lo arma
-- detection_engine/storage.py, funcion finding_key). evidencia es JSONB porque
-- su forma cambia segun el antipatron.
--
-- Lo aplican db-roles (service_roles.sql, como ql_detector) en cada up y el
-- detector al arrancar (storage.ensure_hallazgos_table), asi que todo es
-- idempotente. El detector lo ejecuta con SQLAlchemy: en este archivo no puede
-- haber dos puntos pegados a una palabra (los tomaria como parametro) ni el
-- signo de porcentaje.

-- Volumenes anteriores: public.hallazgos era una tabla con una fila completa
-- por deteccion. Se renombra (no se borra) para que la vista ocupe su nombre;
-- sus filas no se copian a las tablas nuevas.
DO $$
DECLARE
    nuevo_nombre TEXT := 'hallazgos_v1';
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'hallazgos' AND c.relkind = 'r'
    ) THEN
        IF to_regclass('public.hallazgos_v1') IS NOT NULL THEN
            nuevo_nombre := 'hallazgos_v1_' || to_char(clock_timestamp(), 'YYYYMMDD_HH24MISS');
        END IF;
        EXECUTE 'ALTER TABLE public.hallazgos RENAME TO ' || quote_ident(nuevo_nombre);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.problemas (
    id BIGSERIAL PRIMARY KEY,
    -- registro de la base (en MySQL, todo el servidor); NULL si el snapshot no
    -- lo trae
    db_id TEXT,
    antipatron TEXT NOT NULL,
    finding_key TEXT NOT NULL,
    -- de la ultima deteccion; la de cada una queda en detecciones
    severidad TEXT NOT NULL,
    -- de la ultima deteccion: en AP-05 la consulta asociada puede cambiar
    query_id TEXT,
    table_name TEXT,
    -- base donde ocurrio: en MySQL el schema. NULL si el detector no lo sabe
    database_name TEXT,
    evidencia JSONB NOT NULL,
    explicacion TEXT NOT NULL,
    recomendacion TEXT NOT NULL,
    primera_deteccion TIMESTAMPTZ NOT NULL DEFAULT now(),
    ultima_deteccion TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NULLS NOT DISTINCT: un snapshot sin db_id tambien tiene que caer en la misma
-- fila (el upsert de storage.save_findings usa este indice).
CREATE UNIQUE INDEX IF NOT EXISTS problemas_clave_uq
    ON public.problemas (db_id, antipatron, finding_key) NULLS NOT DISTINCT;

CREATE TABLE IF NOT EXISTS public.detecciones (
    id BIGSERIAL PRIMARY KEY,
    problema_id BIGINT NOT NULL REFERENCES public.problemas (id) ON DELETE CASCADE,
    -- la de esta deteccion: un mismo problema puede pasar de media a alta
    severidad TEXT NOT NULL,
    -- hora de insercion: el detector procesa la cola en diferido, asi que
    -- puede ir unos segundos detras del snapshot
    detectado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conteos por problema y rangos de fechas (reporte, pagina de diagnosticos).
CREATE INDEX IF NOT EXISTS detecciones_problema_idx
    ON public.detecciones (problema_id, detectado_en);

CREATE OR REPLACE VIEW public.hallazgos AS
SELECT d.id, p.db_id, p.antipatron, d.severidad, p.query_id, p.table_name,
       p.evidencia, p.explicacion, p.recomendacion, d.detectado_en,
       p.database_name, p.finding_key, d.problema_id
FROM public.detecciones d
JOIN public.problemas p ON p.id = d.problema_id;
