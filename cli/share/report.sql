-- Reporte de hallazgos de una base en [desde, hasta), como un solo JSON que
-- querylens.sh convierte a md, csv o json con jq.
--
-- Variables de psql (querylens.sh las pasa con -v, nunca interpoladas):
--   :'id'        database_identifier (= hallazgos.db_id)
--   :'from'      inicio del rango (timestamptz)
--   :'until'     fin del rango, excluido (timestamptz)
--   :'grace'     segundos despues de :'until' que tambien cuentan: detectado_en
--                es la hora de insercion, y el detector procesa en diferido
--                por la cola los snapshots del final del rango
--   :'evidence'  true/false: incluir la columna evidencia
--
-- El detector inserta una fila por ventana mientras la regla dispare (no hay
-- ciclo de vida de hallazgos, ver detection_engine/references/
-- 001_ciclo_vida_hallazgos.md): se agrupan por (antipatron, query_id,
-- database_name, table_name) y de cada grupo queda la deteccion mas reciente, con cuantas
-- veces se detecto y entre que fechas.
--
-- calidad_deadlocks compara los abrazos mortales que conto el motor
-- (pipeline_health.cycle_samples, un delta por ciclo) con los que QueryLens
-- capturo (public.deadlock_events, uno por informe unico, con o sin
-- hallazgo). CT-05 (cascada por transaccion larga) no es un abrazo mortal y
-- no entra. Los dos lados se cortan en [desde, hasta): el delta de un ciclo
-- cubre los ~10 s anteriores a la muestra, asi que en los bordes puede haber
-- un abrazo de diferencia.
WITH base AS (
    SELECT database_identifier, connection_name, engine, database_name
    FROM registered_databases
    WHERE database_identifier = :'id'
), h AS (
    SELECT id, antipatron, severidad, query_id, table_name, database_name, evidencia,
           explicacion, recomendacion, detectado_en
    FROM public.hallazgos
    WHERE db_id = :'id'
      AND detectado_en >= :'from'::timestamptz
      AND detectado_en < :'until'::timestamptz + make_interval(secs => :'grace'::int)
), g AS (
    SELECT DISTINCT ON (antipatron, query_id, database_name, table_name)
           antipatron, severidad, database_name, table_name, query_id,
           explicacion, recomendacion,
           CASE WHEN :'evidence'::boolean THEN evidencia END AS evidencia,
           count(*) OVER w AS veces,
           min(detectado_en) OVER w AS primera,
           max(detectado_en) OVER w AS ultima
    FROM h
    WINDOW w AS (PARTITION BY antipatron, query_id, database_name, table_name)
    ORDER BY antipatron, query_id, database_name, table_name, detectado_en DESC, id DESC
), s AS (
    SELECT deadlocks_since_prev, counter_reset, log_bytes_since_prev,
           seconds_since_prev, log_window_overrun
    FROM pipeline_health.cycle_samples
    WHERE db_id = :'id'
      AND taken_at >= :'from'::timestamptz
      AND taken_at < :'until'::timestamptz
), e AS (
    SELECT complete, antipatron
    FROM public.deadlock_events
    WHERE db_id = :'id'
      AND detected_at >= :'from'::timestamptz
      AND detected_at < :'until'::timestamptz
), c AS (
    SELECT (SELECT sum(deadlocks_since_prev) FROM s) AS hubo,
           (SELECT count(*) FROM e) AS capturados
)
SELECT json_build_object(
    'base', (SELECT row_to_json(base) FROM base),
    'desde', :'from'::timestamptz,
    'hasta', :'until'::timestamptz,
    'generado', now(),
    'resumen', json_build_object(
        'hallazgos', (SELECT count(*) FROM g),
        'detecciones', (SELECT count(*) FROM h),
        'por_severidad', (
            SELECT coalesce(json_object_agg(severidad, n), '{}')
            FROM (SELECT severidad, count(*) AS n FROM g GROUP BY severidad) s
        ),
        'por_antipatron', (
            SELECT coalesce(json_object_agg(antipatron, n ORDER BY n DESC, antipatron), '{}')
            FROM (SELECT antipatron, count(*) AS n FROM g GROUP BY antipatron) a
        )
    ),
    'calidad_deadlocks', json_build_object(
        -- MySQL: el contador y los encabezados de error_log son de todo el servidor
        'alcance', CASE (SELECT engine FROM base) WHEN 'mysql' THEN 'servidor' ELSE 'base' END,
        -- NULL: ningun ciclo de la ventana pudo comparar el contador con el anterior
        'hubo', (SELECT hubo FROM c),
        'capturados', (SELECT capturados FROM c),
        'con_detalle', (SELECT count(*) FROM e WHERE complete),
        'sin_detalle', (SELECT count(*) FROM e WHERE NOT complete),
        'por_patron', (
            SELECT coalesce(json_object_agg(patron, n ORDER BY patron), '{}')
            FROM (SELECT coalesce(antipatron, 'sin_patron') AS patron, count(*) AS n
                  FROM e WHERE complete GROUP BY 1) p
        ),
        'perdidos', (SELECT greatest(hubo - capturados, 0) FROM c),
        -- casi siempre un borde de la ventana o un reinicio del contador
        'capturados_de_mas', (SELECT greatest(capturados - hubo, 0) FROM c),
        'reinicios_contador', (SELECT count(*) FROM s WHERE counter_reset),
        'ciclos', (SELECT count(*) FROM s),
        'ciclos_con_desborde', (SELECT count(*) FROM s WHERE log_window_overrun),
        'log_kb_s_promedio', (
            SELECT round((sum(log_bytes_since_prev) / nullif(sum(seconds_since_prev), 0) / 1024)::numeric, 1)
            FROM s WHERE log_bytes_since_prev IS NOT NULL AND seconds_since_prev > 0
        ),
        'log_kb_s_max', (
            SELECT round((max(log_bytes_since_prev / seconds_since_prev) / 1024)::numeric, 1)
            FROM s WHERE log_bytes_since_prev IS NOT NULL AND seconds_since_prev > 0
        ),
        'hueco_max_s', (SELECT round(max(seconds_since_prev)::numeric, 0) FROM s),
        -- mas que LOOKBACK_S de DeadlocksStage (120 s): lo de ese tramo no se leyo
        'huecos_largos', (SELECT count(*) FROM s WHERE seconds_since_prev > 120)
    ),
    'hallazgos', (
        SELECT coalesce(json_agg(
                   g ORDER BY CASE severidad WHEN 'high' THEN 0
                                             WHEN 'medium' THEN 1
                                             WHEN 'low' THEN 2
                                             ELSE 3 END,
                              antipatron, ultima DESC), '[]')
        FROM g
    )
);
