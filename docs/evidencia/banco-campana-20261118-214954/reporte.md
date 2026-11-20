# Banco de pruebas — corrida `banco-campana-20261118-214954`

Inicio 2026-11-19 02:49:54.378594+00 · fin 2026-11-19 19:57:11.595509+00 · 10 repeticiones por escenario · 10 corridas de control de 30 min por motor · carga base: sysbench oltp_read_write, 4 hilos, 50 tx/s · commit `d1cf211` · estadisticas de sentencias en cero al empezar cada motor

## Veredicto (Tabla 7)

| Metrica | Umbral | Combinaciones que cumplen | Veredicto |
|---|---|---|---|
| M1 exhaustividad | >= 0.90 en cada combinacion | 24/24 | CUMPLE |
| M2 precision | >= 0.90 en cada combinacion | 24/24 | CUMPLE |
| M3 contencion | >= 0.95 miembros, >= 0.90 clasificacion | 8/8 | CUMPLE |
| M4 latencia | AP <= 120 s; CT-01 a CT-04 <= 15 s; CT-05 <= 45 s | 24/24 | CUMPLE |
| M7 completitud | 100 % de los problemas con causa, evidencia enlazada a su instantanea, recomendacion y riesgo | 1174/1174 problemas | CUMPLE |

Combinaciones con inyecciones: 24/24; con las 10 repeticiones completas: 24/24. Una combinacion sin inyecciones o sin hallazgos que medir no cumple.

## postgres

Corridas de control: 10 (300.1 min en total).

| Patologia | Inyecciones | VP | FN | FP | M1 (IC 95 %) | M2 | M4 mediana / max (s) | M4 en ventanas | M4 dentro del limite |
|---|---|---|---|---|---|---|---|---|---|
| AP-01 degradacion respecto a la linea base | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 18.9 / 22.1 | 1.9 / 2.2 | 10/10 de <= 120 s cumple |
| AP-02 escaneo completo evitable | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.6 / 10.1 | 1.0 / 1.0 | 10/10 de <= 120 s cumple |
| AP-03 predicado no sargable | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.4 / 10.2 | 0.9 / 1.0 | 10/10 de <= 120 s cumple |
| AP-04 indice ausente | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.6 / 10.1 | 1.0 / 1.0 | 10/10 de <= 120 s cumple |
| AP-05 indice no utilizado o redundante | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.7 / 20.1 | 1.0 / 2.0 | 10/10 de <= 120 s cumple |
| AP-06 patron N+1 | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.1 / 9.7 | 0.9 / 1.0 | 10/10 de <= 120 s cumple |
| AP-07 vertido a disco | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.4 / 9.9 | 0.9 / 1.0 | 10/10 de <= 120 s cumple |
| AP-08 error grave de estimacion de cardinalidad | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 30.4 / 40.5 | 3.0 / 4.0 | 10/10 de <= 120 s cumple |
| CT-01 abrazo mortal por orden inconsistente | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 5.9 / 10.1 | 0.6 / 1.0 | 10/10 de <= 15 s cumple |
| CT-02 abrazo mortal por conversion de modo | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 6.6 / 10.1 | 0.7 / 1.0 | 10/10 de <= 15 s cumple |
| CT-05 cascada por transaccion larga | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 16.1 / 20.3 | 1.6 / 2.0 | 10/10 de <= 45 s cumple |

M3, exactitud del analisis de contencion:

| Patologia | Detectados | Miembros y recursos correctos | Patron correcto | |
|---|---|---|---|---|
| CT-01 abrazo mortal por orden inconsistente | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-02 abrazo mortal por conversion de modo | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-05 cascada por transaccion larga | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |

## mysql

Corridas de control: 10 (300.1 min en total).

| Patologia | Inyecciones | VP | FN | FP | M1 (IC 95 %) | M2 | M4 mediana / max (s) | M4 en ventanas | M4 dentro del limite |
|---|---|---|---|---|---|---|---|---|---|
| AP-01 degradacion respecto a la linea base | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 16.6 / 18.2 | 1.7 / 1.8 | 10/10 de <= 120 s cumple |
| AP-02 escaneo completo evitable | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.2 / 19.7 | 0.9 / 2.0 | 10/10 de <= 120 s cumple |
| AP-03 predicado no sargable | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 8.9 / 29.2 | 0.9 / 2.9 | 10/10 de <= 120 s cumple |
| AP-04 indice ausente | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.2 / 19.7 | 0.9 / 2.0 | 10/10 de <= 120 s cumple |
| AP-05 indice no utilizado o redundante | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.1 / 9.8 | 0.9 / 1.0 | 10/10 de <= 120 s cumple |
| AP-06 patron N+1 | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.2 / 18.9 | 0.9 / 1.9 | 10/10 de <= 120 s cumple |
| AP-07 vertido a disco | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 9.0 / 19.5 | 0.9 / 1.9 | 10/10 de <= 120 s cumple |
| AP-08 error grave de estimacion de cardinalidad | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 8.9 / 39.3 | 0.9 / 3.9 | 10/10 de <= 120 s cumple |
| CT-01 abrazo mortal por orden inconsistente | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 6.0 / 9.3 | 0.6 / 0.9 | 10/10 de <= 15 s cumple |
| CT-02 abrazo mortal por conversion de modo | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 4.1 / 8.9 | 0.4 / 0.9 | 10/10 de <= 15 s cumple |
| CT-03 abrazo mortal por contencion de rango | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 6.9 / 10.0 | 0.7 / 1.0 | 10/10 de <= 15 s cumple |
| CT-04 ampliacion de bloqueos por ausencia de indice | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 5.9 / 10.2 | 0.6 / 1.0 | 10/10 de <= 15 s cumple |
| CT-05 cascada por transaccion larga | 10 | 10 | 0 | 0 | 1.00 (0.72–1.00) cumple | 1.00 cumple | 15.6 / 20.1 | 1.6 / 2.0 | 10/10 de <= 45 s cumple |

M3, exactitud del analisis de contencion:

| Patologia | Detectados | Miembros y recursos correctos | Patron correcto | |
|---|---|---|---|---|
| CT-01 abrazo mortal por orden inconsistente | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-02 abrazo mortal por conversion de modo | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-03 abrazo mortal por contencion de rango | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-04 ampliacion de bloqueos por ausencia de indice | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |
| CT-05 cascada por transaccion larga | 10 | 1.00 (10/10) | 1.00 (10/10) | cumple |

## M7, lista de chequeo

1174 problemas en todas las corridas; faltantes por punto: explicacion 0, evidencia 0, recomendacion 0, riesgo 0, enlace_a_instantanea 0.

## Falsos negativos

Ninguno.

## Falsos positivos (hallazgos que ningun manifiesto declara)

Ninguno.

## Hallazgos declarados en los manifiestos (no cuentan como VP ni como FP)

| Tipo | Escenario | Motor | Declaracion | Corridas en las que salio |
|---|---|---|---|---|
| colateral | baseline_degradation | mysql | AP-02: sin el indice, WHERE k = ? recorre toda la tabla para devolver ~10 filas | 10 |
| colateral | baseline_degradation | mysql | AP-04: sin el indice, k se filtra sin un indice que lo respalde | 10 |
| colateral | baseline_degradation | mysql | AP-08: MySQL estima un 10 % fijo para k = ? sin indice ni histograma (~100.000 filas frente a ~10) | 10 |
| colateral | baseline_degradation | postgres | AP-02: sin el indice, WHERE k = ? recorre toda la tabla para devolver ~10 filas | 10 |
| colateral | baseline_degradation | postgres | AP-04: sin el indice, k se filtra sin un indice que lo respalde | 10 |
| colateral | cardinality_misestimate | mysql | AP-02: a, b y c no tienen indice y la consulta devuelve el 10 % de la tabla, justo el limite de la regla | 6 |
| colateral | cardinality_misestimate | mysql | AP-04: igual, sin indice sobre a, b ni c | 6 |
| colateral | cardinality_misestimate | postgres | AP-02: a, b y c no tienen indice y la consulta devuelve el 10 % de la tabla, justo el limite de la regla | 10 |
| colateral | cardinality_misestimate | postgres | AP-04: igual, sin indice sobre a, b ni c | 10 |
| colateral | missing_index | mysql | AP-08: MySQL estima un 10 % fijo para c = ? sin indice ni histograma (~100.000 filas frente a ~10) | 10 |
| colateral | missing_index | postgres | AP-05: ql_scn_k, recien creado, no lo lee la carga del escenario | 10 |
| colateral | non_sargable_predicate | mysql | AP-02: k + 0 = ? no puede usar el indice y recorre toda la tabla para devolver ~10 filas (misma base de escaneo completo que AP-03) | 10 |
| colateral | non_sargable_predicate | mysql | AP-08: ningun motor estima bien una expresion sobre la columna (Postgres 0,5 % por defecto, MySQL casi toda la tabla) frente a ~10 filas reales | 10 |
| colateral | non_sargable_predicate | postgres | AP-02: k + 0 = ? no puede usar el indice y recorre toda la tabla para devolver ~10 filas (misma base de escaneo completo que AP-03) | 10 |
| colateral | non_sargable_predicate | postgres | AP-05: ql_scn_k, recien creado, no lo lee la carga del escenario | 10 |
| colateral | non_sargable_predicate | postgres | AP-08: ningun motor estima bien una expresion sobre la columna (Postgres 0,5 % por defecto, MySQL casi toda la tabla) frente a ~10 filas reales | 10 |
| colateral | unused_index | mysql | AP-01: la inyeccion compite con sysbench por CPU y bloqueos y degrada sus sentencias | 52 |
| linea_base | baseline_degradation | mysql | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 8 |
| linea_base | baseline_degradation | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 82 |
| linea_base | cardinality_misestimate | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 89 |
| linea_base | control | mysql | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 20 |
| linea_base | control | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 94 |
| linea_base | ct | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 10 |
| linea_base | disk_spill | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 93 |
| linea_base | missing_index | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 78 |
| linea_base | n_plus_one | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 100 |
| linea_base | non_sargable_predicate | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 85 |
| linea_base | unused_index | postgres | AP-05: indice k_N de sysbench, que oltp_read_write no usa para leer | 65 |
