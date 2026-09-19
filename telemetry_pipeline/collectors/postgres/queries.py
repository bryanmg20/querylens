INDEXES_QUERY = """
SELECT
    schemaname AS schema_name,
    relname AS table_name,
    indexrelname AS index_name,
    idx_scan AS index_scans,
    last_idx_scan AS last_index_scan,
    pg_get_indexdef(indexrelid) AS index_ref,
    pg_relation_size(indexrelid) AS index_size_bytes
FROM pg_stat_user_indexes;
"""


TABLES_QUERY = """
SELECT
    schemaname AS schema_name,
    relname AS table_name,
    seq_scan AS seq_scans,
    idx_scan AS idx_scans,
    n_live_tup AS live_rows
FROM pg_stat_user_tables;
"""


STATEMENTS_QUERY = """
SELECT
    userid AS userid,
    queryid AS query_id,
    query AS query_text,
    calls AS execution_count,
    rows AS rows_returned,
    ROUND(rows::numeric / NULLIF(calls, 0), 6) AS avg_rows_per_call,
    total_exec_time AS total_time_ms,
    mean_exec_time AS mean_time_ms,
    stddev_exec_time AS stddev_time_ms,
    min_exec_time AS min_time_ms,
    max_exec_time AS max_time_ms,
    stddev_exec_time / NULLIF(mean_exec_time, 0) AS coeff_of_variation,
    temp_blks_written AS disk_spill_indicator
FROM pg_stat_statements
WHERE userid != (SELECT oid FROM pg_roles WHERE rolname = session_user)
ORDER BY total_exec_time DESC;
"""


LOCKS_QUERY = """
SELECT
    l.pid AS process_id,
    c.relname AS table_name,
    l.mode AS lock_mode,
    l.granted AS is_granted
FROM pg_locks l
LEFT JOIN pg_class c ON c.oid = l.relation;
"""


ACTIVE_QUERIES_QUERY = """
SELECT
    pid                   AS process_id,
    query                 AS query_text,
    query_id              AS query_id,
    xact_start AS transaction_start_time,
    pg_blocking_pids(pid) AS blocking_pids  
FROM pg_stat_activity
WHERE usesysid != (SELECT oid FROM pg_roles WHERE rolname = session_user);
"""


STATS_RESET_QUERY = """
SELECT
    pg_postmaster_start_time()                         AS stats_reset
"""

COLUMNS_QUERY = """
SELECT
    table_schema AS schema_name,
    table_name   AS table_name,
    column_name  AS column_name,
    data_type    AS data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema');
"""

SCHEMA_RESOLVER_QUERY = """
WITH role_search_paths AS (
    SELECT
        r.oid AS user_id,
        r.rolname,
        sp.schema_entry,
        row_number() OVER (PARTITION BY r.oid ORDER BY sp.ordinality) AS priority
    FROM pg_roles r
    CROSS JOIN LATERAL unnest(
        string_to_array(
            COALESCE(
                (SELECT regexp_replace(val, 'search_path=', '')
                 FROM unnest(r.rolconfig) AS cfg(val)
                 WHERE cfg.val LIKE 'search_path=%'),
                '"$user", public'
            ),
            ','
        )
    ) WITH ORDINALITY AS sp(schema_entry)
),
normalized_paths AS (
    SELECT
        user_id,
        rolname,
        trim(replace(schema_entry, '"$user"', rolname)) AS schema_name,
        priority
    FROM role_search_paths
),
all_relations AS (
    SELECT
        n.nspname AS schema_name,
        c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'v', 'm', 'p')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
)
SELECT DISTINCT ON (usp.user_id)
    usp.user_id,
    usp.rolname      AS username,
    usp.schema_name  AS resolved_schema
FROM normalized_paths usp
JOIN all_relations t ON t.schema_name = usp.schema_name
ORDER BY usp.user_id, usp.priority;
"""