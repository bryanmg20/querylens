CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'querylens_monitor'
    ) THEN
        CREATE ROLE querylens_monitor LOGIN PASSWORD 'monitor_pass';
    END IF;

    IF NOT EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'app_user'
    ) THEN
        CREATE ROLE app_user LOGIN PASSWORD 'app_pass';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE ql_demo TO querylens_monitor;
GRANT pg_read_all_stats TO querylens_monitor;

GRANT CONNECT ON DATABASE ql_demo TO app_user;
GRANT USAGE, CREATE ON SCHEMA public TO app_user;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_user;