-- Un rol por servicio, con solo los permisos que usa (CODE_REVIEW.md B-10).
-- QUERYLENS_USER queda como superusuario de administracion (initdb, scripts
-- de migracion, psql manual): ningun servicio se conecta con el.
--
-- Lo ejecuta apply_service_roles.sh (servicio db-roles de docker-compose, en
-- cada "up"), que pasa las contrasenas como variables de psql. Es
-- idempotente: crea lo que falta y vuelve a fijar las contrasenas del .env.
-- Para aplicarlo sin reiniciar el stack: docker compose up db-roles

-- Columnas nuevas de registered_databases en volumenes que ya existian (la
-- ventana de recoleccion): los GRANT por columna de ql_cli las necesitan.
\i /querylens_database/registered_databases.sql

-- Roles: crear los que falten y fijar atributos y contrasena.
SELECT format('CREATE ROLE %I LOGIN', r)
FROM unnest(ARRAY['ql_auth', 'ql_api', 'ql_pipeline', 'ql_detector', 'ql_cli']) AS r
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) \gexec

ALTER ROLE ql_auth     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'auth_pw';
ALTER ROLE ql_api      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'api_pw';
ALTER ROLE ql_pipeline LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'pipeline_pw';
ALTER ROLE ql_detector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'detector_pw';
ALTER ROLE ql_cli      LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'cli_pw';

-- Solo estos roles se conectan a la base (PUBLIC tiene CONNECT y TEMP por
-- defecto).
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"dbname" TO ql_auth, ql_api, ql_pipeline, ql_detector, ql_cli;

-- registered_databases: el auth-service registra bases y su health monitor
-- actualiza is_active; pipeline (credenciales cifradas) y api-rest solo leen.
GRANT SELECT, INSERT, UPDATE ON public.registered_databases TO ql_auth;
GRANT SELECT ON public.registered_databases TO ql_pipeline, ql_api;

-- CLI y scheduler (cli/querylens.sh, misma imagen y mismo rol): registran por
-- el auth-service, asi que aqui solo leen las columnas sin credenciales y
-- cambian el monitoreo y la ventana de recoleccion.
GRANT SELECT (database_identifier, connection_name, engine, database_name,
              is_active, monitoring_enabled, collect_from, collect_until,
              collect_started_at, report_written_at, created_at)
    ON public.registered_databases TO ql_cli;
GRANT UPDATE (monitoring_enabled, collect_from, collect_until,
              collect_started_at, report_written_at, updated_at)
    ON public.registered_databases TO ql_cli;

-- pipeline_health: lo escribe solo el pipeline (upsert, resolver, purgar) y
-- lo lee la api-rest.
GRANT USAGE ON SCHEMA pipeline_health TO ql_pipeline, ql_api, ql_cli;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA pipeline_health TO ql_pipeline;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA pipeline_health TO ql_pipeline;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline_health TO ql_api, ql_cli;

-- Cola analyze_job: el pipeline encola (pgmq.send hace INSERT ... RETURNING)
-- y el detector lee (UPDATE del vt) y archiva (DELETE de la cola + INSERT en
-- el archivo). Las funciones de pgmq corren con los permisos de quien llama.
GRANT USAGE ON SCHEMA pgmq TO ql_pipeline, ql_detector;
GRANT SELECT, INSERT ON pgmq.q_analyze_job TO ql_pipeline;
GRANT SELECT, UPDATE, DELETE ON pgmq.q_analyze_job TO ql_detector;
GRANT SELECT, INSERT ON pgmq.a_analyze_job TO ql_detector;

-- Tablas del detection_engine: las crea y migra el mismo al arrancar
-- (CREATE TABLE/INDEX IF NOT EXISTS, ADD COLUMN IF NOT EXISTS), lo que exige
-- CREATE sobre el schema aunque ya existan y ser su dueno.
GRANT CREATE ON SCHEMA public TO ql_detector;
ALTER TABLE IF EXISTS public.hallazgos OWNER TO ql_detector;
ALTER TABLE IF EXISTS public.statement_samples OWNER TO ql_detector;
ALTER TABLE IF EXISTS public.contention_state OWNER TO ql_detector;

-- Crearlas aqui (como ql_detector) si faltan, para poder dar el SELECT de la
-- api-rest antes del primer arranque del detector.
SET ROLE ql_detector;
\i /querylens_database/hallazgos.sql
\i /querylens_database/statement_samples.sql
\i /querylens_database/contention_state.sql
RESET ROLE;

GRANT SELECT ON public.hallazgos TO ql_api, ql_cli;
