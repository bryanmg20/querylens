INDEXES_QUERY = """
SELECT
    t.table_schema                          AS schema_name,
    t.table_name                            AS table_name,
    t.index_name                            AS index_name,
    CAST(io.COUNT_READ AS SIGNED)           AS index_scans,
    NULL                                    AS last_index_scan,
    NULL                                    AS index_ref,
    CAST(st.index_length AS SIGNED)         AS index_size_bytes
FROM information_schema.statistics t
LEFT JOIN performance_schema.table_io_waits_summary_by_index_usage io
       ON io.object_schema = t.table_schema
      AND io.object_name   = t.table_name
      AND io.index_name    = t.index_name
LEFT JOIN information_schema.tables st
       ON st.table_schema = t.table_schema
      AND st.table_name   = t.table_name
WHERE t.table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
GROUP BY
    t.table_schema,
    t.table_name,
    t.index_name,
    io.COUNT_READ,
    st.index_length;
"""


TABLES_QUERY = """
SELECT
    t.table_schema                              AS schema_name,
    t.table_name                                AS table_name,
    CAST(io.COUNT_READ AS SIGNED)               AS seq_scans,
    CAST(
        COALESCE(idx_io.total_index_reads, 0)
    AS SIGNED)                                  AS idx_scans,
    CAST(t.table_rows AS SIGNED)                AS live_rows
FROM information_schema.tables t
LEFT JOIN performance_schema.table_io_waits_summary_by_table io
       ON io.object_schema = t.table_schema
      AND io.object_name   = t.table_name
LEFT JOIN (
    SELECT object_schema, object_name, SUM(COUNT_READ) AS total_index_reads
    FROM performance_schema.table_io_waits_summary_by_index_usage
    WHERE index_name IS NOT NULL
    GROUP BY object_schema, object_name
) idx_io
       ON idx_io.object_schema = t.table_schema
      AND idx_io.object_name   = t.table_name
WHERE t.table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
  AND t.table_type = 'BASE TABLE';
"""

STATEMENTS_QUERY = """
SELECT
    s.DIGEST                                                        AS query_id,
    s.DIGEST_TEXT                                                   AS query_text,
    CAST(s.COUNT_STAR AS SIGNED)                                    AS execution_count,
    CAST(s.SUM_ROWS_SENT AS SIGNED)                                 AS rows_returned,
    CAST(ROUND(s.SUM_ROWS_SENT / NULLIF(s.COUNT_STAR, 0), 0) AS DOUBLE)        AS avg_rows_per_call,
    CAST(ROUND(s.SUM_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS total_time_ms,
    CAST(ROUND(s.AVG_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS mean_time_ms,
    NULL                                                                        AS stddev_time_ms,
    CAST(ROUND(s.MIN_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS min_time_ms,
    CAST(ROUND(s.MAX_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS max_time_ms,
    NULL                                                                        AS coeff_of_variation,
    CAST(s.SUM_CREATED_TMP_DISK_TABLES AS SIGNED)                               AS disk_spill_indicator
FROM performance_schema.events_statements_summary_by_digest s
WHERE s.DIGEST_TEXT IS NOT NULL
  AND s.DIGEST_TEXT NOT LIKE '%performance_schema%'
  AND s.DIGEST_TEXT NOT LIKE '%information_schema%'
  AND s.DIGEST_TEXT NOT LIKE '%events_statements_summary%'
ORDER BY s.SUM_TIMER_WAIT DESC;
"""


LOCKS_QUERY = """
SELECT
    r.trx_mysql_thread_id   AS process_id,
    l.object_name           AS table_name,
    l.lock_mode             AS lock_mode,
    l.lock_status           AS is_granted
FROM performance_schema.data_locks l
LEFT JOIN information_schema.innodb_trx r
       ON CAST(r.trx_id AS CHAR) = l.engine_transaction_id
WHERE l.object_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys');
"""

ACTIVE_QUERIES_QUERY = """
SELECT
    t.processlist_id                AS process_id,
    s.SQL_TEXT                      AS query_text,
    s.DIGEST                        AS query_id,
    trx.trx_started                 AS transaction_start_time,
    (
        SELECT GROUP_CONCAT(t2.processlist_id)
        FROM performance_schema.data_lock_waits dlw
        JOIN performance_schema.threads t2
          ON t2.thread_id = dlw.BLOCKING_THREAD_ID
        WHERE dlw.REQUESTING_THREAD_ID = t.thread_id
    )                               AS blocking_pids
FROM performance_schema.threads t
LEFT JOIN performance_schema.events_statements_current s
       ON s.thread_id = t.thread_id
LEFT JOIN information_schema.innodb_trx trx
       ON trx.trx_mysql_thread_id = t.processlist_id
WHERE t.processlist_user IS NOT NULL
  AND t.processlist_user != (SELECT SUBSTRING_INDEX(CURRENT_USER(), '@', 1))
  AND t.processlist_command != 'Sleep';
"""


STATS_RESET_QUERY = """
SELECT
    FROM_UNIXTIME(
        UNIX_TIMESTAMP() - variable_value
    )                       AS stats_reset
FROM performance_schema.global_status
WHERE variable_name = 'Uptime';
"""

COLUMNS_QUERY = """
SELECT
    table_schema AS schema_name,
    table_name   AS table_name,
    column_name  AS column_name,
    data_type    AS data_type
FROM information_schema.columns
WHERE table_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys');
"""