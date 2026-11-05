# Cobertura del historial de MySQL para AP-08

Consultas cada 5 s durante 60 s por carga.

| Carga | 1 de cada | Ruido | Consultas | Sample en history | Sample en history_long | Alguna ejecucion en history | Ejecuciones en history (mediana) | Edad del sample |
|---|---|---|---|---|---|---|---|---|
| card01 columnas correlacionadas a = b = c | 1 | no | 10 | 10 % | 100 % | 100 % | 40.0 | 21.4 s |
| card01 columnas correlacionadas a = b = c | 1 | sí | 9 | 44 % | 0 % | 100 % | 40 | 15.0 s |
| card04 igualdad sobre indice, distribucion uniforme | 1 | no | 10 | 0 % | 10 % | 100 % | 40.0 | 26.6 s |
| card04 igualdad sobre indice, distribucion uniforme | 1 | sí | 9 | 0 % | 0 % | 100 % | 40 | 10.7 s |
| card07 indice sesgado, 1 % valor frecuente (500.000 filas) y 99 % raros | 1 | no | 10 | 0 % | 20 % | 100 % | 40.0 | 25.9 s |
| card07 indice sesgado, 1 % valor frecuente (500.000 filas) y 99 % raros | 1 | sí | 9 | 0 % | 0 % | 100 % | 40 | 8.4 s |
| card01 columnas correlacionadas a = b = c | 50 | no | 10 | 0 % | 80 % | 30 % | 0.0 | 16.0 s |
| card01 columnas correlacionadas a = b = c | 50 | sí | 9 | 0 % | 0 % | 22 % | 0 | 15.9 s |
| card04 igualdad sobre indice, distribucion uniforme | 50 | no | 10 | 0 % | 0 % | 20 % | 0.0 | 27.3 s |
| card04 igualdad sobre indice, distribucion uniforme | 50 | sí | 9 | 0 % | 0 % | 56 % | 1 | 24.5 s |
| card04 igualdad sobre indice, distribucion uniforme | 200 | no | 10 | 0 % | 10 % | 10 % | 0.0 | 25.4 s |
| card04 igualdad sobre indice, distribucion uniforme | 200 | sí | 9 | 0 % | 0 % | 11 % | 0 | 22.0 s |
