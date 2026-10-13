# Medición de detección: AP-07, AP-01 y AP-06

Mide cuántos casos detecta cada antipatrón cuando hay **muchas consultas distintas a la vez**, no un solo escenario. Por cada antipatrón y motor:

- **100 positivos**: consultas patológicas, cada una con su propio `query_id` y con formas y tamaños variados;
- **40 controles**: consultas sanas que corren al mismo tiempo y **no** deberían marcarse (para medir falsos positivos);
- en algunos casos, **extra**: casos reales que quedan fuera de la regla declarada, reportados aparte (no cuentan en el 90 %).

Todo pasa por el sistema real: carga en el sandbox → `telemetry_pipeline` (contenedor `querylens_pipeline`) → PGMQ → `detection_engine` (contenedor `querylens_detection_engine`) → `public.hallazgos`, con un snapshot cada **60 s** (la ventana del informe).

## Cómo correrlo

Requisitos: sandbox (`ql_postgres`, `ql_mysql`) y compose de la raíz levantados, con las imágenes al día (`docker compose up -d --build pipeline detection_engine`). El orquestador usa solo la librería estándar.

```bash
# desde la raíz del repo
python ql_sandbox/mediciones/measure.py ap07 --engine postgres
python ql_sandbox/mediciones/measure.py all --engine both        # las 6 mediciones, ~1 h 15 min
python ql_sandbox/mediciones/measure.py ap06 --engine mysql --smoke   # prueba corta (8 positivos)
python ql_sandbox/mediciones/measure.py ap07 --engine mysql --temptable-max-ram-mib 8   # RAM de TempTable solo durante la corrida
python ql_sandbox/mediciones/measure.py --reevaluar ap07-mysql-20261012-211225          # recalcular un reporte sin volver a correr
```

Cada corrida deja una carpeta en `resultados/<medicion>-<motor>-<fecha>/`:

| Archivo | Contenido |
|---|---|
| `reporte.md` | resumen, desglose por categoría, positivos no detectados (con el motivo) y controles marcados |
| `results.json` | todo lo anterior por caso, con las ventanas medidas |
| `cases.json` | los casos elegidos, su SQL de muestra y su `query_id`; en AP-07, la calibración y los descartados |
| `driver_stats.json` | ejecuciones y errores de cada caso durante la carga |

Cada corrida usa un `db_id` propio (`medicion-<corrida>`): su historia en `statement_samples` y sus hallazgos no se mezclan con otras corridas.

## Archivos

| Archivo | Dónde corre | Qué hace |
|---|---|---|
| `measure.py` | host | orquesta: arma tablas, lanza la carga, toma los snapshots, inyecta (AP-01), evalúa y escribe el reporte |
| `driver.py` | contenedor desechable (imagen `querylens-pipeline`) | crea las tablas, calcula el `query_id` de cada caso, calibra (AP-07) y genera la carga con hilos |
| `cases.py` | — | definición de tablas, casos, inyección y limpieza de cada medición |
| `pipeline_snapshot.py` | `querylens_pipeline` | un snapshot de un motor con el `db_id` de la corrida |

El `query_id` de cada caso lo calcula el propio motor: `EXPLAIN (VERBOSE)` → `Query Identifier` en Postgres y `STATEMENT_DIGEST()` en MySQL. Antes de correr se verifica que no haya dos casos con el mismo `query_id`.

## Qué se mide en cada antipatrón

### AP-07 vertido a disco

- **Positivos**: 29 plantillas (GROUP BY, DISTINCT, COUNT(DISTINCT), ORDER BY grande, ventanas, hash join, UNION/EXCEPT, CTE, subconsulta IN, ROLLUP, agregados ordenados) × 6 tablas.
  - Postgres: tablas de 30.000 a 200.000 filas, `work_mem` de 64/128/256 kB que va rotando.
  - MySQL: la configuración del escenario de la Tabla 2 (`sort_buffer_size` reducido en la sesión y `temptable_use_mmap = OFF` en el servidor mientras dura la corrida, motor TempTable por defecto), con tablas de 100.000 a 500.000 filas: con menos, la tabla temporal cabe en los 2 MB de `temptable_max_ram` del sandbox y no derrama. Con `temptable_use_mmap = ON` (el valor por defecto) el desborde va a archivos mmap que `SUM_CREATED_TMP_DISK_TABLES` no cuenta (ver `detection_engine/references/002_ap07_alcance_sentencias.md` §8).
- **Calibración**: antes de la carga, cada candidato corre una vez solo y se mide si derramó con los contadores del motor. Se eligen 100 que derraman, alternando plantillas. En MySQL cuenta cualquiera de los dos indicadores que lee AP-07: tablas temporales en disco (`SUM_CREATED_TMP_DISK_TABLES`) u ordenamientos que fueron a disco (`SUM_SORT_MERGE_PASSES`, desde D7 del doc 008).
- **Controles**: agregados chicos, top-N, conteos, orden por índice y, en Postgres, un GROUP BY grande con `work_mem` de sobra.
- **Ventanas**: 5 snapshots; se mide en las ventanas 2–5. Un positivo es **válido** si su contador de spill subió en al menos una ventana.

### AP-01 degradación respecto de la línea base

- 10 tablas de 60.000 filas con 14 columnas indexadas; cada caso es una columna distinta (140 combinaciones: 100 positivos y 40 controles, asignados con semilla fija).
- Cada caso corre 1 vez por segundo (≈ 60 ejecuciones por ventana; la regla pide ≥ 30). Formas: igualdad, rango, `ORDER BY … LIMIT`, `COUNT`, `IN` de 5 valores, JOIN, `MAX`, `LIMIT 1`.
- 1 snapshot de historia + 10 ventanas de línea base; después del snapshot 11 se degradan los 100 positivos a la vez:

| Categoría | Positivos | Degradación |
|---|---|---|
| índice eliminado | 50 (MySQL: 35) | `DROP INDEX` |
| índice invisible (solo MySQL) | 15 | `ALTER INDEX … INVISIBLE` |
| índice menos selectivo | 25 | se borra el índice `(lo, cX)` y queda solo `(lo)`: lee ~3.000 filas en vez de ~1 |
| crecimiento de datos | 25 | se insertan 40.000 filas con valores que la consulta busca: devuelve ~1.600 filas en vez de ~1 |

- **Detectado**: hallazgo en las ventanas 12 o 13 (el informe pide ≤ 2 ventanas). Se reporta también la ventana 14 (tarde) y cualquier hallazgo antes de degradar (falso positivo).
- **Válido**: según los contadores del snapshot, la ventana 12 o 13 cumple la regla (≥ 30 ejecuciones, ≥ 2× la mediana de las 10 ventanas anteriores y ≥ +1 ms).

### AP-06 patrón N+1

- 12 pares de tablas padre/hija con FK (2 compuestas); los pares 10 y 11 solo tienen controles.
- **Positivos**: 10 pares × 2 direcciones (uno a muchos, muchos a uno) × 5 formas de padre e hija, con 2 a 100 filas por padre. El driver hace un N+1 real: ejecuta el padre y después una hija por cada fila que devolvió.
- **Controles**: consultas sueltas por la FK a ritmo fijo, página de detalle 1 + 1, JOIN en una consulta e `IN (...)` en lote; la mitad en tablas que también tienen N+1 y la otra mitad en tablas sin N+1.
- **Extra**: N+1 con caché (cada id se consulta una sola vez: la hija corre la mitad de veces que filas tiene el padre), fuera del ±20 % de la regla.
- **Ventanas**: 5 snapshots; se mide en las 2–5. **Válido**: alguna ventana con ≥ 100 ejecuciones de la hija y ±20 % de las filas de su padre. Se reporta además si el hallazgo nombra al padre correcto.

## Cómo leer el resultado

- **Positivos detectados / positivos** es la cifra principal (meta ≥ 90 %), con su intervalo de confianza de Wilson al 95 %.
- **Detección entre los verificados** descuenta los positivos en los que la patología no llegó a ocurrir según los contadores (por ejemplo, un AP-01 cuya degradación quedó por debajo de +1 ms): ahí el fallo es del caso, no del detector.
- **Controles marcados**: falsos positivos. En AP-07, un control que de verdad derramó (por la memoria compartida de MySQL) se informa aparte y no cuenta como falso positivo.
