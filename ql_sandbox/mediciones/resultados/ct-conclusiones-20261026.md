# Detección de abrazos mortales CT-01 a CT-05: resultados y conclusiones

Corridas del 2026-10-26 (rama `feature/ct-measurements`, issue #49, verificación de `perdidos` del #36):

| Motor | Corrida | Casos | Repeticiones | Duración |
|---|---|---|---|---|
| Postgres 17 | [`ct-postgres-20261025-221615`](ct-postgres-20261025-221615/reporte.md) | 16 | 10 por caso | 2 h 08 min |
| MySQL 8.0.46 | [`ct-mysql-20261025-221620`](ct-mysql-20261025-221620/reporte.md) | 20 | 10 por caso | 2 h 11 min |

Cada corrida registra la base con `querylens add`, provoca cada escenario con los
scripts de `ql_sandbox/scripts/`, guarda la verdad (pid de cada sesión, víctima, hora)
y la cruza con `public.deadlock_events` y `public.hallazgos`. Al final de cada fase
compara el contador del motor con el reporte del CLI (`querylens report` →
`calidad_deadlocks`). Detalle de los casos y reglas de conteo:
[`../README.md`](../README.md), sección "Contención: CT-01 a CT-05".

Con 10 repeticiones, un 10/10 da un intervalo de confianza del 95 % de 72–100 %: alcanza
para decir que el caso funciona de forma estable, no para afirmar una tasa exacta.

## Resumen

- **Todos los patrones que le corresponden a cada motor se detectan y clasifican bien
  en el 100 % de las repeticiones**, con y sin carga de fondo: CT-01, CT-02 y CT-05 en
  Postgres; CT-01 a CT-05 en MySQL. Ningún hallazgo sale con el patrón equivocado, y
  no hay hallazgos que no correspondan a un escenario provocado.
- **Todos cumplen M4**: el abrazo más lento tardó 9,8 s en Postgres y 10,0 s en MySQL
  (límite 15 s). El CT-05 más lento tardó 19,6 s desde que la raíz cumplió 30 s
  (límite 45 s).
- **Los controles no generan falsos positivos**: CT-04 con índice, CT-05 con raíz
  corta, CT-05 con una sola espera y el abrazo en otra base de Postgres.
- **Los abrazos se pierden en cuatro situaciones**, todas por límites del motor o del
  pipeline y no del detector. Ver "Dónde no se detecta".
- **El CLI cuenta bien los perdidos**: en todas las fases, `hubo` coincide con el
  contador del motor y `capturados`/`perdidos` coinciden con la verdad.
- **Se encontró y corrigió un fallo**: un abrazo de MySQL se registraba dos veces si
  `innodb_print_all_deadlocks` cambiaba entre dos ciclos. Ver "Fallo corregido".

## Resultados por caso

Latencia = `detection_delay_s` del hallazgo (snapshot − abrazo), mediana / máximo.

### Postgres

| Caso | Qué prueba | Resultado | Latencia (s) |
|---|---|---|---|
| ct01 | CT-01, dos sesiones | 10/10 CT-01 | 5,5 / 9,7 |
| ct01_tres | CT-01, ciclo de tres sesiones | 10/10 CT-01, `cycle_size` 3 | 5,5 / 8,8 |
| ct02 | CT-02 | 10/10 CT-02 | 4,5 / 8,7 |
| ct01_prepared, ct02_prepared | sentencias preparadas: el log no trae los valores | 10/10 cada uno, `unclassified_deadlock` (esperado) | 8,1 / 9,8 |
| ct05 | CT-05 | 10/10 | 15,2 / 19,6 |
| ct05_raiz_corta, ct05_una_espera | controles CT-05 | 0 falsos positivos en 20 | — |
| rafaga | dos abrazos en el mismo ciclo | 20/20 capturados y clasificados | 4,2 / 9,2 |
| otra_base | abrazo en otra base del servidor | 0 hallazgos en 10 (correcto: no es de la base monitoreada) | — |
| terse | `log_error_verbosity = terse` | 10/10 CT-01 | 4,8 / 9,0 |
| desborde_log | ~400 KB/s de log durante 30 s | 3/10 capturados; `LOG_WINDOW_OVERRUN` | — |
| pausa | monitoreo pausado 135 s | 0/10 capturados (esperado) | — |
| carga: ct01, ct02, ct05 | los mismos con `continuous_load.sh` | 30/30 | 6,3 / 9,7 (abrazos), 14,6 / 18,6 (CT-05) |

### MySQL

| Caso | Qué prueba | Resultado | Latencia (s) |
|---|---|---|---|
| ct01 | CT-01, dos sesiones | 10/10 CT-01 | 4,4 / 9,5 |
| ct01_tres | CT-01, ciclo de tres sesiones | 10/10 CT-01 | 5,0 / 8,7 |
| ct02 | CT-02 | 10/10 CT-02 | 3,7 / 8,6 |
| ct03, ct03_end | CT-03, hueco antes de un registro y al final del índice | 20/20 CT-03, posición del hueco correcta | 4,3–6,8 / 10,0 |
| ct04 | CT-04, UPDATE sin índice | 10/10 CT-04, recomienda `CREATE INDEX ... (cliente)` | 4,9 / 9,5 |
| ct05 | CT-05 | 10/10 | 14,5 / 19,0 |
| ct04_indice | control: con índice no hay abrazo | 0 abrazos, 0 hallazgos | — |
| ct04_for_update | ampliación con `SELECT ... FOR UPDATE` | 10/10 evento sin patrón, sin hallazgo (#48) | — |
| ct05_raiz_corta, ct05_una_espera | controles CT-05 | 0 falsos positivos en 20 | — |
| rafaga | tres abrazos en el mismo ciclo | 30/30 capturados, 10 con detalle y hallazgo | 5,2 / 9,0 |
| rafaga_sin_print_all | lo mismo con `innodb_print_all_deadlocks = OFF` | 10/30 capturados; `DEADLOCK_REPORTS_PARTIAL` | 5,1 / 9,2 |
| otro_schema | abrazo en otro schema del servidor | 10/10 CT-01 sobre `ql_otra.ql_ct01_cuentas` | 5,6 / 9,9 |
| pausa | monitoreo pausado 135 s | 0/10 capturados (esperado) | — |
| carga: ct01 a ct05 | los mismos con `continuous_load.sh` | 50/50 | 4,2–5,7 / 9,2 (abrazos), 14,5 / 19,0 (CT-05) |

## Dónde no se detecta

| Situación | Motor | Qué se pierde | Lo avisa |
|---|---|---|---|
| Pipeline detenido o pausado más de 120 s (`LOOKBACK_S`) | ambos | todo abrazo de ese hueco: 10/10 en `pausa` | CLI: `perdidos` y `huecos_largos` |
| Más de un abrazo entre dos ciclos | MySQL | el detalle de todos menos el último: 20/30 en `rafaga` quedan como evento sin hallazgo. INNODB STATUS guarda solo el último | CLI: `sin_detalle` |
| Lo mismo con `innodb_print_all_deadlocks = OFF` | MySQL | además, el registro: 20/30 no quedan ni como evento | `DEADLOCK_REPORTS_PARTIAL` y CLI: `perdidos` |
| Más de 2 MB de log por ciclo (`LOG_WINDOW_BYTES`) | Postgres | 7/10 en `desborde_log` | `LOG_WINDOW_OVERRUN`, CLI: `ciclos_con_desborde` y `perdidos` |

Además hay dos casos que se registran pero no generan hallazgo:

- **Sentencias preparadas en Postgres** (PREPARE/EXECUTE, o un driver que manda los
  parámetros aparte): el log no trae los valores, así que no se puede saber qué fila
  espera cada proceso. El abrazo sale como `unclassified_deadlock` (10/10 en ambos
  casos). Es un hallazgo, pero sin patrón ni recomendación específica.
- **Ampliación de bloqueos con `SELECT ... FOR UPDATE` en MySQL**: el abrazo se
  registra como evento sin patrón (10/10), sin hallazgo. Es lo que describe el #48.

## Verificación del CLI (`calidad_deadlocks`, #36)

| Motor | Fase | Motor contó | CLI `hubo` | Capturados (verdad / CLI) | Perdidos (verdad / CLI) | Sin detalle (CLI) |
|---|---|---|---|---|---|---|
| Postgres | positivo | 30 | 30 | 30 / 30 | 0 / 0 | 0 |
| Postgres | control | 0 | 0 | 0 / 0 | 0 / 0 | 0 |
| Postgres | límite | 70 | 70 | 53 / 53 | 17 / 17 | 0 |
| Postgres | carga | 20 | 20 | 20 / 20 | 0 / 0 | 0 |
| MySQL | positivo | 50 | 50 | 50 / 50 | 0 / 0 | 0 |
| MySQL | control | 10 | 10 | 10 / 10 | 0 / 0 | 0 |
| MySQL | límite | 90 | 90 | 60 / 60 | 30 / 30 | 20 |
| MySQL | carga | 40 | 40 | 40 / 40 | 0 / 0 | 0 |

`perdidos` coincide con la verdad en las 8 fases, y `capturados_de_mas` es 0 en todas.
Los perdidos de Postgres son los 7 de `desborde_log` y los 10 de `pausa`. Los de MySQL
son los 20 de `rafaga_sin_print_all` y los 10 de `pausa`. Los 20 "sin detalle" de MySQL
son los encabezados de `rafaga`.

`otra_base` no aparece en la fase límite de Postgres porque `pg_stat_database.deadlocks`
es por base: ni el motor ni el CLI lo cuentan para `ql_demo`, y es correcto. En MySQL,
el CLI declara `alcance: servidor`, porque `lock_deadlocks` no separa por schema.

## Fallo corregido

En el smoke previo (1 repetición), el mismo abrazo de MySQL quedó dos veces en
`deadlock_events` y generó un hallazgo duplicado. El CLI, por eso, reportó 7 capturados
y 2 perdidos cuando lo real era 6 y 3.

**Causa.** El detector deduplica con la clave `detected_at|pid de la víctima`.
DeadlocksStage pone en `detected_at` la hora del encabezado de `error_log`, con
microsegundos, si lo encuentra. Si no lo encuentra, pone la de INNODB STATUS, sin
fracciones de segundo. INNODB STATUS muestra el mismo último abrazo en cada ciclo: si
entre dos ciclos se apaga `innodb_print_all_deadlocks`, o `performance_schema.error_log`
descarta el encabezado, la clave cambia y el abrazo parece nuevo.

**Corrección** (`detection_engine/deadlocks/deadlock_detector.py`). Un informe de
`innodb_status` ya se vio si hay uno con la misma víctima a 2 s o menos y una de las dos
horas es de segundo exacto. Dos horas con microsegundos distintas siguen siendo dos
abrazos, como una conexión que reintenta y vuelve a ser víctima. Tiene tests en los dos
sentidos y para el reintento, y pasan los 193 tests del detector. Se verificó en vivo y
en esta corrida: `rafaga_sin_print_all` apaga y prende la variable 10 veces, y no hubo
ningún duplicado ni hallazgo sin corrida.

## Sugerencias

En orden de impacto:

1. **#48: clasificar la ampliación con `SELECT ... FOR UPDATE`.** Es el único abrazo de
   MySQL con detalle completo que no genera hallazgo (10/10). La regla de CT-04 exige
   `undo_log_entries > 0`, y un `FOR UPDATE` no modifica filas. Ampliar la regla, o crear
   una variante, a transacciones con muchas filas bloqueadas y cero modificadas cubriría
   el caso. La recomendación sería la misma: índice sobre la columna del `WHERE`.

2. **Postgres: `terse` no debería disparar `DEADLOCK_REPORTS_PARTIAL`.** Con
   `log_error_verbosity = terse` se clasificaron 10/10 con detalle completo, pero el
   pipeline avisa de reportes parciales (`telemetry_pipeline/stages/deadlocks.py:188`).
   La explicación probable: `terse` recorta DETAIL solo en el log de texto (`stderr`), y
   `jsonlog`, que es lo que lee el pipeline, siempre lo incluye. Conviene confirmarlo en
   el código de Postgres (`elog.c`). Si se confirma, la condición sobre `terse` sobra con
   `jsonlog` y hoy es una falsa alarma en `querylens health`.

3. **MySQL: leer el informe completo de `error_log` en ráfagas.** Con
   `innodb_print_all_deadlocks = ON`, InnoDB escribe cada abrazo completo en el error log,
   no solo el encabezado. Hoy el pipeline toma de `performance_schema.error_log` solo el
   encabezado (MY-012468) y deja sin detalle todos los abrazos menos el último: 2 de cada
   3 en `rafaga`. Vale la pena revisar si las filas siguientes traen las transacciones y
   los bloqueos, y si alcanzan para `parse_innodb_status`. Si alcanzan, las ráfagas se
   clasificarían completas.

4. **Postgres: el desborde de log pierde la mayoría (7/10).** Se avisa bien
   (`LOG_WINDOW_OVERRUN`), pero se pierde. Una opción es que `querylens.read_log` filtre
   las líneas `40P01` del lado del servidor, antes de aplicar el límite de 2 MB. Otra es
   agrandar la ventana del ciclo siguiente cuando el anterior desbordó.

5. **Margen de M4 medido de punta a punta.** `detection_delay_s` (hasta el snapshot)
   tuvo como máximo 10,0 s. Pero "Guardado" (hasta que el hallazgo está en
   `public.hallazgos`, con la cola y el detector) llegó a 14,4 s en MySQL y 14,3 s en
   Postgres, y pasó de 12 s en 25 de 190 abrazos. Si M4 se interpreta como "visible para
   el usuario en 15 s", el margen es menor a 1 s. Conviene fijar en el issue cuál de las
   dos es la métrica.

6. **Pausas mayores a 120 s.** Se pierde todo lo del hueco, y el CLI lo cuenta en
   `perdidos` y `huecos_largos`. En Postgres se podría recuperar parte al reanudar,
   leyendo desde la última posición del log en vez de los últimos 120 s, mientras el
   log siga ahí. En MySQL no hay forma: INNODB STATUS no guarda historia.

7. **MySQL, otro schema:** el hallazgo nombra bien la tabla (`ql_otra.ql_ct01_cuentas`),
   pero se asocia a la base registrada (`ql_demo`). El CLI ya declara `alcance: servidor`.
   Si en la interfaz se agrupan hallazgos por base, convendría marcar los que son de otro
   schema.

## Limitaciones de la medición

- Sandbox local, con un único cliente provocando cada escenario y la carga sintética
  de `continuous_load.sh`. No reproduce la concurrencia ni los patrones de una
  aplicación real.
- `PREPARE/EXECUTE` simula un driver que manda los parámetros aparte. No se probó un
  driver real.
- 10 repeticiones por caso: suficiente para detectar fallos sistemáticos, no los raros.
  Un fallo que ocurra 1 de cada 50 veces probablemente no aparezca.
- La corrida corre los dos motores en paralelo sobre el mismo stack de QueryLens. Los
  tiempos incluyen esa competencia.

## Archivos

- `ct-postgres-20261025-221615/` y `ct-mysql-20261025-221620/`: `reporte.md` (tablas
  completas), `results.json` (cada repetición), `truth.json` (lo que se provocó) y
  `cli/` (reporte del CLI por fase y `querylens health` al final).
- Para volver a evaluar sin correr de nuevo, mientras la base de QueryLens tenga los
  datos: `python3 ql_sandbox/mediciones/measure_ct.py --reevaluar <carpeta>`.
