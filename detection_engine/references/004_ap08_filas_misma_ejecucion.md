# AP-08: estimación contra las filas de la misma ejecución

> **Estado:** implementado el 2026-10-29, sin respaldo. Las limitaciones de la sección 5 quedan para evaluar más adelante.
> Mediciones y scripts en `ql_sandbox/campana_cardinalidad_ap08/`.

## 1. El problema

AP-08 (error grave de estimación de cardinalidad) comparaba la estimación del plan con el **promedio** de filas por ejecución de la última ventana. Pero el plan es de **una** ejecución, con **un** valor de parámetro:

- **Postgres:** el último plan que registró auto_explain (consultas de 50 ms o más) o, si no hay, el `GENERIC_PLAN`.
- **MySQL:** el EXPLAIN de `QUERY_SAMPLE_TEXT`, normalmente la ejecución más lenta.

Cuando una consulta recibe valores que devuelven cantidades de filas muy distintas, cada plan es correcto para su valor, pero no para el promedio. En la batería de 8 escenarios por el flujo real:

| Escenario | Postgres | MySQL |
|---|---|---|
| card07 índice sesgado, 1 % valor frecuente (500.000 filas) | falso positivo: 498.033 estimadas contra un promedio de 4.400 | falso positivo: 402.566 contra 6.000 |
| card08 sin índice, 10 % valor frecuente | falso positivo: 17 estimadas contra un promedio de 50.000 | falso positivo en ventanas con pocas ejecuciones terminadas |

## 2. La solución

El plan normalizado trae `estimates.actual_rows`: las filas que devolvió **la misma ejecución** de la que sale el plan, o `null` si el plan no viene de una ejecución. AP-08 compara `output_rows` con `actual_rows`.

| Motor | De dónde sale | `actual_rows` |
|---|---|---|
| Postgres, `auto_explain` | entrada del log de una ejecución de la aplicación | `Actual Rows` de la raíz, con `auto_explain.log_analyze = on` y `log_timing = off` |
| Postgres, `generic` | `EXPLAIN (GENERIC_PLAN)` | `null` |
| MySQL, `history` | EXPLAIN del texto de la ejecución más lenta de esa consulta que siga en `events_statements_history` | su `ROWS_SENT` |
| MySQL, `sample` | EXPLAIN de `QUERY_SAMPLE_TEXT`, cuando no hay ninguna ejecución en el historial | `null` |

QueryLens no ejecuta ANALYZE. En Postgres, las filas las cuenta el motor mientras ejecuta las consultas de la aplicación. En MySQL, `ROWS_SENT` ya lo guarda performance_schema.

**Sin respaldo:** si `actual_rows` es `null`, AP-08 no evalúa la consulta en ese snapshot. Se sigue exigiendo, además, que la consulta se haya ejecutado desde el snapshot anterior.

## 3. Resultados

- **Escenario del informe** (100.000 filas, `a = b = c` con 10 valores; `run_scenario.py cardinality_misestimate`): Postgres 101 estimadas contra 10.000 reales (q-error 99, plan de auto_explain), MySQL 100 contra 10.000 (q-error 100, plan del historial); recomendación de `CREATE STATISTICS` y de histogramas; detectado en el 1.º o 2.º ciclo.
- **Batería AP-08:** Postgres 8/8; los falsos positivos de card07 y card08 desaparecieron. En MySQL, los dos hallazgos que quedaron son:
  - card07, que salió del respaldo (ya eliminado);
  - card08, que es un error real de MySQL: estima un 10 % fijo sin histograma y la ejecución devolvió 1 fila.
- **Sin efecto en los otros patrones:**
  - campaña de AP-02 escaneo completo evitable, AP-03 predicado no sargable y AP-04 índice ausente: 56/56 antes y después;
  - AP-05 índice no utilizado o redundante: idéntico en los 28 escenarios de cada motor;
  - escenario de AP-05: 6/6.
- **Costo de `log_analyze` en Postgres** (sin medir tiempos): no se distingue del ruido (de −2 % a +3 %, 4 rondas de 30 s).

## 4. Cobertura en MySQL

`events_statements_history` guarda las **últimas 10 sentencias de cada conexión**, de cualquier tipo. Una consulta se puede emparejar si alguna de sus ejecuciones sigue ahí en el momento del snapshot. Carga realista (16 conexiones, 10 consultas de la aplicación, 62 snapshots):

| Consulta | Historial en 10 | Con relleno*, historial en 10 | Con relleno*, historial en 100 |
|---|---|---|---|
| rápidas y frecuentes (1–4 ms) | 97 % | 71–97 % | 97 % |
| lentas y poco frecuentes (2–3 s) | 44–76 % | **5–18 %** | 50–84 % |

\* 200 tipos de consulta fuera de top impact que se llevan el 85 % de las sentencias de las mismas conexiones.

El ~3 % que falta en las rápidas corresponde a los snapshots tomados después de cortar la carga, cuando las conexiones ya se cerraron.

## 5. Limitaciones y qué evaluar

1. **MySQL, consultas lentas y poco frecuentes.** Con el historial por defecto (10) y muchas consultas compartiendo las conexiones, se emparejan entre un 5 y un 18 % de los snapshots. Un error persistente se detecta igual, pero tarde: con snapshots cada 60 s, en promedio entre ~5 y ~20 minutos. Son justamente las consultas que más le importan a AP-08.
   - **Mitigación aplicada:** `setup_target_mysql.sql` deja `performance_schema_events_statements_history_size` en 100 como mínimo (`SET PERSIST_ONLY`, se aplica en el reinicio que el script ya pide). Con 100, las consultas lentas se encontraron entre el 50 y el 84 % de las veces. Si el servidor no se reinicia, sigue en 10.
2. **Postgres, consultas de menos de 50 ms.** Usan el plan genérico, que no tiene filas reales, así que AP-08 no las evalúa. Un error de estimación grave suele hacer lenta la consulta y la lleva al log, pero no siempre.
3. **Conexiones que se cierran enseguida** (una por pedido): el historial de MySQL se pierde al cerrar la conexión.
4. **Respaldo con el promedio de la ventana, descartado por ahora.** Daría cobertura completa, pero en los snapshots sin emparejar vuelve el falso positivo por mezcla de valores. Opciones evaluadas, para retomar si la demora del punto 1 resulta inaceptable:
   - **respaldo marcado:** evidencia `fuente_filas_reales = promedio_ventana`, severidad más baja y una advertencia en la explicación;
   - **respaldo con heurística:** no evaluar si el tiempo de ejecución varía mucho (CV o máx/mín). Con la batería, atrapa card07 (CV 10,9 en Postgres, máx/mín 2.652 en MySQL) pero **no** card08: un escaneo completo tarda parecido devuelva 1 fila o 500.000 (CV de MySQL 0,05, igual que un caso sano). Además, el desvío de MySQL es estimado por el pipeline y los dos motores lo dan acumulado, no por ventana.
5. **La ejecución que se elige del historial es la más lenta.** En una consulta con valores mezclados suele ser la del valor frecuente. Si el error de estimación está solo en los valores raros (card08 en MySQL), se ve menos seguido.
6. **Solo la salida final.** Postgres con `log_analyze` trae las filas reales de cada nodo, pero MySQL solo las finales. Para que los dos motores se traten igual, se compara solo la salida.
7. **Las exclusiones esconden errores.** Un error de estimación dentro de una consulta con GROUP BY, DISTINCT, agregados o LIMIT no se detecta: la salida de esas consultas no se puede comparar con la estimación del plan (en MySQL el último acceso estima las filas antes de agrupar).
8. **Solo candidatos de top impact.** Sin plan no hay estimación: una consulta que nunca entra a top impact no se evalúa. Es igual en todos los detectores que usan el plan.

### Costos a vigilar

9. **Memoria del historial de MySQL.** performance_schema reserva el historial por conexión: filas × (texto + digest + fila) × conexiones. Crece en proporción al tamaño del historial. En el sandbox (`max_connections` = 1.000, texto de 4.096 bytes), con carga realista y relleno:

   | Historial | Memoria | Lentas y poco frecuentes emparejadas (real06–08) | Ventanas de 60 s hasta emparejar (promedio) |
   |---|---|---|---|
   | 10 (por defecto) | 16 MB | 11–19 % | ~5–9 |
   | 50 | 81 MB | 31–66 % | ~1,5–3 |
   | 100 (configurado) | 162 MB | 65–90 % | ~1,1–1,5 |

   M4 pide ≤ 2 ventanas: solo 100 lo cumple con margen para estas consultas. Queda en 100, con la memoria como costo declarado. Con menos `max_connections` o un texto más corto la memoria baja; no se midió.
10. **Volumen del log de Postgres.** Con `log_analyze`, cada plan registrado es más largo (trae las filas reales de cada paso). El pipeline lee los últimos 2 MB del log por ciclo: con más bytes por plan entran menos planes, y con mucha carga alguna consulta lenta puede quedarse sin el suyo. Sin medir.
11. **Costo de `log_analyze` en producción.** Medido solo en el sandbox (4 sesiones, consultas de hasta 1M de filas): no se distinguió del ruido. Con mucha concurrencia puede ser distinto.

### Validación pendiente

12. **Campaña del informe:** 10 inyecciones por motor más corridas de control, con precisión y exhaustividad ≥ 0,90 (M1 y M2). El escenario del informe pasó una vez por motor con el código nuevo (sección 3).
13. **Latencia M4 con ventanas de 60 s.** Todo se midió con snapshots cada 10 s. Con el historial en 10, una consulta lenta y poco frecuente en MySQL que se empareja en ~18 % de los snapshots tardaría en promedio ~5 ventanas, y M4 pide ≤ 2 ventanas (120 s); con 100 baja a ~1,1–1,5 ventanas (punto 9). El escenario del informe no tiene ese problema (la consulta es frecuente). Falta confirmarlo con ventanas reales de 60 s.
14. **Calibrar la severidad.** En el escenario del informe Postgres dio q-error 99 ("media") y MySQL 100 ("alta"): el mismo caso cae a los dos lados del umbral de 100.

### Sin verificar

15. **Semijoin en MySQL** (`IN (SELECT …)`): el último acceso puede estimar las filas antes de quitar duplicados.
16. **EXPLAIN posterior a la ejecución (MySQL).** El EXPLAIN se hace en el ciclo, segundos después de la ejecución elegida; si en ese lapso se actualizaron las estadísticas, la estimación ya no es la que usó esa ejecución.
17. **Formas poco probadas:** subconsultas en el FROM, CTE y planes paralelos de Postgres.

### Pendientes de código

18. **Recomendación por motor:** `_recommendation` elige el texto deduciendo el motor por el tipo de `query_id`. Opciones: texto neutro, un campo `engine` en el contrato, o una acción neutra que la API traduzca.
19. **`table_name` en joins:** queda como `"a, b"`, que no es un nombre de tabla.
20. **Hallazgos repetidos** en cada snapshot mientras dure el problema (`001_ciclo_vida_hallazgos.md`).

## 6. Configuración

Aplicada el 2026-10-29 en los scripts de instalación, la guía y el sandbox:

| Dónde | Postgres | MySQL |
|---|---|---|
| `querylens_database/setup_target_*.sql` | `auto_explain.log_analyze = on`, `auto_explain.log_timing = off` (y el chequeo final los verifica) | `performance_schema_events_statements_history_size` ≥ 100 (`PERSIST_ONLY`, pide reinicio) y el consumer `events_statements_history` activo |
| `teardown_target_mysql.sql` | — | devuelve el tamaño del historial al valor de antes |
| `PREPARAR_BASE.md`, `telemetry_pipeline/references/006` y `007` | textos actualizados | fila nueva en "Qué cambia el script en MySQL" |
| `ql_sandbox/docker-compose.yml` | `-c auto_explain.log_analyze=on -c auto_explain.log_timing=off` | `--performance-schema-events-statements-history-size=100` |

Para que el sandbox tome el compose nuevo hay que recrear `ql_postgres` y `ql_mysql` (`docker compose up -d postgres mysql` en `ql_sandbox/`).
