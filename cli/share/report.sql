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
-- table_name) y de cada grupo queda la deteccion mas reciente, con cuantas
-- veces se detecto y entre que fechas.
WITH base AS (
    SELECT database_identifier, connection_name, engine, database_name
    FROM registered_databases
    WHERE database_identifier = :'id'
), h AS (
    SELECT id, antipatron, severidad, query_id, table_name, evidencia,
           explicacion, recomendacion, detectado_en
    FROM public.hallazgos
    WHERE db_id = :'id'
      AND detectado_en >= :'from'::timestamptz
      AND detectado_en < :'until'::timestamptz + make_interval(secs => :'grace'::int)
), g AS (
    SELECT DISTINCT ON (antipatron, query_id, table_name)
           antipatron, severidad, table_name, query_id,
           explicacion, recomendacion,
           CASE WHEN :'evidence'::boolean THEN evidencia END AS evidencia,
           count(*) OVER w AS veces,
           min(detectado_en) OVER w AS primera,
           max(detectado_en) OVER w AS ultima
    FROM h
    WINDOW w AS (PARTITION BY antipatron, query_id, table_name)
    ORDER BY antipatron, query_id, table_name, detectado_en DESC, id DESC
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
