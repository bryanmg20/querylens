-- Preparacion de una base Postgres para que QueryLens la monitoree (la base
-- del cliente, no la de QueryLens). La corre el DBA una vez, como
-- superusuario, en CADA base a monitorear (la extension y las funciones son
-- por base; el rol es de todo el servidor y se crea solo la primera vez):
--
--   psql -U postgres -d <base> -f setup_target.sql
--
-- Al final pide la contrasena del rol querylens_monitor (\password: no queda
-- en el historial ni en ps). Es la que se ingresa en "querylens add".
--
-- Es idempotente: correrlo de nuevo no rompe nada (y permite cambiar la
-- contrasena). Antes, en postgresql.conf (requiere reiniciar Postgres):
--
--   shared_preload_libraries = 'pg_stat_statements'
--
-- y, para el analisis de abrazos mortales y de planes reales, la
-- configuracion de auto_explain y del log JSON de
-- telemetry_pipeline/references/006_instalacion_auto_explain.md (Pasos 1-3).
-- Las funciones de su Paso 4 ya van aqui abajo.
\set ON_ERROR_STOP on

-- Rol de monitoreo: solo lectura de estadisticas y configuracion
-- (pg_monitor). No lee datos de las tablas.
SELECT 'CREATE ROLE querylens_monitor LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'querylens_monitor') \gexec
ALTER ROLE querylens_monitor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT pg_monitor TO querylens_monitor;
SELECT format('GRANT CONNECT ON DATABASE %I TO querylens_monitor', current_database()) \gexec

-- Estadisticas de consultas. Sin shared_preload_libraries la extension se
-- crea, pero la vista falla al leerla (QueryLens lo reporta en
-- "querylens health" como PG_STATEMENTS_NOT_PRELOADED).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Lectura del log JSON de auto_explain (mismo SQL que el Paso 4 de
-- telemetry_pipeline/references/006_instalacion_auto_explain.md y
-- ql_sandbox/init/auto_explain_postgres.sql: si cambia uno, cambian todos).
-- Sin auto_explain configurado las funciones existen pero no hay archivos
-- .json que leer.
CREATE SCHEMA IF NOT EXISTS querylens;
REVOKE ALL ON SCHEMA querylens FROM PUBLIC;

CREATE OR REPLACE FUNCTION querylens.log_files()
RETURNS TABLE (name text, size bigint, modification timestamptz)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT name, size, modification
    FROM pg_ls_logdir()
    WHERE name LIKE '%.json'
$$;

CREATE OR REPLACE FUNCTION querylens.read_log(file text, start_offset bigint, max_bytes bigint)
RETURNS bytea
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF file !~ '^[A-Za-z0-9_.-]+\.json$' OR position('..' IN file) > 0 THEN
        RAISE EXCEPTION 'querylens.read_log: archivo no permitido';
    END IF;
    IF start_offset < 0 OR max_bytes <= 0 OR max_bytes > 16777216 THEN
        RAISE EXCEPTION 'querylens.read_log: rango no permitido';
    END IF;
    RETURN pg_read_binary_file(
        current_setting('log_directory') || '/' || file, start_offset, max_bytes, true
    );
END
$$;

REVOKE ALL ON FUNCTION querylens.log_files() FROM PUBLIC;
REVOKE ALL ON FUNCTION querylens.read_log(text, bigint, bigint) FROM PUBLIC;
GRANT USAGE ON SCHEMA querylens TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.log_files() TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.read_log(text, bigint, bigint) TO querylens_monitor;

-- EXPLAIN (planes genericos de las consultas lentas) necesita SELECT sobre
-- las tablas, y con SELECT el rol podria leer los datos. Es decision del
-- dueno de la base: sin esto QueryLens funciona, pero sin los antipatrones
-- que dependen del plan. Para darlo, por schema:
--
--   GRANT USAGE ON SCHEMA <schema> TO querylens_monitor;
--   GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO querylens_monitor;
--   ALTER DEFAULT PRIVILEGES FOR ROLE <dueno> IN SCHEMA <schema>
--       GRANT SELECT ON TABLES TO querylens_monitor;

\echo 'Contrasena para querylens_monitor:'
\password querylens_monitor
