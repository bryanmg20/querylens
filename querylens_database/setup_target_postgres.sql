-- Prepara un Postgres para que QueryLens lo monitoree (la base del cliente,
-- no la de QueryLens). Un solo script con todo lo que necesita el pipeline;
-- el paso a paso esta en PREPARAR_BASE.md.
--
--   psql -U postgres -d <base> -f setup_target_postgres.sql
--
-- Como superusuario, en CADA base a monitorear: el rol y la configuracion
-- del servidor se aplican la primera vez, y en las demas bases solo se crean
-- la extension y las funciones. Es idempotente: lo que ya esta bien no se
-- toca, y correrlo de nuevo no rompe nada.
--
-- Al final:
--   * pide la contrasena de querylens_monitor (\password: no queda en el
--     historial ni en ps). Si el rol ya tenia una, pregunta antes;
--   * dice si hace falta reiniciar Postgres. Solo lo pide si cambio algo
--     que Postgres lee al arrancar (shared_preload_libraries,
--     logging_collector, pg_stat_statements.max, track_activity_query_size).
--
-- Requiere PostgreSQL 17 o superior autoadministrado (en RDS, Cloud SQL o
-- Azure no hay superusuario real ni acceso a los archivos de log).
\set ON_ERROR_STOP on
\set QUIET on
SET client_min_messages = warning;
-- Las consultas de este script no van a pg_stat_statements: usan objetos
-- temporales (ql_next, ql_add_library) y QueryLens intentaria explicarlas
-- en cada ciclo (EXPLAIN_FAILED). Solo esta sesion; la primera vez, con la
-- libreria sin cargar, queda como un parametro sin efecto.
SET pg_stat_statements.track = 'none';

SELECT current_setting('server_version_num')::int >= 170000 AS version_ok \gset
\if :version_ok
\else
    \echo 'QueryLens necesita PostgreSQL 17 o superior.'
    \quit
\endif

-- ---------------------------------------------------------------------------
-- 0. Registro de lo que cambia este script, para que
--    teardown_target_postgres.sql deshaga solo eso y deje lo que el servidor
--    ya tenia. Va en la primera base donde se corre (las demas no cambian la
--    configuracion del servidor). Una fila por cambio; si el script se repite,
--    se conserva la primera (el valor de antes de QueryLens).
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS querylens;
REVOKE ALL ON SCHEMA querylens FROM PUBLIC;
CREATE TABLE IF NOT EXISTS querylens.setup_changes (
    -- parametro, o 'extension pg_stat_statements'
    name text PRIMARY KEY,
    -- valor en postgresql.auto.conf antes del cambio (NULL = no estaba)
    previous_value text,
    -- listas (*_preload_libraries, log_destination): el elemento agregado
    added_item text,
    -- escalares: el valor que puso el script. Si despues alguien lo cambio,
    -- el teardown no lo toca.
    set_value text,
    changed_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON querylens.setup_changes FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 1. Rol de monitoreo: estadisticas y configuracion (pg_monitor, que incluye
--    pg_read_all_stats), sin acceso a los datos de las tablas.
-- ---------------------------------------------------------------------------
SELECT 'CREATE ROLE querylens_monitor LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'querylens_monitor') \gexec
ALTER ROLE querylens_monitor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT pg_monitor TO querylens_monitor;
SELECT format('GRANT CONNECT ON DATABASE %I TO querylens_monitor', current_database()) \gexec

-- ---------------------------------------------------------------------------
-- 2. Configuracion del servidor (ALTER SYSTEM: queda en postgresql.auto.conf).
--
-- Las listas se completan, no se reemplazan: se parte del valor que va a
-- regir tras el proximo reinicio (pg_file_settings, por si ya habia un
-- ALTER SYSTEM pendiente) o, si no hay, del actual. Cada cambio se arma con
-- un SELECT de dos columnas que \gexec ejecuta en orden: primero el registro
-- (0.) y despues el ALTER SYSTEM, que no puede ir dentro de un DO.
-- ---------------------------------------------------------------------------
-- Sin filtrar por error: un valor que espera reinicio figura con "setting
-- could not be applied", y es justo el que hay que conservar.
CREATE TEMP VIEW ql_next AS
SELECT s.name,
       coalesce((SELECT f.setting FROM pg_file_settings f
                 WHERE f.name = s.name
                 ORDER BY f.seqno DESC LIMIT 1),
                s.setting) AS value
FROM pg_settings s;

-- Elementos de una lista de configuracion ('a, "b"' -> {a,b}).
CREATE FUNCTION pg_temp.ql_list(value text) RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(array_agg(trim(both ' "' FROM x)) FILTER (WHERE trim(both ' "' FROM x) <> ''), '{}')
    FROM unnest(string_to_array(value, ',')) AS x
$$;

-- INSERT del registro de un cambio, con el valor que el parametro tenia en
-- postgresql.auto.conf.
CREATE FUNCTION pg_temp.ql_record(param text, added text, set_to text) RETURNS text
LANGUAGE sql AS $$
    SELECT format('INSERT INTO querylens.setup_changes (name, previous_value, added_item, set_value) '
                  'VALUES (%L, %L, %L, %L) ON CONFLICT DO NOTHING',
                  param,
                  (SELECT f.setting FROM pg_file_settings f
                   WHERE f.name = param AND f.sourcefile LIKE '%postgresql.auto.conf'
                   ORDER BY f.seqno DESC LIMIT 1),
                  added, set_to)
$$;

-- Agrega lib a la lista de libraries (ALTER SYSTEM SET x = 'a', 'b': con
-- 'a,b' quedaria como un solo nombre). NULL si ya estaba.
CREATE FUNCTION pg_temp.ql_add_library(param text, lib text) RETURNS text
LANGUAGE sql AS $$
    SELECT format('ALTER SYSTEM SET %I = %s', param,
                  string_agg(quote_literal(x), ', ' ORDER BY ord))
    FROM (SELECT trim(both ' "' FROM x) AS x, ord
          FROM ql_next, unnest(string_to_array(value, ',')) WITH ORDINALITY AS t(x, ord)
          WHERE name = param
          UNION ALL
          SELECT lib, 1000000) libs
    WHERE x <> ''
    HAVING NOT bool_or(x = lib AND ord < 1000000)
$$;

-- pg_stat_statements: se carga al arrancar (requiere reinicio).
SELECT pg_temp.ql_record('shared_preload_libraries', 'pg_stat_statements', NULL), stmt
FROM (SELECT pg_temp.ql_add_library('shared_preload_libraries', 'pg_stat_statements') AS stmt) c
WHERE stmt IS NOT NULL \gexec
-- auto_explain: en cada conexion nueva (basta recargar). Si ya se carga al
-- arrancar (shared_preload_libraries), no hace falta.
SELECT pg_temp.ql_record('session_preload_libraries', 'auto_explain', NULL), stmt
FROM (SELECT pg_temp.ql_add_library('session_preload_libraries', 'auto_explain') AS stmt
      FROM ql_next
      WHERE name = 'shared_preload_libraries'
        AND NOT 'auto_explain' = ANY(pg_temp.ql_list(value))) c
WHERE stmt IS NOT NULL \gexec

-- Sin LOAD, ALTER SYSTEM no reconoce los parametros auto_explain.*.
LOAD 'auto_explain';

-- Valores minimos: solo se suben si estan por debajo. Los de
-- pg_stat_statements se aceptan aunque la libreria aun no este cargada
-- (PG 17 permite ALTER SYSTEM sobre parametros que no reconoce).
SELECT pg_temp.ql_record(name, NULL, target::text),
       format('ALTER SYSTEM SET %I = %L', name, target)
FROM (VALUES ('pg_stat_statements.max', 10000),
             ('track_activity_query_size', 4096)) AS t(name, target)
LEFT JOIN ql_next USING (name)
WHERE coalesce(nullif(regexp_replace(value, '\D', '', 'g'), '')::bigint, 0) < target \gexec

-- Valores fijos que necesita QueryLens. track = all: el banco de pruebas del
-- detector se calibro asi (sentencias dentro de funciones incluidas).
SELECT pg_temp.ql_record(name, NULL, target),
       format('ALTER SYSTEM SET %I = %L', name, target)
FROM (VALUES ('pg_stat_statements.track', 'all'),
             ('track_counts', 'on'),
             ('auto_explain.log_min_duration', '50ms'),
             ('auto_explain.log_analyze', 'off'),
             ('auto_explain.log_format', 'json'),
             ('logging_collector', 'on')) AS t(name, target)
LEFT JOIN ql_next USING (name)
WHERE value IS DISTINCT FROM target
  -- 50ms se lee como 50 en pg_settings (unidad ms)
  AND NOT (name = 'auto_explain.log_min_duration' AND value = '50') \gexec

-- Log JSON: se agrega jsonlog a los destinos que ya tenga.
SELECT pg_temp.ql_record('log_destination', 'jsonlog', NULL),
       format('ALTER SYSTEM SET log_destination = %L', value || ',jsonlog')
FROM ql_next
WHERE name = 'log_destination' AND NOT 'jsonlog' = ANY(pg_temp.ql_list(value)) \gexec

-- Rotacion con limpieza (un archivo por dia de la semana que se vacia al
-- reutilizarse): solo si el servidor no escribia archivos de log. Si ya los
-- escribia, se respeta su politica de nombres y rotacion.
SELECT pg_temp.ql_record(name, NULL, target),
       format('ALTER SYSTEM SET %I = %L', name, target)
FROM (VALUES ('log_filename', 'postgresql-%a.log'),
             ('log_truncate_on_rotation', 'on'),
             ('log_rotation_age', '1d')) AS t(name, target)
LEFT JOIN ql_next USING (name)
WHERE current_setting('logging_collector') = 'off'
  AND value IS DISTINCT FROM target \gexec

-- Informes de abrazo mortal: el log tiene que traer el DETAIL de cada uno.
SELECT pg_temp.ql_record(name, NULL, target),
       format('ALTER SYSTEM SET %I = %L', name, target)
FROM (VALUES ('log_error_verbosity', 'default', ARRAY['terse']),
             ('log_min_messages', 'warning', ARRAY['log', 'fatal', 'panic'])) AS t(name, target, bad)
JOIN ql_next USING (name)
WHERE value = ANY(bad) \gexec

-- Esperas de fila: con log_lock_waits, una sesion que espera mas que
-- deadlock_timeout escribe que fila espera ("still waiting"). Es lo que
-- distingue CT-01 de CT-02 cuando la sentencia manda sus valores aparte ($1).
SELECT pg_temp.ql_record(name, NULL, target),
       format('ALTER SYSTEM SET %I = %L', name, target)
FROM (VALUES ('log_lock_waits', 'on')) AS t(name, target)
JOIN ql_next USING (name)
WHERE value <> target \gexec

-- La recarga llega a esta sesion por senal: la pausa le da tiempo antes de
-- leer pg_settings en el chequeo final.
SELECT pg_reload_conf(), pg_sleep(1) \gset

-- ---------------------------------------------------------------------------
-- 3. Extension pg_stat_statements (por base). QueryLens necesita la 1.11 o
--    superior: UPDATE la sube si la base la tenia de una version anterior.
-- ---------------------------------------------------------------------------
SELECT 'INSERT INTO querylens.setup_changes (name) VALUES (''extension pg_stat_statements'') ON CONFLICT DO NOTHING',
       'CREATE EXTENSION pg_stat_statements'
WHERE NOT EXISTS (SELECT FROM pg_extension WHERE extname = 'pg_stat_statements') \gexec
ALTER EXTENSION pg_stat_statements UPDATE;

-- ---------------------------------------------------------------------------
-- 4. Lectura del log JSON (por base, en el schema querylens de 0.). Mismo SQL
--    que el Paso 4 de telemetry_pipeline/references/006_instalacion_auto_explain.md
--    y ql_sandbox/init/auto_explain_postgres.sql: si cambia uno, cambian todos.
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 5. Contrasena y resultado.
-- ---------------------------------------------------------------------------
-- Rol sin contrasena (recien creado): se pide. Si ya tiene, se pregunta.
-- -v ql_skip_password=1 lo salta (instalaciones automatizadas que la fijan
-- por otro lado).
SELECT rolpassword IS NULL AS ql_no_password
FROM pg_authid WHERE rolname = 'querylens_monitor' \gset
\echo ''
\if :{?ql_skip_password}
\elif :ql_no_password
    \echo 'Contrasena para querylens_monitor (la que se ingresa en querylens add):'
    \password querylens_monitor
\else
    \prompt 'querylens_monitor ya tiene contrasena. Cambiarla? [s/N]: ' ql_change
    SELECT :'ql_change' ILIKE 's%' AS ql_change \gset
    \if :ql_change
        \password querylens_monitor
    \endif
\endif

-- Chequeo final contra lo que esta vigente (pg_settings de esta sesion, ya
-- recargada): que falta y por que. Lo que viene de la linea de comandos de
-- Postgres (-c, systemd, contenedor) gana sobre ALTER SYSTEM y no se
-- arregla reiniciando; lo demas que falte es de arranque (context
-- postmaster) y se aplica al reiniciar.
CREATE TEMP VIEW ql_missing AS
SELECT name, source, context
FROM pg_settings
WHERE (name = 'shared_preload_libraries' AND NOT 'pg_stat_statements' = ANY(pg_temp.ql_list(setting)))
   OR (name = 'session_preload_libraries' AND NOT 'auto_explain' = ANY(pg_temp.ql_list(setting))
       AND NOT 'auto_explain' = ANY(pg_temp.ql_list(current_setting('shared_preload_libraries'))))
   -- setting va en unidades base (current_setting daria '4kB')
   OR (name = 'pg_stat_statements.max' AND setting::int < 10000)
   OR (name = 'track_activity_query_size' AND setting::int < 4096)
   OR (name = 'pg_stat_statements.track' AND setting <> 'all')
   OR (name = 'track_counts' AND setting <> 'on')
   OR (name = 'auto_explain.log_format' AND setting <> 'json')
   OR (name = 'auto_explain.log_min_duration' AND setting <> '50')
   OR (name = 'logging_collector' AND setting <> 'on')
   OR (name = 'log_destination' AND NOT 'jsonlog' = ANY(pg_temp.ql_list(setting)))
   OR (name = 'log_error_verbosity' AND setting = 'terse')
   OR (name = 'log_min_messages' AND setting IN ('log', 'fatal', 'panic'))
   OR (name = 'log_lock_waits' AND setting <> 'on');

SELECT count(*) FILTER (WHERE source = 'command line') > 0 AS ql_overridden,
       string_agg(name, ', ' ORDER BY name) FILTER (WHERE source = 'command line') AS ql_overridden_names,
       count(*) FILTER (WHERE source <> 'command line' AND context = 'postmaster') > 0 AS ql_needs_restart
FROM ql_missing \gset
\echo ''
\if :ql_overridden
    \echo 'Falta un paso: estos parametros vienen de la linea de comandos de'
    \echo 'Postgres y ganan sobre lo que configuro este script:' :ql_overridden_names
    \echo 'Cambielos alli con los valores de PREPARAR_BASE.md y reinicie Postgres.'
\elif :ql_needs_restart
    \echo 'Falta un paso: reinicie Postgres para terminar, por ejemplo'
    \echo '    sudo systemctl restart postgresql'
    \echo 'Despues del reinicio no hace falta volver a correr este script.'
\else
    \echo 'Listo: la base ya tiene todo lo que necesita QueryLens.'
\endif
