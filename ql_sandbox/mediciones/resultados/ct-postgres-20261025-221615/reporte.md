# Contención CT-01 a CT-05 — postgres

Corrida `ct-postgres-20261025-221615` · base `db_12f68137` (registrada con `querylens add`) · 10 repeticiones por caso · inicio 2026-10-26 03:16:19.746423+00 · fin 2026-10-26 05:24:43.762194+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.5 / 9.7 | 8.1 / 11.2 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 4.5 / 8.7 | 7.5 / 11.6 | 10/10 | 10/10 |
| ct02_prepared | sin clasificar | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 8.8 / 9.8 | 11.9 / 14.3 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 15.2 / 19.6 | 17.7 / 21.7 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10
- `ct02_prepared`: sentencias preparadas: el log no trae los valores; hallazgos: sin clasificar 10; sin hallazgo: 0; evento: sin clasificar 10

## Controles (sin hallazgo esperado)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct05_raiz_corta | ninguno | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |
| ct05_una_espera | ninguno | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |

- `ct05_raiz_corta`: la raiz no llega a 30 s
- `ct05_una_espera`: una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo

## Casos límite

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01_prepared | sin clasificar | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 8.1 / 9.4 | 12.0 / 14.1 | 10/10 | 10/10 |
| ct01_tres | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.5 / 8.8 | 7.3 / 11.1 | 10/10 | 10/10 |
| rafaga | informativo | 20 (20) | 20 (20) | 20 con hallazgo correcto, 0 mal | 4.2 / 9.2 | — | — | — |
| otra_base | ninguno | 10 (0) | 0 (0) | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |
| desborde_log | informativo | 10 (10) | 3 (3) | informativo | 1.8 / 3.4 | 5.5 / 5.8 | 3/3 | — |
| terse | informativo | 10 (10) | 10 (10) | informativo | 4.8 / 9.0 | 6.3 / 10.4 | 10/10 | — |
| pausa | ninguno | 10 (10) | 0 (0) | **10/10** (100.0 %) · IC 95 % 72–100 % | — | — | — | — |

- `ct01_prepared`: sentencias preparadas: el log no trae los valores; hallazgos: sin clasificar 10; sin hallazgo: 0; evento: sin clasificar 10
- `ct01_tres`: ciclo de 3 transacciones; hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `rafaga`: dos abrazos a la vez, en el mismo ciclo; por repetición: 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos; 2/2 capt., 2 con detalle, 2 hallazgos
- `otra_base`: abrazo en otra base del mismo servidor: no es de la base monitoreada; sin hallazgo: 10
- `desborde_log`: ~400 KB/s de log durante 30 s (> 2 MB por ciclo); `LOG_WINDOW_OVERRUN` en pipeline_health.issues: sí
- `terse`: log_error_verbosity = terse; hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10; `DEADLOCK_REPORTS_PARTIAL` en pipeline_health.issues: sí
- `pausa`: monitoreo pausado 135 s (> 120 s de ventana)

## Positivos con carga de fondo (continuous_load.sh)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 6.3 / 9.7 | 8.2 / 12.5 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.7 / 9.0 | 9.1 / 14.1 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 14.6 / 18.6 | 17.2 / 22.1 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 30 | 30 | 30 | 30 | 0 | 0 | 0 | 131 | 0 | 0 | 10 | 0.7 |
| control | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 114 | 0 | 0 | 10 | 0.6 |
| limite | 70 | 53 | 70 | 53 | 17 | 0 | 0 | 267 | 34 | 10 | 150 | 579.9 |
| carga | 20 | 20 | 20 | 20 | 0 | 0 | 0 | 116 | 0 | 0 | 10 | 5.2 |

