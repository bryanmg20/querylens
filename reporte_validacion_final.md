# Reporte de validación final — rama `feature/validacion-final`

Revisión del 16 de noviembre de 2026: Informe final, auditoría de las 8 issues asignadas y pruebas desde cero. No se hizo ningún commit ni push.

> **Nota sobre esta sección.** La sección 1 original de este reporte se sobrescribió por error con el resumen del chat, y se perdió. Se reconstruyó comparando, línea por línea, [InformeFinal.md](InformeFinal.md) con [SegundoInforme.md](SegundoInforme.md). Las secciones 2 a 5 son las originales.

## 1. Actualización del Informe final e información faltante

### 1.1 Cómo se armó el Informe final

- La plantilla [InformeFinal.md](InformeFinal.md) solo traía los encabezados de las secciones 1 a 16 y un texto guía en cada una.
- Esos textos guía se dejaron tal cual. Debajo de cada uno se puso el contenido del `SegundoInforme`:
  - sin cambios, donde sigue vigente;
  - corregido, donde el código actual dice otra cosa.
- La plantilla reorganiza la sección 10 en 10.1.1 (descripción general), 10.1.2.1 (componentes), 10.1.2.2 (interacción) y 10.1.2.3 (comportamiento). El contenido de las secciones 10.1 a 10.4 del segundo informe se repartió en esa estructura.
- La sección 9 (Evaluación de alternativas) no se copió: la plantilla solo trae un enlace al segundo informe. Se corrigió ese enlace, que apuntaba a la sección 8.

### 1.2 Secciones actualizadas según el código

| Sección | Qué cambió |
|---|---|
| §1 Introducción, párrafo del estado del trabajo | Recolección completa, con los informes de abrazo mortal y el contador de abrazos. Los 8 detectores y el módulo de contención (CT-01 abrazo mortal por orden inconsistente a CT-05 cascada por transacción larga) se ejecutan sobre cada instantánea. Se agregan la línea de comandos, el servicio de ventanas y el banco de un solo comando. |
| §2.4 Observabilidad | La ventana de evaluación pasa de 60 s a 10 s, el tiempo entre dos ciclos de recolección. |
| §3.2 Supuestos | Requisitos del motor: en MySQL, `events_statements_history` con al menos 100 sentencias por hilo; en PostgreSQL, auto_explain con `log_analyze` activo y `log_timing` desactivado. |
| §3.3 Alcance, «Incluye» | **Recolección:** en MySQL, el plan sale de la ejecución más lenta que conserva `events_statements_history`. **Priorización:** CT-05 cascada por transacción larga suma la espera de todas las sesiones afectadas; los abrazos mortales cuentan con impacto cero. **Umbrales:** se congelan en `detection_engine/umbrales_congelados.json`, con su prueba; la ventana es de 10 s. |
| §3.3, Tabla 2 | Las **8 filas** (AP-01 degradación respecto a la línea base a AP-08 error grave de estimación de cardinalidad) se ajustaron a las reglas que implementa el código, con ventanas de 10 s. |
| §3.3, Tabla 3 | Las filas no cambiaron. Se agregó un párrafo sobre el abrazo mortal sin clasificar: código CT sin número, que no cuenta como CT-01 abrazo mortal por orden inconsistente ni como CT-02 abrazo mortal por conversión de modo. |
| §6.1 Contratos | **Instantánea:** agrega la fuente del plan, las filas reales de la ejecución, los informes de abrazo mortal y el estado de la recolección. **Hallazgo:** se guarda una vez por problema, con una clave estable, y cada detección agrega su hora; todavía no guarda el identificador de su instantánea. **Estado de evaluación:** ahora incluye los motivos (un código del estado de la recolección o una condición de la propia regla). |
| §6.2 Priorización | El mismo criterio de impacto que en §3.3, más el desempate: severidad, después código y después clave del problema. |
| §6.4 Concordancia | **AP-01 degradación respecto a la línea base:** pasa a 60 ventanas previas con al menos 5 ejecuciones (antes, 10 con 30). **AP-07 vertido a disco:** agrega las pasadas de mezcla de ordenamientos en MySQL. **AP-08 error grave de estimación de cardinalidad:** compara con las filas reales de la misma ejecución y no se evalúa con plan genérico ni con muestra de MySQL. |
| §7.2 Iteraciones | Solo se ajustó la redacción («hasta el cierre del segundo informe»). |
| §7.3 Validación | M4 conserva el límite de 120 s y se explica su relación con el ciclo de 10 s. Se agregan el estado al cierre del segundo informe y el banco `ql_sandbox/banco`. |
| §7.4 Hitos | Se agrega la columna «Estado al 16 de noviembre de 2026» para los hitos H1 a H6. |
| §8.1 Funcionales | RF-03, RF-06, RF-07, RF-08, RF-09, RF-11 y RF-13 pasan a «Implementado». Se agregan **RF-14** (encender o pausar el monitoreo y programar ventanas) y **RF-15** (reporte por rango de fechas o por sesión). |
| §8.2 No funcionales | **RNF-01:** dice «M5 inconcluso con la recolección completa»; hay que actualizarlo a CUMPLE (17 de noviembre, ver la issue #61). **RNF-06:** «Parcial», porque no hay enlace a la instantánea. **RNF-10:** agrega las pruebas por regla. |
| §10 Arquitectura | De seis componentes se pasa a nueve: se agregan el proxy inverso (nginx), la línea de comandos y el servicio de ventanas. Se actualizaron las **Figuras 1 a 5**: arquitectura, interacción, registro con «Iniciar monitoreo», ciclo con los informes de abrazo mortal y consulta de diagnósticos por sesión con el estado de evaluación. |
| §11 Implementación | Fecha al 16 de noviembre. Se agregan nginx y Bash/psql/jq al stack. Los componentes reflejan los 8 detectores integrados y la contención. El banco reúne los 22 escenarios. |
| §12 Despliegue | Se agregan el proxy inverso, el servicio de ventanas, el puerto 8080 en 127.0.0.1, los roles por servicio, la carpeta `reports/` y los requisitos de auto_explain y de MySQL. |
| §13.1 Pruebas por componentes | El pipeline pasa de 906 a 1225 pruebas (16 de noviembre). Se agrega el motor de detección, con 472 pruebas y un archivo por patología. |
| §13.2 Pruebas de integración | El párrafo final cubre los 8 anti-patrones, las 5 patologías de contención y el banco de un solo comando. |

### 1.3 Lo que se mantuvo intacto

- **Sin cambios respecto al segundo informe:**
  - §2.1 a §2.3, §2.5 y §2.6 del marco conceptual;
  - §3.1 y las restricciones de §3.2;
  - §4 Objetivos y §5 Estado del arte;
  - §6.3 y el resto de la tabla de §6.4;
  - §7.1;
  - los umbrales de la Tabla 7;
  - las filas de la Tabla 3;
  - §13.3 (plan de usabilidad);
  - §16 Referencias.
- **Partes del segundo informe que no pasaron al Informe final:**
  - el Resumen;
  - el párrafo inicial de §3.3 («Respecto al planteamiento del primer informe…»);
  - el detalle de §9, reemplazado por el enlace;
  - §11.4 Pendientes;
  - §14 Resultados parciales;
  - §15 Plan de cierre.

  Eran contenido de una entrega intermedia; las secciones equivalentes del Informe final quedaron con solo el texto guía (ver 1.4).

### 1.4 Información faltante (no se rellenó)

1. **Resumen / Abstract:** solo tiene el texto guía. Depende de los resultados finales.
2. **§7.2 Iteraciones:** solo llega hasta el 12 de octubre. Faltan las iteraciones posteriores.
3. **§12 Despliegue:** falta el **diagrama de despliegue** que exige la plantilla. Solo hay texto.
4. **§13 Validación:** faltan los resultados de M1 a M4 y M7 de la campaña final (#62). El smoke de este reporte tiene una sola repetición y no los reemplaza.
5. **§13.2, M5:** sigue con la medición del 12 de octubre. Hay que pasar a §13.2, §7.4 (H2) y §8.2 (RNF-01) el resultado del 17 de noviembre (CUMPLE en los dos motores con la recolección completa; ver la issue #61). Hoy esas secciones todavía dicen «inconclusa».
6. **§13.2, M6:** sigue con la ejecución del 12 de octubre (10 tablas). No refleja la repetición del 16 de noviembre (15 tablas, 0 apariciones en los dos motores).
7. **§13.3 Pruebas de usabilidad:** no se han realizado. Se mantuvo el plan del segundo informe.
8. **§14 Resultados, discusión y conclusiones:** vacía.
9. **§15 Trabajo futuro:** vacía.
10. **RNF-06 y M7:** el código no guarda de qué instantánea salió cada detección. El informe lo dice tal cual, y queda pendiente tu decisión.
11. **Umbrales congelados:** la Tabla 2 los presenta como congelados en `umbrales_congelados.json`, pero falta la calibración y el campo `congelado_en` (#60).
12. **Tabla 2, fila de AP-05 índice no utilizado o redundante:** se actualizó según el código. Revísala, porque es una fila que normalmente redactas tú.

### 1.5 Detalles de redacción para revisar

- **RNF-06 (§8.2):** la frase repite «la detección»: «la detección es determinista, pero la detección no guarda…».
- **Figuras:** son 5 (1 a 5), no 4 como se dijo antes en el chat. La plantilla pide entre dos y cuatro diagramas de secuencia, y hay tres (Figuras 3 a 5), así que cumple.

## 2. Estado de las 8 issues asignadas

| # | Issue | Estado | Commits / cambios |
|---|---|---|---|
| 55 | Pruebas unitarias por regla | **Completa** | `824cf73`, `24e719c` |
| 57 | Escenario propio de AP-04 índice ausente | **Completa** | `5e34531`, más la corrección de esta revisión (`eb4749d`) |
| 58 | Banco de un solo comando | **Completa** (decisión de AP-01 tomada el 16 de noviembre: opción (a)) | `0877249`, `49608d7` |
| 59 | Cálculo automático de M1 a M4 | **Completa** (agrega además la lista de chequeo de M7) | `56d5783` |
| 60 | Calibrar y congelar umbrales | **Parcial**: el mecanismo está listo, falta la calibración | `08a5023` |
| 61 | Repetir M5 | **Completa: CUMPLE en los dos motores** (17 de noviembre) | `214f30d` (`--aa-pairs`, `--instr-pairs` y evidencia) |
| 62 | Campaña final M1 a M7 | **Pendiente** (depende de #60 y de tus decisiones) | — |
| 64 | Análisis, informe y versión final | **Parcial**: borrador de `InformeFinal.md` | sin commitear |

### #55 Pruebas unitarias por regla (completa)

- Las 13 patologías tienen su archivo de pruebas:
  - `tests/anti_patterns/test_ap01_*` a `test_ap08_*`;
  - `tests/deadlocks/test_ct01_*` a `test_ct05_*`.
- Cada umbral se prueba en su límite y un paso por debajo, con controles de lo que no debe salir y la severidad.
- El motor pasa sus 472 pruebas.

### #57 Escenario de AP-04 índice ausente (completa)

- El escenario `missing_index` sigue la Tabla 2: AP-04 índice ausente con `CREATE INDEX ON ql_scn (c)`, y AP-02 escaneo completo evitable con la fracción real de filas.
- Pasó en ambos motores.
- **Corrección de esta revisión en `run_scenario.py`:**
  - el ajuste `temptable_use_mmap = OFF` ahora se aplica después de preparar la tabla, porque antes rompía el `INSERT` de 1 M de filas de MySQL con ERROR 1146;
  - al desmontar cada escenario se limpian de `pg_stat_statements` las entradas de las tablas borradas, que en PostgreSQL ocupaban `top_impact` y dejaban a AP-05 índice no utilizado o redundante sin consulta asociada.

### #58 Banco de un solo comando (completa)

- Corre los 22 escenarios con 10 repeticiones y las corridas de control de 30 min, por el flujo real (base registrada, servicio `pipeline`, servicio `detection-engine`).
- La carga base es sysbench `oltp_read_write` con 4 hilos.
- Se puede reanudar si se corta (`--reanudar`), reintenta las corridas fallidas y deja un registro de errores.

**Decisión tomada (16 de noviembre): opción (a).** Ya está aplicada en `manifiestos.py` (`DURANTE_INYECCION`) y `metricas.py`, y tiene su prueba (28 pasan). Al recalcular el smoke, M2 queda en 24/24. Sigue el planteamiento original como referencia.

**Decisión: AP-01 degradación respecto a la línea base sobre la carga base.**

- Durante las inyecciones (no en los controles), las sentencias de sysbench se vuelven más de 2 veces más lentas porque la inyección compite con ellas por CPU y bloqueos, y AP-01 lo reporta con severidad baja.
- En el smoke fueron 54 hallazgos así, y hacen que M2 de AP-01 caiga a 0,03–0,04 en los dos motores.
- Opciones:
  - **(a)** Declararlos como colaterales de la inyección. Siguen contando como falsos positivos si aparecen en los controles. **Es mi recomendación**, porque las degradaciones son reales.
  - **(b)** Contarlos como falsos positivos y declarar la limitación.
  - **(c)** Bajar la intensidad de la inyección.

### #59 Métricas M1 a M4 (completa)

- Compara los hallazgos de cada corrida con el manifiesto del escenario:
  - **esperados:** cuentan como VP o FN;
  - **colaterales:** hallazgos de otra patología que la inyección produce por construcción; no cuentan;
  - **línea base:** hallazgos propios de la carga base; no cuentan;
  - **no declarados:** cuentan como FP.
- Informa M1 con su intervalo de confianza de Wilson, M2, M3 (miembros y patrón) y M4 en segundos y en ventanas.
- `reporte.md` se puede recalcular sin volver a correr nada.

### #60 Calibrar y congelar umbrales (parcial)

- Hecho: `umbrales_congelados.json`, con todos los umbrales de AP-01 a AP-08, CT-04 y CT-05, y la prueba que falla si alguno cambia sin actualizar el archivo (41 casos).
- Falta:
  - correr la calibración (por ejemplo `banco.py --etiqueta calibracion`) con corridas distintas de las de validación;
  - completar `congelado_en`.

### #61 Repetir M5 (completa: CUMPLE)

**Resultado válido (17 de noviembre).** Se midió con el arnés `overhead.run --profile serious`, que ya ejercita la recolección completa (informes de abrazo mortal y `pg_stat_database`). Se usaron 16 pares con recolector, 8 pares A/A y 6 de instrumentación por motor.

| Motor | p95 del pipeline | IC 95 % | Brazo A/A (ruido del host) | Ciclos | Veredicto |
|---|---|---|---|---|---|
| PostgreSQL | −0,1 % | [−2,4; +2,3] | [−0,6; +4,0] dentro de ±5 % | 64, 0 fallidos | **CUMPLE** |
| MySQL | +1,2 % | [−0,6; +3,0] | [−3,9; +2,7] dentro de ±5 % | 64, 0 fallidos | **CUMPLE** |

- **Montaje.** Todo corrió dentro de Linux, en la Ubuntu de WSL y con su propio Docker:
  - Docker Desktop y el stack de QueryLens estaban apagados.
  - El sandbox estaba copiado en `~/ql_m5`.
  - El arnés corrió en un contenedor `python:3.14-slim` con `--network host`.
  - Se midió un motor a la vez, con el otro detenido.
  - Hubo 300 s de calentamiento.
  - La cola fue un contenedor temporal `pg17-pgmq` en el puerto 5440, porque el Postgres del sandbox no trae pgmq.
- **Comando por motor:** `--profile serious --engine <motor> --pairs 16 --aa-pairs 8 --warmup-s 300`.
- **Evidencia:**
  - `docs/evidencia/overhead_v2_20261117_053435` (PostgreSQL);
  - `docs/evidencia/overhead_v2_20261117_062617` (MySQL).
- **Coincide con el segundo informe** (Bryan, 12 de octubre): −2,1 % en PostgreSQL y +1,7 % en MySQL. Ahora la medición incluye la recolección completa, que es lo que pedía la issue.
- **Sin `EXPLAIN_FAILED` en MySQL.** El único aviso de salud fue `STATS_RECENTLY_RESET`, de severidad informativa.
- **Las 36 conexiones cortadas de la carga en PostgreSQL no son una falla.** Las provoca el propio arnés en el brazo de instrumentación: `toggles.PgAutoExplain` termina las sesiones de la carga (`pg_terminate_backend`) para que tomen el cambio de `auto_explain`. Con `--skip-instrumentation` hay 0.
- **El brazo de instrumentación quedó INCONCLUSO**: PostgreSQL +3,8 %, IC [−4,6; +11,0]; MySQL +3,3 %, IC [−0,8; +7,5]. No forma parte del veredicto de M5.
- **Cambio en el arnés:** `--aa-pairs` (pares del brazo A/A, que antes eran fijos por perfil). Commiteado en `214f30d`.

**Intentos previos en la misma máquina (descartados, 16 de noviembre).** Se corrieron desde Windows (venv de Windows, Docker Desktop, con el stack encendido) y dieron INCONCLUSO en los dos motores. En la primera, PostgreSQL dio +8,6 %, IC [−2,0; +22,3]; MySQL, +4,2 %, IC [−3,1; +10,4]. El brazo A/A quedó entre ±9 y ±31 %.

El experimento de ruido mostró la causa. Con 1 worker el A/A cierra tanto en Windows como en un contenedor. Con 2 workers, en Linux también cierra a partir del calentamiento. El ruido venía del entorno de Windows (Docker Desktop, el stack y las aplicaciones abiertas), no del hardware.

Una de esas corridas tuvo además los 64 ciclos fallidos (`schema "pgmq" does not exist`). El arnés, lanzado desde Windows, encolaba en `localhost:5432`, que es el Postgres del sandbox. El preflight no lo detecta, porque solo comprueba que la base responda.

**Decisión (17 de noviembre):** esta corrida reemplaza la repetición que iba a hacer Bryan.

### #62 Campaña final M1 a M7 (pendiente)

Depende de:

- la calibración (#60);
- ~~un M5 concluyente (#61)~~ resuelto el 17 de noviembre: CUMPLE;
- **M7:** hoy la detección no guarda el identificador de su instantánea (RNF-06), así que el banco informa M7 como "NO MEDIBLE". Arreglarlo cambia el esquema de `public.detecciones` y el motor de detección; también requiere tu decisión.

### #64 Informe final (parcial)

Ver la sección 1 de este reporte.

## 3. Mapa de archivos modificados y creados

| Issue | Archivo | Estado |
|---|---|---|
| #55 | `detection_engine/tests/anti_patterns/` (13 archivos, incluidos `test_ap03_*` y `test_ap04_*` separados de `test_predicate_fixes.py`) | commiteado |
| #57 | `ql_sandbox/scenarios/run_scenario.py`: escenario `missing_index` y alias | commiteado |
| #57 / auditoría | `ql_sandbox/scenarios/run_scenario.py`: orden del ajuste del servidor y `forget_scenario_statements` | `eb4749d` |
| #58 | `ql_sandbox/banco/banco.py`, `README.md` | `49608d7` |
| #58 | `ql_sandbox/scripts/base_load.sh`: sysbench con tasa fija y `--db-ps-mode=disable` (sin él, MySQL no da digest a las sentencias preparadas) | `49608d7` |
| #58 | `ql_sandbox/mediciones/measure.py`: `run()` envía bytes; arregla el `\r\n` que rompía `querylens add` en Windows | `0877249` |
| #58 | `.gitignore`: `ql_sandbox/banco/resultados/` | `49608d7` |
| #59 | `ql_sandbox/banco/manifiestos.py`, `metricas.py`, `tests/test_metricas.py` | `56d5783` |
| #60 | `detection_engine/umbrales_congelados.json`, `tests/test_umbrales_congelados.py` | `08a5023` |
| #61 | `telemetry_pipeline/overhead/run.py` (`--aa-pairs`, `--instr-pairs`) y `docs/evidencia/overhead_v2_20261117_053435`, `_062617` | `214f30d` |
| #64 | `InformeFinal.md` | nuevo (la plantilla con contenido) |
| — | `reporte_validacion_final.md` | este reporte |

No se modificó la lógica del pipeline (`telemetry_pipeline/`) ni la de los detectores (`detection_engine/anti_patterns`, `deadlocks`), y los detectores siguen siendo independientes y sin ramas por motor. Los archivos `prompt_noche.txt` y `prompt__noche.txt` no se tocaron.

## 4. Resultados de las pruebas desde cero

### Limpieza

- Se ejecutó `docker compose down -v --remove-orphans` en los proyectos `querylens` (todos los perfiles), `qltest` y `ql_sandbox`.
- Después, `docker ps -a` no mostró contenedores, y no quedaron volúmenes ni redes de esos proyectos.
- Quedan 23 volúmenes anónimos sin etiqueta de proyecto; no se tocaron.

### Reconstrucción

- Sandbox y stack principal (`--profile ui`) con `up -d --build --wait`.
- Todos los servicios quedaron *healthy*: postgres, auth-service, api-rest, frontend, nginx, pipeline, detection-engine y scheduler. `db-roles` terminó con código 0.

### Suites

| Suite | Resultado |
|---|---|
| Pipeline (`docker compose -p qltest --profile test run --rm tests`) | 1225 pasan. En la primera pasada, 1 se omitió porque `ql_demo` no tenía tablas; pasó tras la carga. |
| Motor de detección | 472 pasan |
| Banco (`ql_sandbox/banco/tests`) | 27 pasan |
| `ruff check telemetry_pipeline` (lint de CI) | sin errores |
| Frontend `tsc --noEmit` | sin errores |
| Frontend `next lint` | sin advertencias ni errores |

### Escenarios reales (banco `--smoke`, 1 repetición, ambos motores)

**Por escenario:**

| Escenario | PostgreSQL | MySQL |
|---|---|---|
| AP-04 índice ausente + AP-02 escaneo completo evitable | 2/2 | 2/2 |
| AP-03 predicado no sargable | 1/1 | 1/1 |
| AP-08 error grave de estimación de cardinalidad | 1/1 | 1/1 |
| AP-06 patrón N+1 | 2/2 | 2/2 |
| AP-07 vertido a disco | 1/1 | 1/1 |
| AP-05 índice no utilizado o redundante | 3/3 | 3/3 |
| AP-01 degradación respecto a la línea base | 1/1 | 1/1 |
| Contención | 3/3 (CT-01, CT-02, CT-05) | 5/5 (CT-01 a CT-05) |

**Por métrica:**

| Métrica | Resultado |
|---|---|
| M1 | 24/24 combinaciones |
| M3 | 8/8 |
| M4 | 24/24 |
| M2 | 22/24 en la corrida original: solo fallaba AP-01 degradación respecto a la línea base. Con la decisión (a), al recalcular queda en **24/24** (el reporte anterior quedó como `reporte.antes-colateral-ap01.md`). |

**Corridas de control:** ningún falso positivo; solo los índices `k_N` de sysbench, declarados como línea base.

### Punta a punta (API y CLI)

- Por nginx, `/api/diagnostics` devuelve:
  - **PostgreSQL:** 60 problemas;
  - **MySQL:** 119 problemas.
- En ambos motores, la lista está ordenada por impacto acumulado de la sesión y trae el estado de evaluación de las 13 patologías.
- El 100 % de los problemas tiene explicación, evidencia, recomendación y riesgo.
- **CLI:** `querylens report --session` y `querylens health` funcionan.

### M6 (anonimización)

- 0 apariciones de los marcadores en las 15 tablas de almacenamiento y en los logs, en los dos motores.
- En PostgreSQL hubo que repetir la prueba tras reiniciar `pg_stat_statements` del sandbox. En la primera corrida, las sentencias de sysbench del banco ocupaban `top_impact` y el control "candidatas capturadas" falló.

### M5 (sobrecosto)

CUMPLE en los dos motores (17 de noviembre): PostgreSQL −0,1 % [−2,4; +2,3], MySQL +1,2 % [−0,6; +3,0]. Ver la issue #61 en la sección 2.

### Estado final

- `qltest` bajado con sus volúmenes.
- Se borraron los reportes de `reports/` generados por estas corridas.
- El stack principal y el sandbox quedan arriba y sanos, sin carga activa.
- La base de QueryLens conserva los hallazgos del smoke, para que puedas revisarlos en el frontend.

## 5. Guía de verificación manual

1. **Diff y archivos nuevos:**
   ```
   git diff develop...HEAD --stat
   git status
   ```
   Revisar `ql_sandbox/banco/README.md`.
2. **Pruebas:**
   ```
   MSYS_NO_PATHCONV=1 docker compose -p qltest --profile test run --rm tests
   MSYS_NO_PATHCONV=1 docker compose -p qltest --profile test run --rm tests sh -c "cd /repo && python -m pytest -q ql_sandbox/banco/tests"
   ```
   Al final: `MSYS_NO_PATHCONV=1 docker compose -p qltest down -v`.
3. **Resultado del smoke:** abrir `ql_sandbox/banco/resultados/banco-desde-cero-20261116-053927/reporte.md`. Para recalcularlo:
   ```
   python ql_sandbox/banco/metricas.py <carpeta>
   ```
4. **Frontend:**
   - Abrir `http://127.0.0.1:8080/conectar`.
   - Registrar `sandbox-pg`: `host.docker.internal`, puerto 5432, usuario `querylens_monitor`, contraseña `monitor_pass`, base `ql_demo`.
   - En Diagnósticos deberían verse:
     - los hallazgos ordenados por impacto, con riesgo e impacto;
     - el panel de estado de evaluación (AP-06 patrón N+1 aparece "no evaluada" porque ya no hay claves foráneas en el sandbox);
     - el detalle por patrón, con su grafo de evidencia.
   - Repetir con `sandbox-mysql` (puerto 3307, sin base).
5. **CLI:**
   ```
   MSYS_NO_PATHCONV=1 docker compose run --rm -T cli report <id> --session --format md
   MSYS_NO_PATHCONV=1 docker compose run --rm -T cli health <id>
   ```
   Los id actuales son `db_39389b4c` (PostgreSQL) y `db_915a8ca5` (MySQL).
6. **Umbrales congelados:** cambiar a mano un umbral, por ejemplo `_MIN_CHILD_CALLS` de AP-06 patrón N+1, y correr `pytest tests/test_umbrales_congelados.py`. Debe fallar. Después revertir el cambio.
7. **Informe final:**
   - Revisar en `InformeFinal.md` la Tabla 2, la sección 8 y las figuras 1 a 4.
   - Comprobar que los diagramas mermaid se vean bien en GitHub o en VS Code.
   - Completar los 12 puntos faltantes de la sección 1.4 de este reporte.
