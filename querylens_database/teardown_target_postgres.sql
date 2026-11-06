-- Quita de un Postgres lo que puso setup_target_postgres.sql: el rol
-- querylens_monitor, el schema querylens, auto_explain, el log JSON y la
-- configuracion que el setup cambio. Lo que el servidor ya tenia antes de
-- QueryLens se queda como estaba. El paso a paso esta en PREPARAR_BASE.md.
--
--   psql -U postgres -d <base> -f teardown_target_postgres.sql
--
-- Como superusuario, en CADA base donde se corrio el setup. La configuracion
-- del servidor se revierte en la base que tiene el registro del setup
-- (querylens.setup_changes, la primera donde se corrio); en las demas solo se
-- borra lo de esa base. El rol se borra cuando ya no queda ninguna base con
-- permisos suyos. Es idempotente: correrlo de nuevo no rompe nada.
--
-- Al final dice si hace falta reiniciar Postgres (si se quito
-- pg_stat_statements de shared_preload_libraries o se apago
-- logging_collector).
\set ON_ERROR_STOP on
\set QUIET on
SET client_min_messages = warning;
SET pg_stat_statements.track = 'none';

-- ---------------------------------------------------------------------------
-- 1. Configuracion del servidor, desde el registro del setup.
-- ---------------------------------------------------------------------------
SELECT to_regclass('querylens.setup_changes') IS NOT NULL AS ql_has_record \gset
\if :ql_has_record

-- Valor que regira tras el proximo reinicio (igual que en el setup) y el que
-- quedaria sin postgresql.auto.conf (postgresql.conf o el de fabrica).
CREATE TEMP VIEW ql_next AS
SELECT s.name,
       coalesce((SELECT f.setting FROM pg_file_settings f
                 WHERE f.name = s.name ORDER BY f.seqno DESC LIMIT 1),
                s.setting) AS value,
       (SELECT f.setting FROM pg_file_settings f
        WHERE f.name = s.name AND f.sourcefile LIKE '%postgresql.auto.conf'
        ORDER BY f.seqno DESC LIMIT 1) AS auto_value,
       coalesce((SELECT f.setting FROM pg_file_settings f
                 WHERE f.name = s.name AND f.sourcefile NOT LIKE '%postgresql.auto.conf'
                 ORDER BY f.seqno DESC LIMIT 1),
                s.boot_val) AS base_value
FROM pg_settings s;

CREATE FUNCTION pg_temp.ql_list(value text) RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT coalesce(array_agg(trim(both ' "' FROM x)) FILTER (WHERE trim(both ' "' FROM x) <> ''), '{}')
    FROM unnest(string_to_array(value, ',')) AS x
$$;

-- Sin LOAD, ALTER SYSTEM no reconoce los parametros auto_explain.*. Puede
-- fallar si alguien quito la libreria: no es motivo para detenerse.
\set ON_ERROR_STOP off
LOAD 'auto_explain';
\set ON_ERROR_STOP on

-- Listas: se quita solo el elemento que agrego el setup. Si lo que queda es
-- lo mismo que dicen los demas archivos (y antes no habia nada en
-- postgresql.auto.conf), RESET; si no, se fija lo que queda.
SELECT CASE
           WHEN rest = '{}'
                OR (c.previous_value IS NULL AND rest = pg_temp.ql_list(n.base_value))
               THEN format('ALTER SYSTEM RESET %I', c.name)
           WHEN c.name = 'log_destination'
               THEN format('ALTER SYSTEM SET log_destination = %L', array_to_string(rest, ','))
           ELSE format('ALTER SYSTEM SET %I = %s', c.name,
                       (SELECT string_agg(quote_literal(x), ', ') FROM unnest(rest) AS x))
       END
FROM querylens.setup_changes c
JOIN ql_next n USING (name)
CROSS JOIN LATERAL (SELECT array_remove(pg_temp.ql_list(n.value), c.added_item) AS rest) r
WHERE c.added_item IS NOT NULL
  AND c.added_item = ANY(pg_temp.ql_list(n.value)) \gexec

-- Escalares: se vuelve al valor de antes, salvo que alguien lo haya cambiado
-- despues del setup (entonces se respeta). Se lee postgresql.auto.conf
-- directo: pg_stat_statements.* no figura en pg_settings mientras la
-- libreria no este cargada.
SELECT CASE WHEN c.previous_value IS NULL THEN format('ALTER SYSTEM RESET %I', c.name)
            ELSE format('ALTER SYSTEM SET %I = %L', c.name, c.previous_value) END
FROM querylens.setup_changes c
WHERE c.added_item IS NULL AND c.set_value IS NOT NULL
  AND (SELECT f.setting FROM pg_file_settings f
       WHERE f.name = c.name AND f.sourcefile LIKE '%postgresql.auto.conf'
       ORDER BY f.seqno DESC LIMIT 1) IS NOT DISTINCT FROM c.set_value \gexec

SELECT pg_reload_conf(), pg_sleep(1) \gset

-- La extension, solo si la creo el setup en esta base.
SELECT 'DROP EXTENSION IF EXISTS pg_stat_statements'
FROM querylens.setup_changes WHERE name = 'extension pg_stat_statements' \gexec

\endif

-- ---------------------------------------------------------------------------
-- 2. Lo de esta base: el schema querylens (funciones de lectura del log y el
--    registro) y los permisos del rol, incluidos los SELECT opcionales.
-- ---------------------------------------------------------------------------
DROP SCHEMA IF EXISTS querylens CASCADE;

-- Bases donde QueryLens sigue instalado (el rol aun tiene permisos sobre sus
-- objetos). DROP OWNED tambien revoca CONNECT en todas las bases: a estas se
-- les devuelve, para no cortar su monitoreo.
CREATE TEMP TABLE ql_keep AS
SELECT DISTINCT d.datname
FROM pg_shdepend s
JOIN pg_database d ON d.oid = s.dbid
WHERE s.refclassid = 'pg_authid'::regclass
  AND s.refobjid = (SELECT oid FROM pg_roles WHERE rolname = 'querylens_monitor')
  AND d.datname <> current_database();

SELECT 'DROP OWNED BY querylens_monitor'
WHERE EXISTS (SELECT FROM pg_roles WHERE rolname = 'querylens_monitor') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO querylens_monitor', datname) FROM ql_keep \gexec

-- ---------------------------------------------------------------------------
-- 3. El rol, si ya no queda ninguna base con QueryLens.
-- ---------------------------------------------------------------------------
SELECT coalesce(string_agg(datname, ', ' ORDER BY datname), '') AS ql_other_dbs,
       count(*) = 0 AS ql_drop_role
FROM ql_keep \gset
\if :ql_drop_role
    DROP ROLE IF EXISTS querylens_monitor;
\endif

-- ---------------------------------------------------------------------------
-- 4. Resultado.
-- ---------------------------------------------------------------------------
SELECT count(*) > 0 AS ql_needs_restart
FROM pg_settings
WHERE pending_restart \gset
\echo ''
\if :ql_drop_role
\else
    \echo 'Falta un paso: corra este script tambien en estas bases, donde'
    \echo 'querylens_monitor todavia tiene permisos:' :ql_other_dbs
\endif
\if :ql_needs_restart
    \echo 'Reinicie Postgres para terminar, por ejemplo'
    \echo '    sudo systemctl restart postgresql'
\elif :ql_drop_role
    \echo 'Listo: QueryLens ya no tiene nada en esta base.'
\endif
\if :ql_has_record
\else
    \echo '(Esta base no tenia el registro de cambios del servidor: se revierten'
    \echo 'al correr el script en la primera base donde se instalo QueryLens.)'
\endif
