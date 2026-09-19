-- ============================================================
-- QUERYLENS — PostgreSQL initialization script
-- ============================================================

-- -----------------------------------------------
-- Extensiones necesarias
-- -----------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- -----------------------------------------------
-- Crear roles
-- -----------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'querylens_monitor'
    ) THEN
        CREATE ROLE querylens_monitor LOGIN PASSWORD 'monitor_pass';
    END IF;

    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'ql_user'
    ) THEN
        CREATE ROLE ql_user LOGIN PASSWORD 'ql_pass';
    END IF;
END
$$;

-- -----------------------------------------------
-- Permisos querylens_monitor
-- -----------------------------------------------
GRANT CONNECT ON DATABASE ql_demo TO querylens_monitor;
GRANT pg_read_all_stats TO querylens_monitor;

-- Acceso a todos los schemas existentes
DO $$
DECLARE
    s text;
BEGIN
    FOR s IN 
        SELECT nspname 
        FROM pg_namespace 
        WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        AND nspname NOT LIKE 'pg_%'
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO querylens_monitor', s);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO querylens_monitor', s);
    END LOOP;
END
$$;

-- Acceso a schemas que se creen en el futuro
ALTER DEFAULT PRIVILEGES GRANT USAGE ON SCHEMAS TO querylens_monitor;
ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO querylens_monitor;

-- -----------------------------------------------
-- Permisos ql_user
-- -----------------------------------------------
GRANT CONNECT ON DATABASE ql_demo TO ql_user;
GRANT USAGE, CREATE ON SCHEMA public TO ql_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ql_user;

-- Para que querylens_monitor vea las tablas que cree ql_user en el futuro
ALTER DEFAULT PRIVILEGES FOR ROLE ql_user IN SCHEMA public
GRANT SELECT ON TABLES TO querylens_monitor;