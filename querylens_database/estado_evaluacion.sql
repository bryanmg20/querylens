-- Estado de evaluacion por patologia (SegundoInforme 6.1 y 6.3), en el schema
-- public de la base de QueryLens.
--
-- En cada snapshot el detector anota, de cada una de las 13 patologias del
-- catalogo (AP-01 a AP-08, CT-01 a CT-05), si la regla se pudo evaluar y, si
-- no, por que. Sirve para no leer "sin hallazgos" donde hubo "no evaluada".
--
-- Una fila por patologia y ventana serian 13 filas cada 10 s por base. Se
-- guarda agrupado: las ventanas de un mismo tramo de 10 minutos que quedaron
-- con el mismo estado y los mismos motivos comparten fila y suman en
-- `ventanas`. Una base sin problemas deja 13 filas por tramo; un estado que
-- cambia de una ventana a otra no multiplica las filas, porque dentro del tramo
-- cada combinacion de estado y motivos tiene una sola.
--
--   estado    evaluada | parcial | no_evaluada
--   firma     codigos de los motivos, ordenados y separados por coma ('' si
--             evaluada). Con el estado, identifica la fila dentro del tramo
--   motivos   [{codigo, origen, severidad, seccion, mensaje, accion}] de la
--             ultima ventana de la fila. origen = recoleccion (un problema
--             del estado de la recoleccion, con su accion de remedio) o
--             detector (una condicion de la propia regla)
--   detalle   lo que la regla tuvo para mirar en la ultima ventana de la fila
--
-- El estado actual de una base son las filas con el `hasta` mas reciente. El
-- de un rango (sesion de deteccion, reporte) es la suma de `ventanas` por
-- estado de las filas que lo tocan: en los bordes puede sobrar hasta un tramo.
--
-- Lo escribe detection_engine/storage.py (save_evaluation), que tambien purga
-- las filas de mas de 15 dias. Lo aplican db-roles (service_roles.sql, como
-- ql_detector) en cada up y el detector al arrancar: todo es idempotente. El
-- detector lo ejecuta con SQLAlchemy: en este archivo no puede haber dos
-- puntos pegados a una palabra (los tomaria como parametro) ni el signo de
-- porcentaje.

CREATE TABLE IF NOT EXISTS public.estado_evaluacion (
    -- registro de la base (en MySQL, todo el servidor)
    db_id TEXT NOT NULL,
    -- codigo de la patologia
    codigo TEXT NOT NULL,
    antipatron TEXT NOT NULL,
    -- inicio del tramo de 10 minutos de las ventanas de la fila
    tramo TIMESTAMPTZ NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('evaluada', 'parcial', 'no_evaluada')),
    firma TEXT NOT NULL DEFAULT '',
    motivos JSONB NOT NULL DEFAULT '[]',
    detalle JSONB NOT NULL DEFAULT '{}',
    -- ventanas (snapshots) del tramo que quedaron con este estado y estos motivos
    ventanas INTEGER NOT NULL DEFAULT 1,
    -- hora de la primera y de la ultima de esas ventanas (cuando se encolo su
    -- snapshot)
    desde TIMESTAMPTZ NOT NULL,
    hasta TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (db_id, codigo, tramo, estado, firma)
);

-- Estado actual de una base, rangos de fechas y purga.
CREATE INDEX IF NOT EXISTS estado_evaluacion_hasta_idx
    ON public.estado_evaluacion (db_id, hasta);
