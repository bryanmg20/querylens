# Contención CT-01 a CT-05 — mysql

Corrida `ct-mysql-20261025-192818` · base `db_e00a5b74` (registrada con `querylens add`) · 1 repeticiones por caso · inicio 2026-10-26 00:28:22.330678+00 · fin 2026-10-26 00:45:25.497071+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 8.4 / 8.4 | 9.6 / 9.6 | 1/1 | 1/1 |
| ct02 | CT-02 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 0.1 / 0.1 | 1.4 / 1.4 | 1/1 | 1/1 |
| ct03 | CT-03 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 1.8 / 1.8 | 3.3 / 3.3 | 1/1 | 1/1 |
| ct03_end | CT-03 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 3.5 / 3.5 | 5.1 / 5.1 | 1/1 | 1/1 |
| ct04 | CT-04 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 5.8 / 5.8 | 7.6 / 7.6 | 1/1 | 1/1 |
| ct05 | CT-05 | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | 13.3 / 13.3 | 15.6 / 15.6 | 1/1 | 1/1 |

- `ct01`: hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `ct02`: hallazgos: CT-02 1; sin hallazgo: 0; evento: CT-02 1
- `ct03`: hallazgos: CT-03 1; sin hallazgo: 0; evento: CT-03 1
- `ct03_end`: hueco al final del indice; hallazgos: CT-03 1; sin hallazgo: 0; evento: CT-03 1
- `ct04`: hallazgos: CT-04 1; sin hallazgo: 0; evento: CT-04 1

## Controles (sin hallazgo esperado)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct04_indice | ninguno | 0 (0) | 0 (0) | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |
| ct04_for_update | ninguno | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |
| ct05_raiz_corta | ninguno | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |
| ct05_una_espera | ninguno | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |

- `ct04_indice`: con indice sobre cliente no hay abrazo
- `ct04_for_update`: amplia un SELECT ... FOR UPDATE: abrazo sin patron (issue #48); sin hallazgo: 1; evento: sin_patron 1
- `ct05_raiz_corta`: la raiz no llega a 30 s
- `ct05_una_espera`: una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo

## Casos límite

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01_tres | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 0.7 / 0.7 | 5.2 / 5.2 | 1/1 | 1/1 |
| rafaga | informativo | 3 (3) | 3 (1) | 1 con hallazgo correcto, 0 mal | 2.4 / 2.4 | — | — | — |
| rafaga_sin_print_all | informativo | 3 (3) | 1 (1) | 1 con hallazgo correcto, 0 mal | 5.0 / 5.0 | — | — | — |
| otro_schema | informativo | 1 (1) | 1 (1) | informativo | 0.1 / 0.1 | 0.7 / 0.7 | 1/1 | — |
| pausa | ninguno | 1 (1) | 0 (0) | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |

- `ct01_tres`: ciclo de 3 transacciones; hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `rafaga`: tres abrazos a la vez: INNODB STATUS guarda solo el ultimo; por repetición: 3/3 capt., 1 con detalle, 1 hallazgos
- `rafaga_sin_print_all`: innodb_print_all_deadlocks = OFF: error_log no recibe los encabezados; por repetición: 1/3 capt., 1 con detalle, 1 hallazgos; `DEADLOCK_REPORTS_PARTIAL` en pipeline_health.issues: sí
- `otro_schema`: abrazo en otro schema: InnoDB no separa por schema; hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `pausa`: monitoreo pausado 135 s (> 120 s de ventana)

## Positivos con carga de fondo (continuous_load.sh)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 6.2 / 6.2 | 8.4 / 8.4 | 1/1 | 1/1 |
| ct02 | CT-02 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 10.4 / 10.4 | 12.8 / 12.8 | 1/1 | 1/1 |
| ct03 | CT-03 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 9.5 / 9.5 | 12.2 / 12.2 | 1/1 | 1/1 |
| ct04 | CT-04 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 1.4 / 1.4 | 4.2 / 4.2 | 1/1 | 1/1 |
| ct05 | CT-05 | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | 18.7 / 18.7 | 24.8 / 24.8 | 1/1 | 1/1 |

- `ct01`: hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `ct02`: hallazgos: CT-02 1; sin hallazgo: 0; evento: CT-02 1
- `ct03`: hallazgos: CT-03 1; sin hallazgo: 0; evento: CT-03 1
- `ct04`: hallazgos: CT-04 1; sin hallazgo: 0; evento: CT-04 1

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 5 | 5 | 5 | 5 | 0 | 0 | 0 | 20 | 0 | 0 | 13 | None |
| control | 1 | 1 | 1 | 1 | 0 | 0 | 0 | 18 | 0 | 0 | 13 | None |
| limite | 9 | 6 | 9 | 7 | 2 | 0 | 2 | 20 | 0 | 1 | 163 | None |
| carga | 4 | 4 | 4 | 4 | 0 | 0 | 0 | 19 | 0 | 0 | 13 | None |

## Hallazgos de contención que no son de ninguna corrida

- 2026-10-26T00:36:54.110059+00:00: `range_contention_deadlock` (pid [739, 742])

