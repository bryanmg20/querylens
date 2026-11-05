# Costo de auto_explain.log_analyze en Postgres

4 rondas de 30 s por carga y modo, 4 sesiones, modos alternados.
El log trae `Actual Rows` con analyze_sin_tiempo: True

| Carga | Modo | Por segundo (mediana) | vs off | Latencia (mediana) |
|---|---|---|---|---|
| oltp | off | 788.1 transacciones | +0.0 % | 7.11 ms (p95) |
| oltp | analyze_sin_tiempo | 812.4 transacciones | +3.1 % | 6.27 ms (p95) |
| oltp | analyze_con_tiempo | 802.6 transacciones | +1.9 % | 6.49 ms (p95) |
| scan | off | 31.9 consultas | +0.0 % | 113.56 ms (media) |
| scan | analyze_sin_tiempo | 32.7 consultas | +2.3 % | 110.67 ms (media) |
| scan | analyze_con_tiempo | 31.7 consultas | -0.7 % | 114.35 ms (media) |
| join | off | 31.4 consultas | +0.0 % | 115.57 ms (media) |
| join | analyze_sin_tiempo | 30.8 consultas | -1.8 % | 117.65 ms (media) |
| join | analyze_con_tiempo | 29.5 consultas | -6.0 % | 123.66 ms (media) |
