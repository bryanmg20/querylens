# Contención CT-01 a CT-05 — postgres 17.11 (Debian 17.11-1.pgdg13+2)

Corrida `ct-postgres-20261119-074239` · base `db_39389b4c` (registrada con `querylens add`) · 10 repeticiones por caso · inicio 2026-11-19 12:42:47.819517+00 · fin 2026-11-19 13:02:06.060086+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. M4 se cuenta con *Guardado*. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.2 / 9.3 | 5.9 / 10.1 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.9 / 9.3 | 6.6 / 10.1 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 15.4 / 19.6 | 16.1 / 20.3 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 20 | 20 | 20 | 20 | 0 | 0 | 0 | 113 | 0 | 0 | 10 | 1.1 |

