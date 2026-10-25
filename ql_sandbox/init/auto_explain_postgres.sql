-- Funciones que dejan a querylens_monitor leer el log JSON de auto_explain.
--
-- Mismo SQL que el Paso 4 de telemetry_pipeline/references/006_instalacion_auto_explain.md
-- y que querylens_database/setup_target_postgres.sql (si cambia uno, cambian
-- todos). La configuración del servidor (auto_explain,
-- jsonlog, rotación) va en los -c del servicio postgres de docker-compose.yml.
--
-- Lo ejecuta el servicio postgres_setup en cada `docker compose up`, también
-- con un volumen que ya existía: por eso todo es idempotente.

CREATE SCHEMA IF NOT EXISTS querylens;
REVOKE ALL ON SCHEMA querylens FROM PUBLIC;

-- Lista los archivos .json del directorio de logs (nombre, tamaño, última modificación).
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

-- Devuelve hasta max_bytes del archivo indicado, desde start_offset.
-- Solo acepta un nombre de archivo .json, sin rutas: no puede salir del
-- directorio de logs. Devuelve bytea: un corte en medio de un caracter UTF-8
-- no rompe la lectura.
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

-- Las funciones nacen ejecutables por PUBLIC: se quita y se da solo al monitor.
REVOKE ALL ON FUNCTION querylens.log_files() FROM PUBLIC;
REVOKE ALL ON FUNCTION querylens.read_log(text, bigint, bigint) FROM PUBLIC;

GRANT USAGE ON SCHEMA querylens TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.log_files() TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.read_log(text, bigint, bigint) TO querylens_monitor;
