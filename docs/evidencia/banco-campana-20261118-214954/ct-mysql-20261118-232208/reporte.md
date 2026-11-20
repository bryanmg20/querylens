# Contención CT-01 a CT-05 — mysql 8.0.46

Corrida `ct-mysql-20261118-232208` · base `db_915a8ca5` (registrada con `querylens add`) · 10 repeticiones por caso · inicio 2026-11-19 04:22:13.804931+00 · fin 2026-11-19 04:47:38.248086+00

Latencia: `detection_delay_s` del hallazgo (snapshot − abrazo, reloj del motor); en CT-05, antigüedad de la raíz en el snapshot del hallazgo − 30 s. *Guardado*: `detectado_en` − el mismo instante, con la cola y el detector. M4 se cuenta con *Guardado*. Valores mediana / máximo en s.

## Positivos

| Caso | Esperado | Abrazos (motor) | Capturados (con detalle) | Correctos | Latencia | Guardado | M4 | Evidencia |
|---|---|---|---|---|---|---|---|---|
| ct01 | CT-01 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.8 / 9.1 | 6.0 / 9.3 | 10/10 | 10/10 |
| ct02 | CT-02 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 3.8 / 8.6 | 4.1 / 8.9 | 10/10 | 10/10 |
| ct03 | CT-03 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 6.6 / 9.6 | 6.9 / 10.0 | 10/10 | 10/10 |
| ct04 | CT-04 | 10 (10) | 10 (10) | **10/10** (100.0 %) · IC 95 % 72–100 % | 5.6 / 9.8 | 5.9 / 10.2 | 10/10 | 10/10 |
| ct05 | CT-05 | — (0) | — | **10/10** (100.0 %) · IC 95 % 72–100 % | 15.3 / 19.8 | 15.6 / 20.1 | 10/10 | 10/10 |

- `ct01`: hallazgos: CT-01 10; sin hallazgo: 0; evento: CT-01 10
- `ct02`: hallazgos: CT-02 10; sin hallazgo: 0; evento: CT-02 10
- `ct03`: hallazgos: CT-03 10; sin hallazgo: 0; evento: CT-03 10
- `ct04`: hallazgos: CT-04 10; sin hallazgo: 0; evento: CT-04 10

## Calidad de captura según el CLI (`querylens report` → `calidad_deadlocks`)

Por fase: lo que contó el motor (lectura directa del contador antes y después) contra lo que dice el reporte del CLI para la misma ventana.

| Fase | Motor contó | Capturados (verdad) | CLI hubo | CLI capturados | CLI perdidos | De más | Sin detalle | Ciclos | Con desborde | Huecos > 120 s | Hueco máx. s | Log KB/s máx. |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positivo | 40 | 40 | 40 | 40 | 0 | 0 | 0 | 149 | 0 | 0 | 10 | None |

