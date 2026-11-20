# Reporte de validación final — rama `feature/validacion-final`

Revisión del 16 de noviembre de 2026: Informe final, auditoría de las 8 issues asignadas y pruebas desde cero. No se hizo ningún commit ni push.

**Actualización del 19 de noviembre de 2026:** la calibración (#60), el enlace de cada detección con su instantánea (#54) y la campaña final (#62) están hechos. La campaña cumplió M1 a M4 y M7 en las 24 combinaciones; ver #62 en la sección 2. Los puntos resueltos de la sección 1.4 están tachados.

**Actualización del 20 de noviembre de 2026:** se revisó el Informe final sección por sección contra el `SegundoInforme` y contra el código (ver 1.6), se completaron el Resumen, §7.2, §9, §12 (Figura 6), §14 y §15, se quitaron los textos guía de la plantilla, se repitió M6 después de la campaña y se corrigieron en el front las reglas de los patrones que no coincidían con los detectores (ver 1.7). Queda pendiente §13.3 (usabilidad; ver 1.4, punto 7). El mismo día se hizo la validación final del informe contra el código (ver 1.8). Esos cambios se dejaron sin commitear y se commitearon al final del 20 de noviembre (hashes en la sección 3). No se hizo push.

> **Nota sobre esta sección.** La sección 1 original de este reporte se sobrescribió por error con el resumen del chat, y se perdió. Se reconstruyó comparando, línea por línea, [InformeFinal.md](../InformeFinal.md) con [SegundoInforme.md](../SegundoInforme.md). Las secciones 2 a 5 son las originales.

## 1. Actualización del Informe final e información faltante

### 1.1 Cómo se armó el Informe final

- La plantilla [InformeFinal.md](../InformeFinal.md) solo traía los encabezados de las secciones 1 a 16 y un texto guía en cada una.
- Debajo de cada texto guía se puso el contenido del `SegundoInforme`:
  - sin cambios, donde sigue vigente;
  - corregido, donde el código actual dice otra cosa.
- ~~Esos textos guía se dejaron tal cual.~~ El 20 de noviembre se quitaron todos.
- La plantilla reorganiza la sección 10 en 10.1.1 (descripción general), 10.1.2.1 (componentes), 10.1.2.2 (interacción) y 10.1.2.3 (comportamiento). El contenido de las secciones 10.1 a 10.4 del segundo informe se repartió en esa estructura.
- ~~La sección 9 (Evaluación de alternativas) no se copió: la plantilla solo trae un enlace al segundo informe.~~ El 20 de noviembre se copió completa (Tablas 10 a 14), con un apartado nuevo sobre el ajuste posterior de la fuente de planes.

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
| §7.4 Hitos | Se agrega la columna de estado de los hitos H1 a H6, hoy «Estado al 19 de noviembre de 2026»; los seis están en «Cumplido». |
| §8.1 Funcionales | RF-03, RF-06, RF-07, RF-08, RF-09, RF-11 y RF-13 pasan a «Implementado». Se agregan **RF-14** (encender o pausar el monitoreo y programar ventanas) y **RF-15** (reporte por rango de fechas o por sesión). |
| §8.2 No funcionales | ~~**RNF-01:** dice «M5 inconcluso»; **RNF-06:** «Parcial».~~ Al 20 de noviembre: RNF-01, RNF-03 a RNF-06 y RNF-09 «Verificado»; RNF-10 «Implementado»; RNF-11, ver 1.4, punto 7. |
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
  - §16 Referencias (el 20 de noviembre se agregó la [45], Wilson).
- **Partes del segundo informe que no pasaron al Informe final:**
  - el Resumen (reescrito el 20 de noviembre);
  - el párrafo inicial de §3.3 («Respecto al planteamiento del primer informe…»);
  - ~~el detalle de §9, reemplazado por el enlace~~ (copiado el 20 de noviembre);
  - §11.4 Pendientes;
  - §14 Resultados parciales (reemplazada por la §14 final);
  - §15 Plan de cierre (reemplazada por Trabajo futuro).

  Eran contenido de una entrega intermedia.

### 1.4 Información faltante (no se rellenó)

1. ~~**Resumen / Abstract:** solo tiene el texto guía.~~ Resuelto el 20 de noviembre: cuatro párrafos (problema y método, sistema, resultados y conclusiones).
2. ~~**§7.2 Iteraciones:** solo llega hasta el 12 de octubre.~~ Resuelto el 20 de noviembre: iteraciones 6 a 11 (13 de octubre al 19 de noviembre), reconstruidas del historial de commits, y un párrafo con los ajustes del periodo.
3. ~~**§12 Despliegue:** falta el diagrama de despliegue.~~ Resuelto el 20 de noviembre: Figura 6, armada con las redes, puertos y servicios de `docker-compose.yml`, y un párrafo de puesta en marcha.
4. ~~**§13 Validación:** faltan los resultados de M1 a M4 y M7 de la campaña final (#62).~~ Resuelto el 19 de noviembre: §13.2 tiene la campaña y la Tabla 16.
5. ~~**§13.2, M5:** sigue con la medición del 12 de octubre.~~ Resuelto el 19 de noviembre: §13.2, §7.4 (H2) y §8.2 (RNF-01) tienen el resultado del 17 de noviembre.
6. ~~**§13.2, M6:** sigue con la ejecución del 12 de octubre (10 tablas).~~ Resuelto el 19 de noviembre con la repetición del 16 de noviembre; el 20 de noviembre se repitió **después de la campaña**, como pide la Tabla 7 (ver sección 4, M6), y §13.2 tiene esa ejecución.
7. **§13.3 Pruebas de usabilidad: PENDIENTE y con texto que hay que corregir.** La sesión con compañeros no se ha hecho. Está planeada: 7 compañeros de la carrera que no participaron en el desarrollo, una sesión grupal con el flujo principal y un formulario anónimo (8 afirmaciones de 1 a 5, una pregunta de perfil y una abierta de mejora). **Ojo:** RNF-11 (Tabla 9), el último párrafo de §8.2, el objetivo 8 de la Tabla 17 y §14.3 ya dicen que los compañeros usaron la herramienta. Antes de entregar hay dos caminos: hacer la sesión y escribir §13.3 con los resultados reales, o aplicar la versión «no realizada» (RNF-11 «Pendiente», objetivo 8 «No realizado», §13.3 con el protocolo preparado).
8. ~~**§14 Resultados, discusión y conclusiones:** vacía.~~ Resuelto el 20 de noviembre: 14.1 con la Tabla 17 (estado de los 8 objetivos), 14.2 Discusión (incluye el intervalo de Wilson y los límites del diseño) y 14.3 Conclusiones.
9. ~~**§15 Trabajo futuro:** vacía.~~ Resuelto el 20 de noviembre: un párrafo inicial y ocho líneas de trabajo.
10. ~~**RNF-06 y M7:** el código no guarda de qué instantánea salió cada detección.~~ Resuelto el 18 de noviembre (#54, `d10ed1e`): cada detección guarda su evidencia y el `msg_id` de su instantánea. RNF-06 está como verificado en el informe.
11. ~~**Umbrales congelados:** falta la calibración y el campo `congelado_en` (#60).~~ Resuelto el 18 de noviembre (`298781e`): versión 2, con `congelado_en` y la calibración registrada.
12. **Tabla 2, fila de AP-05 índice no utilizado o redundante:** se actualizó según el código. Revísala, porque es una fila que normalmente redactas tú.

### 1.5 Detalles de redacción para revisar

- ~~**RNF-06 (§8.2):** la frase repite «la detección».~~ Resuelto: RNF-06 quedó «Verificado» con otra redacción.
- **Figuras:** son 6. La plantilla pide entre dos y cuatro diagramas de secuencia, y hay tres (Figuras 3 a 5), así que cumple; la Figura 6 es el diagrama de despliegue.

### 1.6 Revisión sección por sección (20 de noviembre)

Se comparó cada subsección del Informe final con el `SegundoInforme` y con el código, y se aplicaron solo los cambios aprobados uno por uno.

| Sección | Cambios |
|---|---|
| Resumen | Escrito completo, en español. La frase de la fuente de planes aclara que el 18 de 18 es de la evaluación del segundo informe. |
| §1 Introducción | Ajustes del párrafo de organización del documento y del estado del trabajo. |
| §2.4 y §2.6 | Tabla 1: filas del contador de abrazos mortales y del plan de una ejecución real. §2.6: una frase sobre el intervalo de Wilson. |
| §3.2 | Último párrafo: las candidatas son las 10 sentencias de mayor tiempo acumulado, más las inestables y las que escriben a disco, y la limitación que eso implica. |
| §3.3 | Tiempos verbales, fuente de planes de MySQL, SQL Server como trabajo futuro, umbrales congelados el 18 de noviembre, interfaz web (grafo de evidencia por hallazgo). |
| §5.3 y §6.4 | Tabla 4 con la fuente de planes actual; §6.4: en servicios gestionados, AP-08 error grave de estimación de cardinalidad queda sin evaluar. |
| §7 | 7.1: pruebas por regla del motor. 7.2: iteraciones 6 a 11. 7.3: verbos en presente o pasado. 7.4: **H6 en «Cumplido»**. |
| §8 | RF-05 con la fuente de planes por motor; RNF-02 con la nota de auto_explain; RNF-09 «Verificado»; RNF-10 «Implementado»; RNF-11 (ver 1.4, punto 7). |
| §9 | Copiada del segundo informe, con el apartado «Ajuste posterior a la selección» en 9.3 (`log_analyze` en PostgreSQL, `events_statements_history` en MySQL y el hueco del historial). No se agregaron la tabla de cobertura del historial ni la nota sobre el costo de la Opción C medido antes de `log_analyze` (decisión del 20 de noviembre). |
| §10 | Fuente de planes de MySQL en 10.1.1, historial de MySQL en la Figura 4 y la aclaración de auto_explain en el párrafo de eficiencia. Interfaz: el grafo de evidencia está en cada hallazgo y la página por patrón tiene una visualización. |
| §11 | Fecha al 19 de noviembre; recolector sin la frase contradictoria de MySQL; el motor ya dice que todas las reglas cumplieron en la campaña; sqlglot en la Tabla 15; exportación a PDF; grafo por hallazgo. |
| §12 | Figura 6 (despliegue), párrafo de puesta en marcha (`gen_env.py`, perfil `ui`, guías) y sin la oración repetida. |
| §13 | 13.1: se quitó la frase de que registro y API no tienen pruebas, porque se van a hacer. 13.2: M6 del 20 de noviembre, la fuente de planes aclara su configuración y la nota de la Tabla 16 explica que AP-02 escaneo completo evitable y AP-04 índice ausente comparten escenario. |
| §14, §15 y §16 | Ver 1.4, puntos 8 y 9, y la referencia [45]. |
| Textos guía | Se quitaron los 31 bloques que quedaban (§9 a §16). |

### 1.7 Correcciones en el front (20 de noviembre)

Las páginas de patrones se escribieron con ventanas de 60 s y no se habían actualizado. Se compararon con los detectores y con las Tablas 2 y 3:

- **AP-01 degradación respecto a la línea base:** el gráfico dice «ventana de 10 s» y el eje va en segundos; la regla pasa a 5 ejecuciones, 60 ventanas previas (10 min) y 10 ms (antes decía 30 ejecuciones, 10 ventanas y 1 ms). También la portada.
- **AP-02 escaneo completo evitable:** regla completa y en español; la evidencia de ejemplo dice «Filas devueltas por ejecución».
- **AP-03 predicado no sargable:** regla con escaneo completo y primera columna de un índice; se quitaron la causa y la acción de LIKE '%texto', que ya no se detecta.
- **AP-04 índice ausente:** la regla dice lo que lo define (ninguna columna del filtro es primera de un índice) y la advertencia aclara que LIKE no se evalúa.
- **AP-05 índice no utilizado o redundante:** 30 min de estadísticas, ni primario ni único, excepción de clave foránea.
- **AP-06 patrón N+1:** la advertencia incluye FOR SHARE.
- **AP-07 vertido a disco:** se quitó la frase falsa de que en MySQL no se recolectan los ordenamientos que van a disco.
- **AP-08 error grave de estimación de cardinalidad:** compara con las filas reales de la misma ejecución, no con el promedio.
- **Portada:** «Regístrala con sus credenciales, inicia su monitoreo y…», porque una base nueva queda en pausa.

CT-01 a CT-05 coincidían. `tsc --noEmit` y `next lint` pasan. El contenedor del front se reconstruyó desde Git Bash con `MSYS_NO_PATHCONV=1`, y `/auth` y `/api` responden por nginx.

### 1.8 Validación final del informe contra el código (20 de noviembre)

Última revisión: se leyó el Informe final completo y se contrastó cada afirmación con el código, los scripts de inyección, la configuración, la integración continua y la evidencia de la campaña. Quedaron fuera la usabilidad (§13.3 y lo relacionado, ver 1.4, punto 7) y la §15.

**Lo que se verificó y estaba bien**

- **Tabla 16:** las 24 combinaciones coinciden con `metricas.json` de la campaña (detecciones, medianas y máximos de M4), igual que 0 falsos negativos, 0 falsos positivos, 1174 problemas completos y los 20 controles.
- **Pruebas:** se recontaron 1225 pruebas del recolector (24 de integración), 474 del motor y 30 del banco.
- **Umbrales de las Tablas 2 y 3:** coinciden con las constantes de los 13 detectores, severidades incluidas.
- **Escenarios de inyección:** coinciden con los scripts, salvo CT-05 cascada por transacción larga (ver abajo).
- **Recolector y servicios:** 43 códigos de salud, 15 tablas, ventana de 2 MiB del registro, umbral de auto_explain de 50 ms, recolección secuencial de 0,3 a 0,65 s por base, intervalos de 30 s y 10 s, y nginx como único puerto publicado.
- **Congelamiento:** la versión 2 de los umbrales es anterior a las campañas, y el código de detección no cambió desde entonces.

**Lo que se corrigió en el informe**

| Dónde | Antes | Ahora | Motivo |
|---|---|---|---|
| Tabla 5 | «el tamaño del mensaje está acotado» | si supera 1 MB, el estado de la recolección lo informa | el código no tiene tope: `PAYLOAD_WARN_BYTES` solo abre un aviso informativo |
| Tabla 2, AP-04 índice ausente y AP-03 predicado no sargable | `seq_scan`, `seq_tup_read`, `idx_scan`, `SUM_NO_INDEX_USED` | filas vivas, diferencias de ejecuciones y filas, texto analizado e índices | esos campos no los usa ningún detector; la base de escaneo es la de AP-02 escaneo completo evitable |
| Tabla 6, AP-03 y AP-04 | «La sentencia no tiene plan» | «Igual que AP-02» | misma evaluación de escaneo completo |
| Tabla 3, CT-04 ampliación de bloqueos por ausencia de índice | `INNODB_TRX` (`trx_rows_locked`, `trx_rows_modified`) | solo el informe del abrazo mortal | las filas bloqueadas y las modificadas salen del informe |
| Tabla 3, CT-05 cascada por transacción larga | inactiva 120 s | 60 s | es lo que inyecta el banco (`HOLD_SECONDS=60`) |
| §3.2, selección de candidatas | «todos salvo AP-01 y AP-06» | AP-02, AP-03, AP-04, AP-05 y AP-08 | AP-07 vertido a disco no se ve afectado: toda sentencia que vertió entra entre las candidatas |
| §3.2, supuestos | «el recolector los verifica» | dos excepciones declaradas | no se comprueban `log_analyze` ni el historial de MySQL de 100 sentencias; su falta solo aparece como AP-08 no evaluada |
| §3.3 | CT-01 y CT-02 sin evaluar en servicios gestionados | también AP-08 error grave de estimación de cardinalidad | coherente con la §6.4 |
| §10.1.2.3 | «consulta la cola cada 5 s» | lecturas de hasta 5 s que revisan la cola cada 100 ms | es `read_with_poll` |
| §7.4, H4 | «guarda la instantánea» | «guarda el identificador de la instantánea» | es lo que se guarda |
| Tabla 15 | SQLAlchemy 2.0 | SQLAlchemy 2 | el recolector y el motor usan la 2.1 |
| §3.2, §3.3 y §5.3 | verbos en futuro | presente o pasado | el proyecto ya terminó |
| Formato | cuatro títulos o párrafos pegados a listas | corregidos | el párrafo de la línea 158 se dibujaba dentro de la lista |

**Decisiones que se cerraron**

- **AP-07 vertido a disco:** no evalúa sentencias sin plan (`CREATE INDEX`, `REINDEX`, `CREATE TABLE … AS`, `UPDATE` con ordenamiento). Se declaró como limitación en la Tabla 2 y no se cambió el código, porque habría obligado a repetir la campaña. `detection_engine/references/002` quedó como «decidido: opción 4».
- **Modelo de lenguaje:** «No incluye» ya no lo deja abierto: no se incorporó, y las explicaciones salen de plantillas fijas por regla.
- **Supuestos sin verificar:** se declaran en el texto (§3.2); no se agregaron chequeos al recolector.
- **Registro sin autenticación:** la §12 explica por qué el proxy solo escucha en 127.0.0.1 y que exponerlo requiere autenticación de usuarios.
- **Registros de decisión:** la §9 y el objetivo 2 de la Tabla 17 citan `docs/decisiones` (ADR-001 a ADR-003). Al ADR-001 se le agregó el «Ajuste posterior» (`log_analyze`, historial de MySQL y su límite de cobertura).

**Limpieza del código (solo comentarios; el motor sigue pasando sus 474 pruebas):** se cerraron los «Pendiente: llevar … al informe» de `baseline_degradation.py`, `cardinality_misestimate.py` y `n_plus_one.py` (dos), y se actualizó `references/002` (decisión, severidades de 10 s y `SUM_SORT_MERGE_PASSES` ya recolectado). Se dejó el pendiente de LIKE en `missing_index.py`, que es trabajo futuro del pipeline y no del informe.

**§13.1:** ahora describe los cinco trabajos de integración continua (análisis estático, unitarias y de contrato, motor de detección, suite completa del recolector y levantamiento del sistema completo con su prueba por nginx y por la línea de comandos).

**Pendiente tras esta validación:** solo §13.3 y las cuatro frases de usabilidad (ver 1.4, punto 7).

## 2. Estado de las 8 issues asignadas

| # | Issue | Estado | Commits / cambios |
|---|---|---|---|
| 55 | Pruebas unitarias por regla | **Completa** | `824cf73`, `24e719c` |
| 57 | Escenario propio de AP-04 índice ausente | **Completa** | `5e34531`, más la corrección de esta revisión (`eb4749d`) |
| 58 | Banco de un solo comando | **Completa** (decisión de AP-01 tomada el 16 de noviembre: opción (a)) | `0877249`, `49608d7` |
| 59 | Cálculo automático de M1 a M4 | **Completa** (agrega además la lista de chequeo de M7) | `56d5783` |
| 60 | Calibrar y congelar umbrales | **Completa** (18 de noviembre) | `08a5023`, `298781e`, `4b6dfd6` |
| 61 | Repetir M5 | **Completa: CUMPLE en los dos motores** (17 de noviembre) | `214f30d` (`--aa-pairs`, `--instr-pairs` y evidencia) |
| 62 | Campaña final M1 a M7 | **Completa: CUMPLE** (18 y 19 de noviembre) | `bba7c60`, `ad09307`, `d1cf211`; evidencia en `docs/evidencia/banco-campana-20261118-214954` (`2b6f1f1`) y M6 en `e525941` |
| 64 | Análisis, informe y versión final | **Casi completa** (20 de noviembre): `InformeFinal.md` revisado sección por sección, con Resumen, §14 y §15; falta resolver §13.3 (usabilidad) | `ac1f0b5` (informe), `607b9ab`, `2af3455`, `d23ed8c` |

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

### #60 Calibrar y congelar umbrales (completa)

- `umbrales_congelados.json` tiene todos los umbrales de AP-01 a AP-08, CT-04 y CT-05, y una prueba que falla si alguno cambia sin actualizar el archivo (41 casos).
- **Calibración (18 de noviembre):** se hizo con corridas distintas de las de validación (`banco-calibracion-20261118-022801`). Resultado: M1 24/24, M2 24/24, M3 8/8 y 0 falsos positivos.
- **Único cambio de umbral (versión 2):** AP-01 degradación respecto a la línea base pasa de 1 ms a 10 ms de aumento mínimo. En la primera calibración dio 31 falsos positivos en los controles de MySQL; con 10 ms dio 0.
- `congelado_en` es `2026-11-18`. El archivo registra la corrida de calibración y su resultado (`298781e`).
- `measure_ct.py` ahora guarda los hallazgos de cada repetición de contención (`4b6dfd6`).

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
- **Control positivo (19 de noviembre).** Se corrió aparte, en el mismo montaje de WSL. Comando: `--positive-control --aa-pairs 2 --pairs 2 --skip-instrumentation`.
  - **Resultado:** el arnés lo detectó en los dos motores, con el límite inferior del IC por encima del 5 %:
    - PostgreSQL: +14,8 % [+11,0; +18,5];
    - MySQL: +22,0 % [+9,1; +67,2].
  - **Lo que no cuenta:** en esa corrida, el A/A de MySQL, con solo 2 pares, salió ruidoso y el brazo del recolector quedó INCONCLUSO. Con 2 pares no es evidencia de M5, que sigue siendo la corrida del 17 de noviembre.
  - **Evidencia:** `docs/evidencia/overhead_v2_20261119_210548` (PostgreSQL) y `_212325` (MySQL), en `1f753d6`.

**Intentos previos en la misma máquina (descartados, 16 de noviembre).** Se corrieron desde Windows (venv de Windows, Docker Desktop, con el stack encendido) y dieron INCONCLUSO en los dos motores. En la primera, PostgreSQL dio +8,6 %, IC [−2,0; +22,3]; MySQL, +4,2 %, IC [−3,1; +10,4]. El brazo A/A quedó entre ±9 y ±31 %.

El experimento de ruido mostró la causa. Con 1 worker el A/A cierra tanto en Windows como en un contenedor. Con 2 workers, en Linux también cierra a partir del calentamiento. El ruido venía del entorno de Windows (Docker Desktop, el stack y las aplicaciones abiertas), no del hardware.

Una de esas corridas tuvo además los 64 ciclos fallidos (`schema "pgmq" does not exist`). El arnés, lanzado desde Windows, encolaba en `localhost:5432`, que es el Postgres del sandbox. El preflight no lo detecta, porque solo comprueba que la base responda.

**Decisión (17 de noviembre):** esta corrida reemplaza la repetición que iba a hacer Bryan.

### #62 Campaña final M1 a M7 (completa: CUMPLE)

**Corrida `banco-campana-20261118-214954`** (commit `d1cf211`, árbol limpio):

- se ejecutó del 18 de noviembre, 21:49, al 19 de noviembre, 14:57 (~17 h);
- primero MySQL y después PostgreSQL;
- comando: `banco.py --engine both --reiniciar-sentencias --etiqueta campana`.

| Métrica | Resultado | Veredicto |
|---|---|---|
| M1 | 24/24 combinaciones, 0 falsos negativos | CUMPLE |
| M2 | 24/24, 0 falsos positivos (también en los 20 controles de 30 min) | CUMPLE |
| M3 | 8/8, miembros y patrón 10/10 en cada una | CUMPLE |
| M4 | 24/24 (máximo: 40,5 s en AP-08 error grave de estimación de cardinalidad en PostgreSQL) | CUMPLE |
| M7 | 1174/1174 problemas | CUMPLE |

- **Dependencias resueltas:**
  - calibración (#60), 18 de noviembre;
  - M5 (#61), 17 de noviembre;
  - enlace de cada detección con su instantánea (#54, `d10ed1e`), con el que M7 se puede medir.
- **Evidencia:** `docs/evidencia/banco-campana-20261118-214954`. Contiene el reporte, las métricas, `corridas.jsonl`, el log y los bloques de contención. Se recalcula idéntica con `metricas.py`.
- **Informe:** los resultados están en §13.2 (Tabla 16) de `InformeFinal.md`.

### #64 Informe final (casi completa)

Ver la sección 1 de este reporte, en especial 1.4 (punto 7, usabilidad), 1.6 y 1.7.

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
| #60 | `detection_engine/umbrales_congelados.json` (versión 2), `anti_patterns/baseline_degradation.py` (10 ms) y su prueba | `298781e` |
| #60 | `ql_sandbox/mediciones/measure_ct.py`: hallazgos por repetición | `4b6dfd6` |
| #54 | `querylens_database/hallazgos.sql`, `detection_engine/storage.py` y `main.py`, banco (M7) y pruebas | `d10ed1e` |
| #62 | `ql_sandbox/banco/banco.py`, `manifiestos.py` y `README.md` (`--reiniciar-sentencias`, orden de escenarios y de motores) | `bba7c60`, `ad09307`, `d1cf211` |
| #62 | `docs/evidencia/banco-campana-20261118-214954/` | `2b6f1f1` (`campana.log` no se versiona: `*.log` está en `.gitignore`) |
| #61 | `docs/evidencia/overhead_v2_20261119_210548/`, `_212325/` (control positivo) | `1f753d6` |
| M6 | `docs/evidencia/m6-20261120/` (resultado y primer intento) | `e525941` (los `.log` se agregaron con `git add -f`) |
| #64 | `InformeFinal.md` | `ac1f0b5` (nuevo: la plantilla con contenido) |
| #64 | `ql_sandbox/banco/README.md`: el orden de AP-05 índice no utilizado o redundante apunta a §3.2 del informe | `d23ed8c` |
| #64 | `frontend/lib/antipatterns.ts`, `frontend/lib/landing-data.ts`, `frontend/components/antipatterns/Viz.tsx`, `frontend/components/landing/slides/CtaSlide.tsx` (textos, ver 1.7) | `607b9ab` |
| #64 | `detection_engine/anti_patterns/baseline_degradation.py`, `cardinality_misestimate.py`, `n_plus_one.py` (solo comentarios, ver 1.8) | `d23ed8c` |
| #64 | `detection_engine/references/002_ap07_alcance_sentencias.md` (decisión de AP-07) y `docs/decisiones/ADR-001_fuente_del_plan.md` («Ajuste posterior») | `2af3455` |
| — | `docs/reporte_validacion_final.md` | este reporte (antes estaba en la raíz del repositorio) |

No se modificó la lógica del pipeline (`telemetry_pipeline/`) ni la de los detectores (`detection_engine/anti_patterns`, `deadlocks`), y los detectores siguen siendo independientes y sin ramas por motor. Los cambios del front del 20 de noviembre son solo de textos y etiquetas, y los de `detection_engine/anti_patterns` del mismo día son solo comentarios. Los archivos `prompt_noche.txt` y `prompt__noche.txt` no se tocaron.

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
- **20 de noviembre, después de la campaña (#62):** 0 apariciones en las 15 tablas y en los logs, en los dos motores; candidatas capturadas 6 y 6. El primer intento salió inconcluso en PostgreSQL porque el servicio `detection-engine` en marcha archivó el mensaje antes de que el script lo leyera de `q_analyze_job` (el script solo lee la cola viva); se repitió con ese servicio detenido y se volvió a levantar. Evidencia en `docs/evidencia/m6-20261120`.

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
     - en cada hallazgo, su grafo de evidencia (sección plegable de la tarjeta);
     - la página de cada patrón («¿Qué es esto?»), con la regla corregida y su visualización (ver 1.7).
   - Repetir con `sandbox-mysql` (puerto 3307, sin base).
5. **CLI:**
   ```
   MSYS_NO_PATHCONV=1 docker compose run --rm -T cli report <id> --session --format md
   MSYS_NO_PATHCONV=1 docker compose run --rm -T cli health <id>
   ```
   Los id actuales son `db_39389b4c` (PostgreSQL) y `db_915a8ca5` (MySQL).
6. **Umbrales congelados:** cambiar a mano un umbral, por ejemplo `_MIN_CHILD_CALLS` de AP-06 patrón N+1, y correr `pytest tests/test_umbrales_congelados.py`. Debe fallar. Después revertir el cambio.
7. **Informe final:**
   - Revisar en `InformeFinal.md` la Tabla 2, la sección 8, la Tabla 17 y las figuras 1 a 6.
   - Comprobar que los diagramas mermaid se vean bien en GitHub o en VS Code, en especial la Figura 6, que es nueva.
   - Resolver §13.3 y las frases de usabilidad (sección 1.4, punto 7) antes de entregar.
   - Revisar la fila de AP-05 índice no utilizado o redundante en la Tabla 2 (sección 1.4, punto 12).
