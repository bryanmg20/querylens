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
    queryid AS query_id,
    query AS query_text,
    calls AS execution_count,
    rows AS rows_returned,
    (rows/calls) AS avg_rows_per_call,
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
