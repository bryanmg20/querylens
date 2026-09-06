CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

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

GRANT CONNECT ON DATABASE ql_demo TO querylens_monitor;
GRANT pg_read_all_stats TO querylens_monitor;
GRANT USAGE ON SCHEMA public TO querylens_monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO querylens_monitor;

GRANT CONNECT ON DATABASE ql_demo TO ql_user;
GRANT USAGE, CREATE ON SCHEMA public TO ql_user;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO ql_user;

ALTER DEFAULT PRIVILEGES FOR ROLE ql_user IN SCHEMA public
GRANT SELECT ON TABLES TO querylens_monitor;