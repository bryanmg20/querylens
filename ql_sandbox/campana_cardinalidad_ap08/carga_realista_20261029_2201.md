# Carga realista en MySQL: candidatos emparejados con su ejecucion

## history_size = 10 | relleno: 200 tipos, 85% de las sentencias

62 snapshots; por snapshot (mediana): 10.0 candidatos, 5.0 emparejados.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.5 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.54 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.61 ms | 62 | 59 (95 %) | 3 | 0 |
| real04 | 3.56 ms | 62 | 52 (84 %) | 10 | 0 |
| real05 | 2.72 ms | 62 | 44 (71 %) | 18 | 0 |
| real06 | 2208.72 ms | 62 | 6 (10 %) | 56 | 0 |
| real07 | 2497.63 ms | 62 | 11 (18 %) | 51 | 0 |
| real08 | 2209.66 ms | 62 | 3 (5 %) | 59 | 0 |
| real09 | 2946.45 ms | 62 | 3 (5 %) | 59 | 0 |
| real10 | 3320.87 ms | 62 | 1 (2 %) | 61 | 0 |

## history_size = 100 | relleno: 200 tipos, 85% de las sentencias

62 snapshots; por snapshot (mediana): 10.0 candidatos, 8.0 emparejados.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.5 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.54 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.59 ms | 62 | 60 (97 %) | 2 | 0 |
| real04 | 3.67 ms | 62 | 60 (97 %) | 2 | 0 |
| real05 | 2.86 ms | 62 | 60 (97 %) | 2 | 0 |
| real06 | 2228.31 ms | 62 | 52 (84 %) | 10 | 0 |
| real07 | 2526.15 ms | 62 | 43 (69 %) | 19 | 0 |
| real08 | 2232.24 ms | 62 | 31 (50 %) | 31 | 0 |
| real09 | 2833.4 ms | 62 | 29 (47 %) | 33 | 0 |
| real10 | 3375.23 ms | 62 | 8 (13 %) | 54 | 0 |
