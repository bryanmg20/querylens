# Medición de detección: AP-07, AP-01 y AP-06 (y CT-01 a CT-05)

La contención (CT-01 a CT-05) tiene su propio banco, `measure_ct.py`: ver [Contención](#contención-ct-01-a-ct-05) al final.

Mide cuántos casos detecta cada antipatrón cuando hay **muchas consultas distintas a la vez**, no un solo escenario. Por cada antipatrón y motor:

- **100 positivos**: consultas patológicas, cada una con su propio `query_id` y con formas y tamaños variados;
- **40 controles**: consultas sanas que corren al mismo tiempo y **no** deberían marcarse (para medir falsos positivos);
- en algunos casos, **extra**: casos reales que quedan fuera de la regla declarada, reportados aparte (no cuentan en el 90 %).

Todo pasa por el sistema real: carga en el sandbox → `telemetry_pipeline` (servicio `pipeline` del compose) → PGMQ → `detection_engine` (servicio `detection-engine`) → `public.hallazgos`, con un snapshot cada **60 s** (la ventana del informe).

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
| `pipeline_snapshot.py` | servicio `pipeline` | un snapshot de un motor con el `db_id` de la corrida |

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

## Contención: CT-01 a CT-05

`measure_ct.py` corre los escenarios de `ql_sandbox/scripts/<motor>/simulate_ct0*.sh` varias veces y compara lo que de verdad pasó con lo que QueryLens vio. A diferencia de las mediciones de antipatrones, no toma snapshots a mano: registra la base del sandbox con el CLI (`querylens add`, conexión `sandbox-pg` o `sandbox-mysql`) y deja que el servicio `pipeline` recolecte cada 10 s. Así se mide también lo que escribe `pipeline_health` (`cycle_samples`, `issues`) y el reporte del CLI.

Corrida completa en un solo comando: levanta el sandbox, pone al día las imágenes del stack (`pipeline`, `detection-engine` y la CLI) y corre los dos motores en paralelo. Con 10 repeticiones tarda unas 2 h.

```bash
bash ql_sandbox/mediciones/run_ct.sh            # 10 repeticiones por caso, Postgres y MySQL
bash ql_sandbox/mediciones/run_ct.sh 5 postgres # 5 repeticiones, un solo motor
PG_VERSION=15 MYSQL_VERSION=8.4 bash ql_sandbox/mediciones/run_ct.sh 2   # otras versiones
```

`PG_VERSION` (17 por defecto; 15, 16, 18, 19) y `MYSQL_VERSION` (8.0; 8.4) eligen la versión del
sandbox: el script levanta su profile y `measure_ct.py` apunta a ella con `--pg-version` /
`--mysql-version`. Cada versión se registra como otra base en QueryLens y deja su carpeta como
`ct-postgres15-<fecha>` o `ct-mysql84-<fecha>`.

Por partes:

```bash
# desde la raiz del repo, con el sandbox y el compose de la raiz levantados
python ql_sandbox/mediciones/measure_ct.py --engine postgres            # 10 repeticiones por caso
python ql_sandbox/mediciones/measure_ct.py --engine mysql --smoke       # 2 repeticiones
python ql_sandbox/mediciones/measure_ct.py --engine mysql --cases ct01,rafaga --reps 3
python ql_sandbox/mediciones/measure_ct.py --engine postgres --pg-version 18   # con el profile pg18 arriba
python ql_sandbox/mediciones/measure_ct.py --reevaluar ct-mysql-20261026-001500
```

Los dos motores se pueden correr a la vez, en dos procesos. Cada corrida deja `resultados/ct-<motor>-<fecha>/`:

| Archivo | Contenido |
|---|---|
| `truth.json` | la verdad de cada repetición: salida del script, pid, víctima y contador del motor antes y después. Se escribe en cada repetición: `--reevaluar` parte de acá |
| `results.json` | evaluación por repetición y resumen por caso y por fase |
| `reporte.md` | tablas por fase, calidad de captura según el CLI y corridas con un resultado distinto del esperado |
| `cli/<fase>.json` | `querylens report --format json` de la ventana de cada fase |
| `cli/health.txt` | `querylens health` al terminar |

### Casos

| Fase | Postgres | MySQL | Qué se espera |
|---|---|---|---|
| positivo | `ct01`, `ct02`, `ct01_bind`, `ct02_bind`, `ct05` | `ct01`, `ct02`, `ct01_prepared`, `ct02_prepared`, `ct03`, `ct03_end`, `ct04`, `ct05` | el hallazgo del patrón |
| control | `ct05_raiz_corta`, `ct05_una_espera` | `ct04_indice`, `ct04_for_update`, `ct05_raiz_corta`, `ct05_una_espera` | ningún hallazgo |
| límite | `ct01_bind_rapido`, `ct02_bind_rapido`, `ct01_tres`, `ct01_tres_bind`, `ct01_tres_bind_rapido`, `sin_unica`, `sin_unica_rapido`, `dos_tablas`, `dos_schemas`, `particionada`, `rafaga`, `otra_base`, `desborde_log`, `terse`, `pausa` | `ct01_tres`, `rafaga`, `rafaga_sin_print_all`, `otro_schema`, `pausa` | ver abajo |
| carga | `ct01`, `ct02`, `ct05` | `ct01` a `ct05` | lo mismo que en positivo, con `continuous_load.sh` de fondo |

Casos límite:

- `ct01_tres`: ciclo de 3 transacciones (`SESSIONS=3`).
- Valores aparte en PG (`MODE=bind`: `\bind` de psql, el mismo protocolo extendido que psycopg 3 o JDBC; el log trae `$1`). La fila de cada espera sale del ctid: el de la víctima de su CONTEXT, el de las demás de su aviso `still waiting` (`log_lock_waits`), que solo escriben si esperaron más que `deadlock_timeout` (1 s) antes de que se cerrara el ciclo.
  - `ct01_bind`, `ct02_bind`, `ct01_tres_bind`: esperas separadas por 1,5 s (`GAP=1.5`). Se espera el patrón.
  - `ct01_bind_rapido`, `ct02_bind_rapido`, `ct01_tres_bind_rapido`: separadas por 0,5 s. Queda solo el ctid de la víctima: `unclassified_deadlock`.
  - `sin_unica`, `sin_unica_rapido`: valores escritos, pero el `WHERE` no cubre un índice único. Con 1,5 s, CT-01 por el ctid; con 0,5 s, `unclassified_deadlock`.
  - `dos_tablas`, `dos_schemas`, `particionada`: filas con el mismo ctid en tablas, schemas o particiones distintas. Se espera CT-01, no CT-02.
- `ct01_prepared`, `ct02_prepared` (MySQL): `PREPARE ... FROM` / `EXECUTE ... USING`. El informe de InnoDB identifica cada registro por su dirección (space:page:heap), así que se espera el patrón. `PREPARE` de SQL no es el protocolo binario de un driver: INNODB STATUS muestra la sentencia con los valores sustituidos, pero la clasificación no la lee.
- `rafaga`: 2 (PG) o 3 (MySQL) escenarios a la vez, en el mismo ciclo. En MySQL, `INNODB STATUS` guarda solo el último: los demás llegan sin detalle desde `error_log`.
- `rafaga_sin_print_all` (MySQL): lo mismo con `innodb_print_all_deadlocks = OFF`. `error_log` no recibe los encabezados y el CLI tendría que mostrar los perdidos.
- `otra_base` (PG) y `otro_schema` (MySQL): abrazo en `ql_otra`. En PG no es de la base monitoreada. En MySQL, InnoDB no separa por schema: es informativo.
- `desborde_log` (PG): ~400 KB/s de `RAISE WARNING` durante 30 s, más que los 2 MB por ciclo que lee `PgLogTail`. Se espera `LOG_WINDOW_OVERRUN`.
- `terse` (PG): `log_error_verbosity = terse`. Se espera `DEADLOCK_REPORTS_PARTIAL`.
- `pausa`: `querylens pause` durante 135 s, más que los 120 s de ventana de `DeadlocksStage`. El abrazo se pierde y el CLI tendría que contarlo en `perdidos` y `huecos_largos`.

Los ajustes del servidor (`terse`, `innodb_print_all_deadlocks`) y la base `ql_otra` se restauran al terminar, aunque la corrida falle.

### Cómo se cuenta

- **Abrazo**: el script terminó con una víctima (`deadlock detected` o `ERROR 1213`).
- **Capturado**: hay una fila en `public.deadlock_events`, por la clave `detected_at|pid de la víctima`. Si llegó sin detalle, cuenta el primer evento sin detalle de la ventana del escenario.
- **Correcto**: el hallazgo con el mismo conjunto de pid del ciclo es del patrón esperado. En los controles, que no haya hallazgo. En CT-05 se busca por el pid de la raíz.
- **Latencia (M4)**: `detection_delay_s` de la evidencia (≤ 15 s). En CT-05, la antigüedad de la raíz en el snapshot del hallazgo menos 30 s (≤ 45 s). *Guardado* suma la cola y el detector: `detectado_en` menos el mismo instante.
- **Evidencia**: CT-01 con el tamaño del ciclo y la tabla, CT-03 con el hueco en la posición esperada, CT-04 con `related_antipattern = AP-04` y `CREATE INDEX … (cliente)`, y CT-05 con todas las sesiones bloqueadas.
- **CLI**: por fase, `hubo` de `calidad_deadlocks` contra el contador leído directo del motor, y `capturados` contra los eventos.
