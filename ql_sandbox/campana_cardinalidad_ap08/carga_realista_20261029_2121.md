# Carga realista en MySQL: candidatos emparejados con su ejecucion

## history_size = 10

62 snapshots; por snapshot (mediana): 10.0 candidatos, 7.0 emparejados.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.65 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.82 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.84 ms | 62 | 60 (97 %) | 2 | 0 |
| real04 | 3.82 ms | 62 | 60 (97 %) | 2 | 0 |
| real05 | 3.17 ms | 62 | 60 (97 %) | 2 | 0 |
| real06 | 2114.19 ms | 62 | 47 (76 %) | 15 | 0 |
| real07 | 2432.46 ms | 62 | 39 (63 %) | 23 | 0 |
| real08 | 2147.48 ms | 62 | 27 (44 %) | 35 | 0 |
| real09 | 2761.48 ms | 62 | 16 (26 %) | 46 | 0 |
| real10 | 3162.08 ms | 62 | 13 (21 %) | 49 | 0 |

## history_size = 100

62 snapshots; por snapshot (mediana): 10.0 candidatos, 10.0 emparejados.

| Consulta | Duracion media | Veces en top impact | Emparejada (history) | Sample | Sin plan |
|---|---|---|---|---|---|
| real01 | 1.6 ms | 62 | 60 (97 %) | 2 | 0 |
| real02 | 1.77 ms | 62 | 60 (97 %) | 2 | 0 |
| real03 | 1.81 ms | 62 | 60 (97 %) | 2 | 0 |
| real04 | 3.76 ms | 62 | 60 (97 %) | 2 | 0 |
| real05 | 3.0 ms | 62 | 60 (97 %) | 2 | 0 |
| real06 | 2137.7 ms | 62 | 60 (97 %) | 2 | 0 |
| real07 | 2434.59 ms | 62 | 60 (97 %) | 2 | 0 |
| real08 | 2189.51 ms | 62 | 60 (97 %) | 2 | 0 |
| real09 | 2804.33 ms | 62 | 59 (95 %) | 3 | 0 |
| real10 | 3250.27 ms | 62 | 47 (76 %) | 15 | 0 |
