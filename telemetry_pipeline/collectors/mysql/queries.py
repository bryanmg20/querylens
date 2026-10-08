INDEXES_QUERY = """
SELECT
    t.table_schema                          AS schema_name,
    t.table_name                            AS table_name,
    t.index_name                            AS index_name,
    CAST(io.COUNT_READ AS SIGNED)           AS index_scans,
    NULL                                    AS last_index_scan,
    CONCAT(
        'CREATE ',
        IF(MAX(t.NON_UNIQUE) = 0, 'UNIQUE ', ''),
        'INDEX ', t.index_name,
        ' ON ', t.table_schema, '.', t.table_name,
        ' USING ', LOWER(MAX(t.index_type)),
        ' (',
        GROUP_CONCAT(t.column_name ORDER BY t.seq_in_index SEPARATOR ', '),
        ')'
    )                                       AS index_ref,
    # index_size_bytes es a nivel de TABLA, no de indice:
    # information_schema.tables.index_length agrega el almacenamiento de todos
    # los indices de la tabla, asi que se repite identico en cada fila de la
    # misma tabla (verificado en vivo: PRIMARY y k_1 con el mismo 212992).
    # No es el tamaño individual del indice; para eso no hay fuente directa.
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
    CONCAT(s.schema_name, '/', s.DIGEST)                                AS query_id,
    s.DIGEST_TEXT                                                   AS query_text,
    s.QUERY_SAMPLE_TEXT                                             AS query_sample_text,
    s.schema_name                                                     AS schema_name,
    s.schema_name                                                     AS database_name,
    CAST(s.COUNT_STAR AS SIGNED)                                    AS execution_count,
    CAST(s.SUM_ROWS_SENT AS SIGNED)                                 AS rows_returned,
    CAST(ROUND(s.SUM_ROWS_SENT / NULLIF(s.COUNT_STAR, 0), 6) AS DOUBLE)        AS avg_rows_per_call,
    CAST(ROUND(s.SUM_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS total_time_ms,
    CAST(ROUND(s.AVG_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS mean_time_ms,
    NULL                                                                        AS stddev_time_ms,
    CAST(ROUND(s.MIN_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS min_time_ms,
    CAST(ROUND(s.MAX_TIMER_WAIT / 1000000000.0, 6) AS DOUBLE)                  AS max_time_ms,
    NULL                                                                        AS coeff_of_variation,
    CAST(s.SUM_CREATED_TMP_DISK_TABLES AS SIGNED)                               AS disk_spill_indicator,
    FIRST_SEEN AS counters_epoch
FROM performance_schema.events_statements_summary_by_digest s
WHERE s.DIGEST_TEXT IS NOT NULL
  AND s.DIGEST_TEXT NOT LIKE '%performance_schema%'
  AND s.DIGEST_TEXT NOT LIKE '%information_schema%'
  AND s.DIGEST_TEXT NOT LIKE '%events_statements_summary%'
  AND s.DIGEST_TEXT NOT LIKE 'SELECT @@%'
  AND s.DIGEST_TEXT NOT LIKE 'SELECT `VERSION`%'
  AND s.DIGEST_TEXT NOT LIKE 'SET @@%'
  AND s.DIGEST_TEXT NOT LIKE 'SHOW %'
  -- Autobservacion: events_statements_summary_by_digest agrupa por digest y
  -- no expone usuario, asi que no hay filtro por rol como el de Postgres. La
  -- unica defensa es reconocer la forma de lo que emite el propio pipeline.
  -- Sin esto, el EXPLAIN del ExplainStage se acumula entre corridas y termina
  -- contaminando high_impact_statements con planes del pipeline.
  AND s.DIGEST_TEXT NOT LIKE 'SET NAMES%'
  AND s.DIGEST_TEXT NOT LIKE 'SET `AUTOCOMMIT`%'
  AND s.DIGEST_TEXT NOT LIKE 'USE %'
  AND s.DIGEST_TEXT NOT LIKE 'ROLLBACK%'
  AND s.DIGEST_TEXT NOT LIKE 'SELECT SCHEMA%'
  AND s.DIGEST_TEXT NOT LIKE 'SELECT ?%'
  AND s.DIGEST_TEXT NOT LIKE 'EXPLAIN FORMAT = JSON%'
  -- BEGIN/COMMIT son sentencias de control, no telemetria: nunca llegan a ser
  -- candidatos a explicar, asi que excluirlas no_costa un candidato. Se
  -- excluyen para que el filtro y PIPELINE_FINGERPRINT digan lo mismo.
  AND s.DIGEST_TEXT NOT LIKE 'BEGIN%'
  AND s.DIGEST_TEXT NOT LIKE 'COMMIT%'
ORDER BY s.SUM_TIMER_WAIT DESC;
"""


LOCKS_QUERY = """
SELECT
    t.processlist_id        AS process_id,
    l.object_schema         AS database_name,
    l.object_name           AS table_name,
    l.lock_mode             AS lock_mode,
    l.lock_status           AS is_granted
FROM performance_schema.data_locks l
# El hilo sale directo de data_locks.thread_id, no de information_schema.innodb_trx;
# innodb_trx es un cache (refresco ~100 ms) que se materializa antes de leer
# data_locks, asi que las transacciones nuevas quedaban sin par y process_id
# llegaba NULL -> LockRow rechazaba el snapshot completo. Verificado en vivo con
# carga FOR UPDATE, 48/60 lecturas con NULL via innodb_trx, 0/60 via thread_id.
JOIN performance_schema.threads t
  ON t.thread_id = l.thread_id
WHERE l.object_schema NOT IN ('mysql', 'performance_schema', 'information_schema', 'sys')
  # Hilos de fondo (sin processlist_id) no tienen proceso de cliente que reportar
  # y LockRow.process_id es int obligatorio.
  AND t.processlist_id IS NOT NULL
  AND (t.processlist_user IS NULL OR t.processlist_user != (SELECT SUBSTRING_INDEX(CURRENT_USER(), '@', 1)));
"""

ACTIVE_QUERIES_QUERY = """
SELECT
    t.processlist_id                AS process_id,
    s.SQL_TEXT                      AS query_text,
    CONCAT(s.CURRENT_SCHEMA, '/', s.DIGEST) AS query_id,
    s.CURRENT_SCHEMA                AS database_name,
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


SERVER_START_QUERY = """
SELECT
    FROM_UNIXTIME(
        UNIX_TIMESTAMP() - variable_value
    )                       AS server_start_time
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