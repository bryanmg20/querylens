# Carga realista en MySQL: candidatos emparejados con su ejecucion

## history_size = 10 | relleno: 200 tipos, 85% de las sentencias

62 snapshots; por snapshot (mediana): 10.0 candidatos, 5.0 emparejados. Memoria de events_statements_history: 16.2 MB.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.57 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.63 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.74 ms | 62 | 57 (92 %) | 5 | 0 |
| real04 | 3.59 ms | 62 | 50 (81 %) | 12 | 0 |
| real05 | 3.21 ms | 62 | 44 (71 %) | 18 | 0 |
| real06 | 2274.08 ms | 62 | 10 (16 %) | 52 | 0 |
| real07 | 2577.71 ms | 62 | 12 (19 %) | 50 | 0 |
| real08 | 2298.51 ms | 62 | 7 (11 %) | 55 | 0 |
| real09 | 2988.22 ms | 62 | 4 (6 %) | 58 | 0 |
| real10 | 3338.87 ms | 62 | 1 (2 %) | 61 | 0 |

## history_size = 50 | relleno: 200 tipos, 85% de las sentencias

62 snapshots; por snapshot (mediana): 10.0 candidatos, 7.0 emparejados. Memoria de events_statements_history: 81.0 MB.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.69 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.74 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.87 ms | 62 | 60 (97 %) | 2 | 0 |
| real04 | 3.81 ms | 62 | 60 (97 %) | 2 | 0 |
| real05 | 3.19 ms | 62 | 60 (97 %) | 2 | 0 |
| real06 | 2335.54 ms | 62 | 41 (66 %) | 21 | 0 |
| real07 | 2662.31 ms | 62 | 35 (56 %) | 27 | 0 |
| real08 | 2397.39 ms | 62 | 19 (31 %) | 43 | 0 |
| real09 | 3047.25 ms | 62 | 18 (29 %) | 44 | 0 |
| real10 | 3614.77 ms | 62 | 2 (3 %) | 60 | 0 |

## history_size = 100 | relleno: 200 tipos, 85% de las sentencias

63 snapshots; por snapshot (mediana): 10 candidatos, 8 emparejados. Memoria de events_statements_history: 161.9 MB.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.5 ms | 63 | 61 (97 %) | 2 | 0 |
| real02 | 1.57 ms | 63 | 61 (97 %) | 2 | 0 |
| real03 | 1.6 ms | 63 | 61 (97 %) | 2 | 0 |
| real04 | 3.64 ms | 63 | 61 (97 %) | 2 | 0 |
| real05 | 2.97 ms | 63 | 61 (97 %) | 2 | 0 |
| real06 | 2157.32 ms | 63 | 57 (90 %) | 6 | 0 |
| real07 | 2430.01 ms | 63 | 41 (65 %) | 22 | 0 |
| real08 | 2163.48 ms | 63 | 42 (67 %) | 21 | 0 |
| real09 | 2696.2 ms | 63 | 18 (29 %) | 45 | 0 |
| real10 | 3229.62 ms | 63 | 19 (30 %) | 44 | 0 |
