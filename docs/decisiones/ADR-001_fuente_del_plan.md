# ADR-001. Fuente del plan de ejecución de las sentencias candidatas

- **Estado:** aceptada.
- **Fecha:** 12 de octubre de 2026.
- **Evaluación completa:** Segundo Informe, secciones 9.1 a 9.4 (Tabla 10).
- **Evidencia:** `telemetry_pipeline/logs/eval_opcion_c/` y las corridas `telemetry_pipeline/logs/overhead_v2_20261012_123930` (PostgreSQL) y `overhead_v2_20261012_112108` (MySQL).

## Contexto

Para diagnosticar una consulta lenta, los detectores necesitan su plan de ejecución: qué tablas recorre completas, qué índice usa y cuántas filas espera el motor. Las estadísticas por sentencia no traen el plan, y en PostgreSQL tampoco conservan los valores reales de la consulta, porque pg_stat_statements los reemplaza por `$1`, `$2`, etc.

La decisión es de dónde sale el plan de cada sentencia candidata. Está condicionada por tres restricciones del proyecto: el rol de monitoreo es de solo lectura (no puede ejecutar `EXPLAIN ANALYZE` ni explicar sentencias de modificación), ningún literal puede persistirse y el sobrecosto sobre la carga observada debe quedar por debajo del 5 % (M5). Como esta decisión afecta a la base observada, su evaluación incluye el sobrecosto medido en el banco de pruebas.

## Alternativas consideradas

- **A. Actividad del momento** (`pg_stat_activity` / `events_statements_current`) más `EXPLAIN`. Solo encuentra la consulta si sigue en ejecución al revisar. Encontró el 0 % de las consultas en la Prueba 1.
- **B. Registro de sentencias del servidor** (registro de sentencias de PostgreSQL / *slow log* de MySQL) más `EXPLAIN`. Encontró el 100 %, pero exige registrar todo el tráfico del servidor y pierde las consultas que quedan enterradas fuera de la porción leída (Prueba 2).
- **C. Lo que el motor ya guarda de cada consulta.** En PostgreSQL, el plan que auto_explain escribe en el registro del servidor, leído mediante `querylens.log_files()` y `querylens.read_log()`, con `EXPLAIN (GENERIC_PLAN)` como respaldo. En MySQL, `EXPLAIN FORMAT=JSON` sobre `QUERY_SAMPLE_TEXT` de `events_statements_summary_by_digest`.

## Decisión

Se adopta la **alternativa C**, con la mayor puntuación ponderada (4,3 frente a 3,1 de B y 1,8 de A). Los motivos son los siguientes:

- Con datos sesgados, obtuvo el plan real en 18 de 18 consultas en cada motor, frente a 9 de 18 del plan genérico en PostgreSQL (Prueba 3).
- No depende del instante de la revisión ni de registrar todas las sentencias.
- En MySQL no agrega ninguna fuente: la muestra viene de la tabla que ya se recolecta.
- El costo de la instrumentación no se distinguió del ruido en ninguno de los dos motores (aumento de p95 de +0,42 % [−2,24; +2,15] en PostgreSQL y de −0,50 % [−2,34; +1,32] en MySQL).
- Respeta la anonimización: el texto con valores reales solo se usa dentro del recolector, y los valores de los predicados del plan se redactan.

## Consecuencias

- **Positivas:** el plan refleja una ejecución real. Si falla la lectura del registro, PostgreSQL degrada al plan genérico en lugar de quedarse sin plan, y el problema se informa como `AUTO_EXPLAIN_LOG_UNAVAILABLE`.
- **Límites medidos:**
  - En PostgreSQL, las consultas de menos de 50 ms reciben el plan genérico, y el plan real se pierde cuando después de la consulta se escriben más de 2 MiB de registro (Prueba 5).
  - En MySQL, las muestras de más de 1 024 bytes llegan cortadas y quedan sin plan (`QUERY_TEXT_TRUNCATED`), igual que las consultas nuevas cuando la tabla de digests está llena.
- **Requisito operativo:** auto_explain, el registro en formato JSON y las dos funciones de lectura deben instalarse con un superusuario, según `telemetry_pipeline/references/006_instalacion_auto_explain.md`. Por eso esta fuente no está disponible en servicios gestionados (Amazon RDS, Aurora, Cloud SQL, Azure), donde se usa el plan genérico.
- **Alternativas descartadas:** A y B se retiraron del código. Sus pruebas se conservan como evidencia de la iteración en `telemetry_pipeline/references/005_evaluacion_alternativas_query_real.md`.
