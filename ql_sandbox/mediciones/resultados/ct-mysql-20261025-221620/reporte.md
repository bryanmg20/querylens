# Contención CT-01 a CT-05 — mysql

Corrida `ct-mysql-20261025-221620` · base `db_adb30660` (registrada con `querylens add`) · 10 repeticiones por caso · inicio 2026-10-26 03:16:24.61879+00 · fin 2026-10-26 05:27:02.971237+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.4 / 9.5 | 6.8 / 10.3 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 3.7 / 8.6 | 6.8 / 11.8 | 10/10 | 10/10 |
| ct03 | CT-03 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 6.8 / 9.7 | 9.8 / 14.4 | 10/10 | 10/10 |
| ct03_end | CT-03 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.3 / 10.0 | 7.0 / 11.6 | 10/10 | 10/10 |
| ct04 | CT-04 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.9 / 9.5 | 8.5 / 14.2 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 14.5 / 19.0 | 17.7 / 22.4 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10
- `ct03`: hallazgos: CT-03 10; sin hallazgo: 0; evento: CT-03 10
- `ct03_end`: hueco al final del indice; hallazgos: CT-03 10; sin hallazgo: 0; evento: CT-03 10
- `ct04`: hallazgos: CT-04 10; sin hallazgo: 0; evento: CT-04 10

## Controles (sin hallazgo esperado)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct04_indice | ninguno | 0 (0) | 0 (0) | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |
| ct04_for_update | ninguno | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |
| ct05_raiz_corta | ninguno | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |
| ct05_una_espera | ninguno | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |

- `ct04_indice`: con indice sobre cliente no hay abrazo
- `ct04_for_update`: amplia un SELECT ... FOR UPDATE: abrazo sin patron (issue #48); sin hallazgo: 10; evento: sin_patron 10
- `ct05_raiz_corta`: la raiz no llega a 30 s
- `ct05_una_espera`: una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo

## Casos límite

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01_tres | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.0 / 8.7 | 7.0 / 10.7 | 10/10 | 10/10 |
| rafaga | informativo | 30 (30) | 30 (10) | 10 con hallazgo correcto, 0 mal | 5.2 / 9.0 | — | — | — |
| rafaga_sin_print_all | informativo | 30 (30) | 10 (10) | 10 con hallazgo correcto, 0 mal | 5.1 / 9.2 | — | — | — |
| otro_schema | informativo | 10 (10) | 10 (10) | informativo | 5.6 / 9.9 | 8.9 / 14.1 | 10/10 | — |
| pausa | ninguno | 10 (10) | 0 (0) | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |

- `ct01_tres`: ciclo de 3 transacciones; hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `rafaga`: tres abrazos a la vez: INNODB STATUS guarda solo el ultimo; por repetición: 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos; 3/3 capt., 1 con detalle, 1 hallazgos
- `rafaga_sin_print_all`: innodb_print_all_deadlocks = OFF: error_log no recibe los encabezados; por repetición: 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; 1/3 capt., 1 con detalle, 1 hallazgos; `DEADLOCK_REPORTS_PARTIAL` en pipeline_health.issues: sí
- `otro_schema`: abrazo en otro schema: InnoDB no separa por schema; hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `pausa`: monitoreo pausado 135 s (> 120 s de ventana)

## Positivos con carga de fondo (continuous_load.sh)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.3 / 8.8 | 9.2 / 13.2 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.6 / 8.8 | 6.4 / 10.5 | 10/10 | 10/10 |
| ct03 | CT-03 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.7 / 8.9 | 9.3 / 14.0 | 10/10 | 10/10 |
| ct04 | CT-04 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.2 / 9.2 | 5.8 / 10.9 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 14.5 / 19.0 | 17.8 / 22.6 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10
- `ct03`: hallazgos: CT-03 10; sin hallazgo: 0; evento: CT-03 10
- `ct04`: hallazgos: CT-04 10; sin hallazgo: 0; evento: CT-04 10

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 50 | 50 | 50 | 50 | 0 | 0 | 0 | 168 | 0 | 0 | 10 | None |
| control | 10 | 10 | 10 | 10 | 0 | 0 | 0 | 149 | 0 | 0 | 10 | None |
| limite | 90 | 60 | 90 | 60 | 30 | 0 | 20 | 173 | 0 | 10 | 150 | None |
| carga | 40 | 40 | 40 | 40 | 0 | 0 | 0 | 151 | 0 | 0 | 10 | None |

