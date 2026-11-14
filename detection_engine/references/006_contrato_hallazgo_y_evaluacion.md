# Contrato del hallazgo, orden por impacto y estado de evaluación

Issues #50, #51, #52 y lo pendiente de #78. Implementado en la rama `feature/contrato-hallazgo` (2026-11-12). Fuente: `SegundoInforme.md` 6.1, 6.2, 6.3, Tabla 6 y 11.4.

Validado de punta a punta el 2026-11-12 con el stack completo (scheduler, pipeline, detector, API por nginx, CLI y front) sobre el sandbox: Postgres y MySQL registrados por HTTP, carga de AP-02, AP-03, AP-04, AP-06, AP-07 y AP-08, abrazos CT-01 a CT-05 (MySQL, con un CT-01 en el schema `ql_otra`) y CT-01, CT-02 y CT-05 (Postgres). Mismo orden en la API y en la CLI; estados evaluada, parcial y no_evaluada con motivos del detector y de la recolección (`MISSING_PG_READ_ALL_STATS` con el permiso revocado, `HOST_UNREACHABLE` con el motor detenido y con una base que nunca conectó).

## 1. Riesgo (#50)

- Campo `riesgo` del `Hallazgo` (`models.py`), columna `problemas.riesgo`. Es lo que puede pasar al **aplicar la recomendación**, no la gravedad del problema (eso es la severidad).
- Lo escribe **cada detector**, junto a su recomendación, y sigue sus variantes: AP-03 por subtipo, AP-04 suma el costo de un índice por rama en un OR, AP-05 distinto para «revisar», «eliminar un índice sin uso», «duplicado exacto» y «prefijo», AP-07 distinto si solo fue el ordenamiento. Se descartó un catálogo por código de patología porque una misma patología recomienda cosas distintas.
- CT-01 a CT-05 y el abrazo sin clasificar ya tenían su texto en `evidencia.risk`: ahora es el mismo texto en `riesgo`.

## 2. Impacto (#50)

Campo `impacto_ms` del `Hallazgo`, en milisegundos. Se guarda en cada detección (`detecciones.impacto_ms`) y el de la última en `problemas.impacto_ms`.

| Patología | Impacto | Dónde queda en la evidencia |
|---|---|---|
| AP-01, AP-07 | Tiempo de la sentencia en la ventana (delta de `total_time_ms`) | `impact_ms`, `window_time_ms` |
| AP-02, AP-03, AP-04, AP-08 | Ídem; los detectores ya medían la ventana de ejecuciones y filas, ahora también la de tiempo | `window_time_ms` |
| AP-06 | Tiempo de la consulta **hija** (todas sus ejecuciones) **más el del padre** en la ventana: el de la operación completa, como en los demás patrones. Sin el tiempo del padre, solo la hija. No incluye la latencia de red de cada viaje entre la aplicación y la base, que es lo que más cuesta en un N+1: el impacto es un piso | `window_time_ms` (`child_window_time_ms` + `parent_window_time_ms`) |
| AP-05 | Tiempo en la ventana de la **consulta asociada** al hallazgo (su `query_id`). El costo real del índice está en las escrituras, que el snapshot no reparte por índice | `window_time_ms` y en `queries_que_filtran_la_tabla` |
| CT-05 | Espera acumulada de las sesiones afectadas (`impact_wait_s` × 1000) | `impact_wait_s` |
| CT-01 a CT-04, sin clasificar | **Sin medir** (`None`): el informe del motor no dice cuánto esperó cada transacción | — |

El tiempo nunca decide si hay hallazgo: si no viene en el snapshot el hallazgo sale igual, con impacto `None`.

## 3. Orden por impacto (#51)

`prioritization.py`: de mayor a menor impacto; empates por severidad (alta, media, baja) y después por código de patología (`catalog.py`, en el orden del informe, no alfabético por nombre). Un impacto sin medir cuenta como 0; un patrón fuera del catálogo va al final; la clave del problema deja fijo el último empate. `main.process_job` guarda en ese orden.

La API (`api_rest/app/routers/diagnostics.py`) y el reporte (`cli/share/report.sql`) ordenan los **problemas** con la misma regla sobre la **suma** del impacto de sus detecciones en la sesión o el rango (el tiempo que el problema costó en ese periodo). Si se cambia la regla, cambiar los tres lugares. El reporte ordena por la suma sin redondear (la muestra redondeada a 0,1 ms): redondeada, dos problemas de 0,04 y 0,01 ms empatarían y la CLI y la API darían órdenes distintos.

## 4. Estado de evaluación por patología (#52)

- **Contrato del snapshot:** campo nuevo `collection_status` (`CollectionStatus`: `status`, `sections_failed`, `issues` con code/severity/section/message/remediation). Lo arma `HealthReport.snapshot_status()` y lo agrega `telemetry_pipeline/main._cycle` antes de validar. Sin él, una sección que falló llegaba igual que una vacía.
- **Cálculo:** `evaluation.py`, para cada snapshot y cada una de las 13 patologías: `evaluada`, `parcial` (se evaluó, pero quedó algo afuera) o `no_evaluada`, con motivos de origen `recoleccion` (un código del catálogo del pipeline, con su acción de remedio) o `detector` (`LINEA_BASE_EN_CONSTRUCCION`, `SIN_PLAN`, `SIN_FILAS_REALES`, `SIN_CLAVES_FORANEAS`, `VENTANA_ESTADISTICAS_CORTA`, `SECCION_NO_LEIDA`).
- **Hallazgos descartados:** si a una regla le falta una fuente que lee (una sección que no se pudo leer, `TRACK_COUNTS_OFF` en AP-05), sus hallazgos de esa ventana se descartan: con los índices sin leer, AP-04 reportaría como ausente un índice que existe.
- **Guardado:** `public.estado_evaluacion` (`querylens_database/estado_evaluacion.sql`), agrupado por tramos de 10 minutos: las ventanas con el mismo estado y motivos comparten fila y suman `ventanas`. Retención de 15 días, como `pipeline_health`.
- **Secciones que afinan:** AP-04 lee `columns` solo para descartar una conversión implícita; si esa sección falla queda `parcial` y sus hallazgos se conservan (salen con `tipo_de_columna_desconocido`).
- **Ciclo fallido:** si no hubo snapshot (problema bloqueante de conexión) el detector no corre; la API marca todas como no evaluadas con los problemas bloqueantes abiertos. Si el detector nunca dejó un estado (la base falla desde el primer ciclo), la API devuelve igual las 13 como no evaluadas con esos motivos, en vez de una lista vacía. El reporte de la CLI lista los tramos del rango sin extracción (`ciclos_fallidos`: problemas `blocking` de conexión, credenciales o internos de `pipeline_health.issues`, del primer al último ciclo que falló, recortados al rango, con cuántos ciclos fallaron; no hasta `resolved_at`, que es el siguiente ciclo bueno y puede llegar horas después si el monitoreo estuvo apagado), porque esas ventanas no dejan fila en `estado_evaluacion`; los `blocking` de una sección no entran, porque el snapshot sale igual y el detector ya los guarda como motivo.
- **No entran:** `LOG_WINDOW_OVERRUN` y `DEADLOCKS_MISSED` (el pipeline los calcula después de encolar); siguen en la salud de la extracción.
- **CT-03 y CT-04 en PostgreSQL:** el detector no distingue el motor; la API y el reporte las muestran como «no aplica».

## 5. Base del hallazgo (#78, lo pendiente)

Los antipatrones guardan `database_name` (la de la sentencia; en AP-05 la de la consulta asociada). El CSV del reporte tiene la columna `base`.

La clave del problema (`storage.finding_key`) lleva la tabla calificada (`schema.tabla`), o el índice calificado en AP-05: la misma consulta, o el mismo índice, en dos schemas da dos problemas.

### Validación en vivo (2026-11-12)

Stack aislado (`compose -p qlaud`, volumen nuevo) sobre el sandbox, con las bases registradas por HTTP. En cada caso se corrió la **misma** consulta (o los mismos índices) en los dos lugares a la vez, sobre tablas `ql_scn` de 1.000.000 de filas:

| Caso | Carga | Resultado |
|---|---|---|
| MySQL, schemas `ql_demo` y `ql_otra` (un registro) | `WHERE c = ?` sin índice en `c` | AP-02, AP-04 y AP-08 una vez por schema (6 problemas), misma huella de texto, `database_name` correcto en `problemas`, API, CSV (`base`) y MD (`Base:`) |
| MySQL, schemas `ql_demo` y `ql_otra` (un registro) | carga de AP-05 (`ql_scn_k_dup`, `ql_scn_c`) | AP-05 «redundante» una vez por schema (4 problemas), cada uno con su base. «Sin uso» no se probó: pide 30 min de estadísticas desde el arranque |
| Postgres, bases `ql_demo` y `ql_otra` (dos registros) | `WHERE c = ?` | Cada registro ve solo su base (el pipeline filtra por `current_database()`); AP-02 y AP-04 con `database_name` correcto |
| Postgres, schemas `public` y `ql_otra_s` de `ql_demo` (un registro, un rol por schema) | `WHERE c = ?` | AP-02 y AP-04 una vez por schema: claves `public.ql_scn` y `ql_otra_s.ql_scn` |
| Front | — | La tarjeta muestra «Base `ql_demo`» / «Base `ql_otra`» en los tres registros |

La API y la CLI dieron el mismo orden en todos los casos. En una primera lectura parecieron distintos, pero era AP-08 pasando de severidad media a alta entre las dos consultas.

### Limitación: schema en Postgres con `SET search_path`

En Postgres el schema de una sentencia **sale del rol**, no de la sesión: `pg_stat_statements` no guarda el schema y el pipeline toma el primer schema con tablas del `search_path` configurado en el rol (`SCHEMA_RESOLVER_QUERY`; limitación ya aceptada en `telemetry_pipeline/references/004_decisiones_contrato.md`, «schema_name es escalar por statement»).

Verificado en vivo: con el mismo rol (`ql_user`, `search_path` por defecto) y `SET search_path = ql_otra_s` en la sesión, la consulta sobre `ql_otra_s.ql_scn` se asignó a `public` y sus detecciones cayeron en el problema de `public.ql_scn`. Con un rol propio (`ALTER ROLE ... SET search_path = ql_otra_s`) se separó bien.

Además, el `queryid` depende de la tabla y no del rol: si un rol mal resuelto y uno bien resuelto ejecutan la misma consulta sobre la misma tabla, el statement llega dos veces con el mismo `query_id` y uno con el schema equivocado. En la prueba hubo que borrar la fila vieja de `ql_user` (`pg_stat_statements_reset(userid, dbid, queryid)`) para que el caso del rol propio se viera limpio.

Para el informe: en Postgres, la separación por schema requiere un rol por schema; con un solo rol que cambia de schema en la sesión, los hallazgos se atribuyen al primer schema del rol. En MySQL no aplica: el digest ya trae el schema.

## 6. Diferencias con el informe (actualizar la Tabla 6 y la 6.2)

- **AP-01:** la condición de no evaluada es la de las ventanas de 10 s (60 ventanas previas con ≥ 5 ejecuciones), no 10 ventanas con ≥ 30.
- **AP-02, AP-03, AP-04:** «la sentencia no tiene plan» se cuenta sobre las candidatas que son SELECT simples: `no_evaluada` si a todas les falta, `parcial` si a algunas.
- **AP-05:** con la ventana de estadísticas corta queda `parcial` (el subtipo «redundante» se evalúa igual), no `no_evaluada`.
- **AP-06:** también queda sin evaluar si la base no declara claves foráneas.
- **AP-08:** además de sin plan, sin las filas de una ejecución real (plan genérico o muestra de MySQL).
- **Impacto de AP-05 y de los abrazos mortales:** el informe no los define; ver la sección 2.
