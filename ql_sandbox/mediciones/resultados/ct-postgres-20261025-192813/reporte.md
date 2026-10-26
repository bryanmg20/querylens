# Contención CT-01 a CT-05 — postgres

Corrida `ct-postgres-20261025-192813` · base `db_f5af522f` (registrada con `querylens add`) · 1 repeticiones por caso · inicio 2026-10-26 00:28:17.670676+00 · fin 2026-10-26 00:45:07.031794+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 1.8 / 1.8 | 3.1 / 3.1 | 1/1 | 1/1 |
| ct02 | CT-02 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 3.6 / 3.6 | 5.1 / 5.1 | 1/1 | 1/1 |
| ct02_prepared | sin clasificar | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 5.4 / 5.4 | 7.1 / 7.1 | 1/1 | 1/1 |
| ct05 | CT-05 | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | 15.2 / 15.2 | 17.3 / 17.3 | 1/1 | 1/1 |

- `ct01`: hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `ct02`: hallazgos: CT-02 1; sin hallazgo: 0; evento: CT-02 1
- `ct02_prepared`: sentencias preparadas: el log no trae los valores; hallazgos: sin clasificar 1; sin hallazgo: 0; evento: sin clasificar 1

## Controles (sin hallazgo esperado)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct05_raiz_corta | ninguno | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |
| ct05_una_espera | ninguno | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |

- `ct05_raiz_corta`: la raiz no llega a 30 s
- `ct05_una_espera`: una sola sesion bloqueada (la regla pide >= 2); espera sin abrazo

## Casos límite

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01_prepared | sin clasificar | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 5.9 / 5.9 | 12.6 / 12.6 | 1/1 | 1/1 |
| ct01_tres | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 7.1 / 7.1 | 11.4 / 11.4 | 1/1 | 1/1 |
| rafaga | informativo | 2 (2) | 2 (2) | 2 con hallazgo correcto, 0 mal | 11.6 / 11.6 | — | — | — |
| otra_base | ninguno | 1 (0) | 0 (0) | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |
| desborde_log | informativo | 1 (1) | 0 (0) | informativo | — | — | — | — |
| terse | informativo | 1 (1) | 1 (1) | informativo | 2.5 / 2.5 | 3.3 / 3.3 | 1/1 | — |
| pausa | ninguno | 1 (1) | 0 (0) | **1/1** (100.0 %) · IC 95 % 21–100 % | — | — | — | — |

- `ct01_prepared`: sentencias preparadas: el log no trae los valores; hallazgos: sin clasificar 1; sin hallazgo: 0; evento: sin clasificar 1
- `ct01_tres`: ciclo de 3 transacciones; hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `rafaga`: dos abrazos a la vez, en el mismo ciclo; por repetición: 2/2 capt., 2 con detalle, 2 hallazgos
- `otra_base`: abrazo en otra base del mismo servidor: no es de la base monitoreada; sin hallazgo: 1
- `desborde_log`: ~400 KB/s de log durante 30 s (> 2 MB por ciclo); `LOG_WINDOW_OVERRUN` en pipeline_health.issues: sí
- `terse`: log_error_verbosity = terse; hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1; `DEADLOCK_REPORTS_PARTIAL` en pipeline_health.issues: sí
- `pausa`: monitoreo pausado 135 s (> 120 s de ventana)

## Positivos con carga de fondo (continuous_load.sh)

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 10.9 / 10.9 | 13.2 / 13.2 | 1/1 | 1/1 |
| ct02 | CT-02 | 1 (1) | 1 (1) | **1/1** (100.0 %) · IC 95 % 21–100 % | 0.0 / 0.0 | 2.5 / 2.5 | 1/1 | 1/1 |
| ct05 | CT-05 | — (0) | — | **1/1** (100.0 %) · IC 95 % 21–100 % | 19.7 / 19.7 | 22.8 / 22.8 | 1/1 | 1/1 |

- `ct01`: hallazgos: CT-01 1; sin hallazgo: 0; evento: CT-01 1
- `ct02`: hallazgos: CT-02 1; sin hallazgo: 0; evento: CT-02 1

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 3 | 3 | 3 | 3 | 0 | 0 | 0 | 16 | 0 | 0 | 13 | 0.7 |
| control | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 0 | 13 | 0.5 |
| limite | 7 | 5 | 7 | 5 | 2 | 0 | 0 | 29 | 3 | 1 | 163 | 575.6 |
| carga | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 16 | 0 | 0 | 13 | 4.1 |

