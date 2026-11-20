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
-- El detector guarda cada problema una vez (public.problemas, por db_id +
-- antipatron + finding_key) y una fila por deteccion (public.detecciones,
-- con su hora y su severidad; ver querylens_database/hallazgos.sql). El
-- reporte trae un problema por linea con las detecciones del rango: cuantas,
-- entre que fechas, la severidad mas alta y la de la ultima, y cuantas de cada
-- severidad; mas las del historico completo. La explicacion, la recomendacion,
-- su riesgo y la evidencia son las de la ultima deteccion del problema, aunque
-- sea posterior al rango.
--
-- Orden por impacto (SegundoInforme 6.2): impacto_ms es la suma del impacto de
-- las detecciones del rango (tiempo de base de datos de la sentencia, o espera
-- acumulada en contencion). Los empates van por la severidad mas alta y
-- despues por el codigo de la patologia; un impacto sin medir (abrazos
-- mortales) cuenta como 0. Es la misma regla que
-- detection_engine/prioritization.py y que la API de diagnosticos.
--
-- evaluacion: por patologia, cuantas ventanas del rango quedaron evaluadas, en
-- parte o sin evaluar (public.estado_evaluacion, por tramos de 10 minutos: en
-- los bordes puede sobrar un tramo), y los motivos de la ultima que no quedo
-- evaluada.
--
-- ciclos_fallidos: los tramos del rango en que el pipeline no pudo extraer
-- (problemas blocking de conexion, credenciales o internos, de
-- pipeline_health.issues). Sin snapshot el detector no corre y esas ventanas
-- no dejan fila en estado_evaluacion: sin esto el reporte las omitiria sin
-- decir por que. La API hace lo mismo con el ultimo ciclo (fetch_evaluation).
-- Los blocking de una seccion (p. ej. PG_STATEMENTS_NOT_INSTALLED) no entran:
-- el snapshot sale igual y el detector ya los guarda como motivo.
--
-- calidad_deadlocks compara los abrazos mortales que conto el motor
-- (pipeline_health.cycle_samples, un delta por ciclo) con los que QueryLens
-- capturo (public.deadlock_events, uno por informe unico, con o sin
-- hallazgo). CT-05 (cascada por transaccion larga) no es un abrazo mortal y
-- no entra. Los dos lados se cortan en [desde, hasta): el delta de un ciclo
-- cubre los ~10 s anteriores a la muestra, asi que en los bordes puede haber
-- un abrazo de diferencia.
WITH base AS (
    SELECT database_identifier, connection_name, engine, database_name,
           detection_session_started_at AS sesion_desde
    FROM registered_databases
    WHERE database_identifier = :'id'
), h AS (
    -- detecciones del rango
    SELECT d.problema_id, d.severidad, d.detectado_en, d.impacto_ms
    FROM public.detecciones d
    JOIN public.problemas p ON p.id = d.problema_id
    WHERE p.db_id = :'id'
      AND d.detectado_en >= :'from'::timestamptz
      AND d.detectado_en < :'until'::timestamptz + make_interval(secs => :'grace'::int)
), r AS (
    SELECT problema_id,
           count(*) AS veces,
           min(detectado_en) AS primera,
           max(detectado_en) AS ultima,
           -- severidad mas alta del rango: alta 3, media 2, baja 1
           max(CASE severidad WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END)
               AS rango_severidad,
           (array_agg(severidad ORDER BY detectado_en DESC))[1] AS severidad_actual,
           sum(impacto_ms) AS impacto_ms
    FROM h
    GROUP BY problema_id
), rs AS (
    SELECT problema_id, severidad, count(*) AS n
    FROM h
    GROUP BY problema_id, severidad
), g AS (
    SELECT p.antipatron, p.codigo,
           CASE r.rango_severidad WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low'
                ELSE r.severidad_actual END AS severidad,
           -- para el orden, no salen en el JSON (ver hallazgos abajo). El
           -- orden usa el impacto sin redondear, como la API: redondeado, dos
           -- problemas de 0.04 y 0.01 ms empatarian y decidiria la severidad
           r.rango_severidad,
           p.finding_key,
           r.impacto_ms AS impacto_orden,
           round(r.impacto_ms::numeric, 1) AS impacto_ms,
           r.severidad_actual,
           (SELECT json_object_agg(rs.severidad, rs.n) FROM rs WHERE rs.problema_id = p.id)
               AS por_severidad,
           p.database_name, p.table_name, p.query_id,
           -- lo que distingue dos problemas de la misma consulta y tabla: el
           -- indice (AP-05) o la columna (AP-03), aunque no se pida la evidencia
           p.evidencia->>'index_name' AS indice,
           p.evidencia->>'column' AS columna,
           p.explicacion, p.recomendacion, p.riesgo,
           CASE WHEN :'evidence'::boolean THEN p.evidencia END AS evidencia,
           r.veces, r.primera, r.ultima,
           (SELECT count(*) FROM public.detecciones x WHERE x.problema_id = p.id) AS veces_historico,
           p.primera_deteccion AS primera_historica
    FROM r
    JOIN public.problemas p ON p.id = r.problema_id
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
), ev AS (
    -- tramos del estado de evaluacion que tocan el rango
    SELECT codigo, antipatron, estado, motivos, ventanas, hasta
    FROM public.estado_evaluacion
    WHERE db_id = :'id'
      AND hasta >= :'from'::timestamptz
      AND desde < :'until'::timestamptz + make_interval(secs => :'grace'::int)
), evp AS (
    SELECT codigo,
           min(antipatron) AS antipatron,
           coalesce(sum(ventanas) FILTER (WHERE estado = 'evaluada'), 0) AS evaluada,
           coalesce(sum(ventanas) FILTER (WHERE estado = 'parcial'), 0) AS parcial,
           coalesce(sum(ventanas) FILTER (WHERE estado = 'no_evaluada'), 0) AS no_evaluada,
           -- motivos de la ultima fila del rango que no quedo evaluada
           (array_agg(motivos ORDER BY hasta DESC) FILTER (WHERE estado <> 'evaluada'))[1] AS motivos,
           (array_agg(estado ORDER BY hasta DESC))[1] AS estado_final
    FROM ev
    GROUP BY codigo
), cf_cortes AS (
    -- cortes que dejaron ciclos sin snapshot y que tocan el rango: una fila de
    -- issues por corte (si se resuelve y vuelve, es otra fila). El corte va del
    -- primer al ultimo ciclo que fallo (last_seen), recortado al rango; no
    -- hasta resolved_at, que es el siguiente ciclo bueno y puede llegar horas
    -- despues si el monitoreo estuvo apagado
    SELECT code, message, remediation, last_seen,
           greatest(first_seen, :'from'::timestamptz) AS desde,
           least(last_seen, :'until'::timestamptz) AS hasta,
           resolved_at IS NULL AS abierto,
           -- ciclos que fallaron en el corte; puede incluir ciclos fuera del
           -- rango si el corte lo cruza
           occurrences AS ciclos
    FROM pipeline_health.issues
    WHERE db_id = :'id'
      AND severity = 'blocking'
      AND scope IN ('connection', 'credentials', 'pipeline', 'enqueue')
      AND first_seen < :'until'::timestamptz
      AND last_seen >= :'from'::timestamptz
), cf AS (
    -- una linea por codigo: una base que falla de forma intermitente deja
    -- decenas de cortes iguales. Textos del corte mas reciente
    SELECT code,
           (array_agg(message ORDER BY last_seen DESC))[1] AS message,
           (array_agg(remediation ORDER BY last_seen DESC))[1] AS remediation,
           min(desde) AS desde,
           max(hasta) AS hasta,
           bool_or(abierto) AS abierto,
           sum(ciclos) AS ciclos,
           count(*) AS cortes
    FROM cf_cortes
    GROUP BY code
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
    'evaluacion', (
        SELECT coalesce(json_agg(
                   json_build_object(
                       'codigo', codigo,
                       'antipatron', antipatron,
                       -- CT-03 y CT-04 dependen de InnoDB (Tabla 3)
                       'aplica', NOT ((SELECT engine FROM base) = 'postgresql' AND codigo IN ('CT-03', 'CT-04')),
                       'ventanas', json_build_object('evaluada', evaluada, 'parcial', parcial,
                                                     'no_evaluada', no_evaluada),
                       'estado_final', estado_final,
                       'motivos', coalesce(motivos, '[]'::jsonb)
                   ) ORDER BY codigo), '[]')
        FROM evp
    ),
    'ciclos_fallidos', (
        SELECT coalesce(json_agg(
                   json_build_object(
                       'codigo', code, 'mensaje', message, 'accion', remediation,
                       'desde', desde, 'hasta', hasta, 'abierto', abierto, 'ciclos', ciclos,
                       'cortes', cortes
                   ) ORDER BY desde, code), '[]')
        FROM cf
    ),
    -- Nombre en espanol de cada patron, el mismo que muestra el front
    -- (frontend/lib/antipatterns.ts): el md y el csv lo usan en vez del
    -- identificador interno. Si se cambia un titulo, cambiar los dos lugares.
    'nombres', '{
        "baseline_degradation": "Degradación frente a su histórico",
        "avoidable_full_scan": "Escaneo completo evitable",
        "non_sargable_predicate": "Predicado no sargable",
        "missing_index": "Índice ausente",
        "unused_index": "Índice sin uso o redundante",
        "n_plus_one": "Patrón N+1",
        "disk_spill": "Derrame a disco",
        "cardinality_misestimate": "Estimación de filas errada",
        "inconsistent_lock_order_deadlock": "Abrazo mortal por orden inconsistente",
        "lock_mode_conversion_deadlock": "Abrazo mortal por conversión de modo",
        "range_contention_deadlock": "Abrazo mortal por contención de rango",
        "lock_amplification_deadlock": "Ampliación de bloqueos por falta de índice",
        "long_transaction_cascade": "Cascada por transacción larga",
        "unclassified_deadlock": "Abrazo mortal sin clasificar",
        "sin_patron": "Sin patrón"
    }'::json,
    'hallazgos', (
        SELECT coalesce(json_agg(
                   (to_jsonb(g) - 'rango_severidad' - 'finding_key' - 'impacto_orden')
                   ORDER BY coalesce(impacto_orden, 0) DESC, rango_severidad DESC,
                            codigo NULLS LAST, finding_key), '[]')
        FROM g
    )
);
