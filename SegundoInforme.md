# Segundo Informe - querylens

## Resumen / Abstract

El diagnóstico de problemas de rendimiento y contención en bases de datos relacionales depende hoy de personal DBA especializado, un perfil escaso, y de herramientas comerciales costosas que operan como caja negra. Se propone QUERYLENS, una herramienta agnóstica de motor que recolecta telemetría de PostgreSQL 17 y MySQL 8.0 de forma no intrusiva, la normaliza a un modelo canónico común y detecta de manera determinista un catálogo cerrado de trece patologías (ocho anti-patrones de rendimiento y cinco patologías de contención, cuatro de ellas patrones de abrazo mortal), presentando cada hallazgo con evidencia, causa y recomendación accionable a través de una interfaz web. El alcance corresponde a un MVP funcional y documentado, de solo lectura y sin aprendizaje automático. El desarrollo sigue una metodología de ingeniería de diseño: definición de modelo canónico, evaluación de alternativas mediante matriz ponderada, implementación por componentes y validación en un banco de pruebas con inyección controlada de fallas.

## 1. Introducción

Las bases de datos relacionales constituyen el componente central de la mayoría de las aplicaciones empresariales, desde sistemas transaccionales hasta plataformas de comercio electrónico y servicios financieros, cuyo funcionamiento depende de que las consultas se resuelvan en tiempos predecibles y de que las transacciones concurrentes no interfieran entre sí. En el sector de las tecnologías de la información, la adopción masiva de arquitecturas de microservicios, frameworks de mapeo objeto-relacional (ORM) y el crecimiento sostenido del volumen de datos han convertido el rendimiento de la capa de persistencia en un factor determinante tanto de la experiencia del usuario final como de los costos de infraestructura. En paralelo, la observabilidad de software se ha consolidado como práctica estándar de ingeniería, aunque su aplicación específica a bases de datos (donde la telemetría es heterogénea entre motores y su interpretación exige conocimiento especializado) sigue siendo un área con herramientas fragmentadas y de difícil acceso para equipos sin un administrador de bases de datos dedicado.

En la práctica actual, el diagnóstico de degradaciones de rendimiento depende de que alguien con experiencia interprete planes de ejecución y vistas internas cuyo nombre, formato y semántica cambian de un motor a otro, un perfil con el que no cuenta la mayoría de los equipos de desarrollo. Esto provoca que problemas recurrentes (consultas sin índice adecuado, patrones ineficientes generados por el ORM o transacciones que se bloquean mutuamente) se resuelvan de forma reactiva y superficial, por ejemplo reintentando una transacción fallida en lugar de corregir su causa estructural en el código. Las soluciones comerciales que automatizan este diagnóstico existen, pero su costo de licenciamiento y su naturaleza de caja negra, que no explica el razonamiento detrás de cada hallazgo, las hacen poco accesibles o poco confiables para buena parte de las organizaciones, en particular para equipos pequeños o con baja madurez operativa.

Esta situación revela una necesidad técnica concreta: una herramienta que traduzca la telemetría heterogénea de distintos motores a un modelo de análisis común, que aplique reglas de detección deterministas y auditables en lugar de modelos de caja negra, y que comunique sus hallazgos en un lenguaje accesible para un desarrollador sin formación especializada en administración de bases de datos. Esta necesidad constituye a su vez una oportunidad de diseño de ingeniería completa, que exige resolver la recolección no intrusiva de datos en un sistema en producción, la normalización semántica entre motores con modelos internos distintos, la anonimización de información sensible en el origen y el diseño de una capa de explicación que traduzca evidencia técnica en recomendaciones accionables.

Frente a esta oportunidad se propone QUERYLENS, una herramienta agnóstica de motor que observa bases de datos PostgreSQL y MySQL en operación sin instalar agentes dentro del motor, detecta de forma automática y determinista un catálogo de anti-patrones de rendimiento y patologías de contención (incluyendo el análisis estructural de abrazos mortales mediante la reconstrucción de su grafo de espera) y presenta cada hallazgo con evidencia, explicación y recomendación de acción. Se espera que esta herramienta reduzca el tiempo de diagnóstico de incidentes de rendimiento en equipos sin un especialista dedicado en bases de datos, sirviendo además como base extensible para incorporar motores adicionales en el futuro. Las secciones siguientes presentan el marco conceptual que sustenta la propuesta y detallan el planteamiento del problema, el alcance específico del proyecto, la metodología de desarrollo y el plan de trabajo propuesto para su ejecución.


## 2. Marco conceptual
 
Esta sección presenta los conceptos necesarios para comprender el problema, la solución propuesta y las decisiones técnicas de QUERYLENS, y los vincula con las patologías del catálogo en las que se aplican.
 
### 2.1 Bases de datos relacionales, transacciones y motores
 
Un **sistema gestor de bases de datos relacional** (SGBD), también llamado **motor**, administra datos organizados en tablas, decide cómo ejecutar cada consulta SQL y controla el acceso concurrente. Su unidad de trabajo es la **transacción**, un conjunto de operaciones que se confirma o se revierte como un todo y que cumple las propiedades ACID: atomicidad, consistencia, aislamiento y durabilidad [1]. Las patologías de contención del catálogo son consecuencias no deseadas de cómo los motores garantizan el aislamiento.
 
PostgreSQL integra en el servidor su mecanismo de almacenamiento y control de concurrencia [2]. MySQL, en cambio, separa la capa del servidor de la de **motores de almacenamiento**; **InnoDB** es su motor predeterminado y el que ofrece transacciones, bloqueos por fila y detección de abrazos mortales [3], por lo que el compromiso del proyecto en MySQL se declara sobre tablas InnoDB. Ambos motores usan **control de concurrencia multiversión** (MVCC), de modo que las lecturas ordinarias no bloquean a las escrituras; los conflictos que estudia QUERYLENS ocurren entre transacciones que modifican datos o que leen con bloqueo (`SELECT … FOR SHARE` o `FOR UPDATE`).
 
### 2.2 Ejecución de consultas, índices y anti-patrones de rendimiento
 
El **optimizador basado en costos** de cada motor genera planes alternativos para una consulta, estima su costo a partir de estadísticas sobre los datos y elige el más barato [4]. El resultado es el **plan de ejecución**, un árbol de operadores que se consulta con `EXPLAIN`. Los operadores más relevantes son el **escaneo completo**, que recorre toda la tabla (`Seq Scan` en PostgreSQL; `access_type = ALL` en MySQL), y el **acceso por índice**. QUERYLENS usa `EXPLAIN` sin `ANALYZE`, porque esta última variante ejecuta la consulta y contradiría el principio de solo lectura.
 
La calidad del plan depende de la **estimación de cardinalidad**, es decir, de cuántas filas predice el optimizador para cada nodo. Cuando las columnas están correlacionadas, el supuesto de independencia entre condiciones produce errores grandes, que se miden con el ***q-error***: el máximo entre los cocientes estimado/real y real/estimado [5]. Un valor de 10 indica un error de un orden de magnitud, que es el umbral de AP-08.
 
Un **índice** de árbol B+ permite localizar filas sin recorrer la tabla, y un índice compuesto solo se aprovecha cuando la consulta filtra por su **prefijo izquierdo** [6]. La **selectividad** de una condición es la fracción de filas que la cumplen; un escaneo completo es evitable (AP-02) cuando recorre una tabla grande para devolver pocas filas. Un predicado es **sargable** cuando el motor puede usar un índice para evaluarlo; deja de serlo si la columna aparece dentro de una función, una expresión o una conversión de tipo (AP-03). Como cada índice encarece las escrituras, uno que no se usa o que duplica a otro impone un costo sin beneficio (AP-05).
 
Dos anti-patrones adicionales surgen del uso de memoria y de la aplicación. El **vertido a disco** ocurre cuando una operación de ordenamiento o agrupamiento excede la memoria de trabajo asignada (`work_mem` en PostgreSQL; `sort_buffer_size` y `tmp_table_size` en MySQL) y escribe datos intermedios en archivos temporales (AP-07). El **patrón N+1** aparece cuando un mapeador objeto-relacional (ORM) con **carga perezosa** ejecuta una consulta para obtener N registros y luego una consulta adicional por cada uno [7]; cada consulta es rápida, pero su repetición multiplica el trabajo (AP-06).
 
### 2.3 Concurrencia, bloqueos y abrazos mortales
 
Un **bloqueo** reserva un recurso para evitar modificaciones incompatibles. Un bloqueo **compartido** (S) admite otros compartidos, mientras que uno **exclusivo** (X) no admite ningún otro; cuando la solicitud es incompatible, la transacción **espera**. Si dos transacciones mantienen un bloqueo compartido sobre el mismo recurso e intentan convertirlo en exclusivo, cada una espera a la otra (CT-02). Ni PostgreSQL ni InnoDB realizan **escalada de bloqueos** de fila a tabla, razón por la cual el catálogo usa la conversión de modo en lugar de ese patrón.
 
El nivel de aislamiento predeterminado es READ COMMITTED en PostgreSQL y REPEATABLE READ en InnoDB [2], [8]. Bajo este último, InnoDB bloquea no solo registros sino también los **huecos** entre ellos (bloqueos de hueco y *next-key*) para impedir inserciones, lo que origina CT-03; además, si un `UPDATE` o `DELETE` no tiene un índice utilizable, bloquea todos los registros que recorre, lo que origina CT-04. PostgreSQL no usa bloqueos de hueco y solo bloquea las filas que cumplen el predicado, por lo que estos dos patrones no le aplican.
 
Un **abrazo mortal** ocurre cuando un conjunto de transacciones queda detenido porque cada una espera un recurso que mantiene otra del conjunto; requiere exclusión mutua, retención y espera, no expropiación y espera circular [9]. Se analiza con el **grafo de espera**, en el que una arista de Ti a Tj indica que Ti espera un recurso de Tj; hay abrazo mortal si y solo si el grafo contiene un **ciclo**. Ambos motores lo detectan, revierten una transacción **víctima** y registran un informe del ciclo: PostgreSQL en el registro del servidor, e InnoDB en `SHOW ENGINE INNODB STATUS` o en el registro de errores [10]. Revertir la víctima resuelve el episodio, pero no su causa; por eso QUERYLENS identifica el **patrón estructural** del ciclo y calcula una **firma normalizada** (huellas, tablas, índices y modos implicados) para reconocer episodios recurrentes. Cuando no hay ciclo, sino una cadena de sesiones que esperan a una **transacción larga** que no confirma, se produce una **cascada de bloqueo** que el motor no resuelve por sí mismo (CT-05).
 
### 2.4 Observabilidad y telemetría de bases de datos
 
La **observabilidad** es la capacidad de comprender el estado interno de un sistema a partir de los datos que emite, llamados **telemetría** [11]. En los SGBD, la telemetría incluye estadísticas acumuladas por sentencia (`pg_stat_statements` en PostgreSQL [12] y `performance_schema` en MySQL [13]), estadísticas de tablas e índices, el estado de sesiones y bloqueos, que se obtiene por **muestreo**, y eventos como los informes de abrazo mortal. Para agrupar ejecuciones de una misma sentencia, los motores sustituyen sus literales por marcadores y calculan una **huella**: `queryid` en PostgreSQL y `DIGEST` en MySQL.
 
Las estadísticas acumuladas son contadores que solo crecen, así que QUERYLENS toma **instantáneas** periódicas y calcula su diferencia en cada **ventana de evaluación** de 60 s. La **línea base** de una sentencia es su comportamiento en ventanas previas, y AP-01 la calcula con la mediana por ser menos sensible a valores atípicos. La Tabla 1 muestra que la información de ambos motores es conceptualmente equivalente pero difiere en nombre, estructura y unidades, lo que motiva el modelo canónico.
 
**Tabla 1.** Correspondencia entre conceptos de telemetría y las interfaces de cada motor.
 
| Concepto | PostgreSQL 17 | MySQL 8.0 (InnoDB) |
|---|---|---|
| Huella de sentencia | `queryid` | `DIGEST` |
| Estadísticas por sentencia | `pg_stat_statements` (milisegundos) | `events_statements_summary_by_digest` (picosegundos) |
| Estadísticas de índices | `pg_stat_user_indexes` | `table_io_waits_summary_by_index_usage` |
| Sesiones y transacciones | `pg_stat_activity` | `INNODB_TRX` |
| Bloqueos y esperas | `pg_locks`, `pg_blocking_pids()` | `data_locks`, `data_lock_waits` |
| Informe de abrazo mortal | Registro del servidor | Informe de InnoDB |
| Escaneo completo en el plan | `Seq Scan` | `access_type = ALL` |
 
La **recolección sin agente** (*agentless*) se conecta al motor como un cliente SQL autorizado y consulta sus interfaces públicas, sin instalar software en el servidor. Toda recolección consume recursos, por lo que introduce un **sobrecosto**, que el proyecto mide sobre las **transacciones por segundo** (TPS) y la **latencia p95** (el tiempo por debajo del cual responde el 95 % de las transacciones) de la carga observada.
 
### 2.5 Modelo canónico y anonimización
 
Un **modelo canónico de datos** es una representación común a la que cada fuente se traduce mediante un componente específico [14]. En QUERYLENS define entidades como sentencia, plan, bloqueo y transacción; las **funciones de traducción** convierten la telemetría de cada motor a ellas, y las reglas se escriben una sola vez sobre el modelo, lo que hace a la herramienta **agnóstica del motor**. Los datos que un motor no ofrece se marcan como **no aplicables**, y la traducción se verifica con **pruebas de contrato** sobre instantáneas de referencia.
 
Algunas fuentes de telemetría contienen **literales** reales de las sentencias, que pueden incluir datos personales. QUERYLENS los **anonimiza en origen**, antes de cualquier persistencia, en coherencia con el régimen colombiano de protección de datos personales [15], y lo verifica inyectando **literales marcadores** que no deben aparecer en su almacenamiento.
 
### 2.6 Detección determinista y validación
 
Una **regla de detección** es una condición explícita sobre la evidencia, con **umbrales** declarados, que produce un **hallazgo**. La detección es **determinista** si la misma entrada produce siempre el mismo resultado, y **auditable** si cualquier persona puede revisar la regla y la evidencia de cada hallazgo; a diferencia de un modelo de aprendizaje automático, este enfoque permite explicar el porqué de cada alerta. Un hallazgo es **accionable** cuando indica causa, evidencia, recomendación y el **riesgo** de aplicarla.
 
La validación usa **inyección de fallas**: se introducen defectos a propósito para comprobar si se detectan [16]. Como cada escenario provoca una patología conocida, se dispone de una **verdad por construcción**, registrada en un **manifiesto**, y las **corridas de control** sin inyección miden las falsas alarmas. Al comparar, cada resultado es verdadero positivo (VP), falso positivo (FP) o falso negativo (FN), y con ellos se calculan la **precisión**, VP / (VP + FP), y la **exhaustividad**, VP / (VP + FN) [17]. Para evitar el **sobreajuste**, los umbrales se **calibran** con corridas distintas de las de validación y se congelan antes de esta. La carga base se genera con **sysbench** [18], cuyo perfil `oltp_read_write` simula una carga transaccional típica.

## 3. Planteamiento del problema

El problema central que aborda este proyecto es la carencia, en equipos de desarrollo y operación de aplicaciones empresariales, de capacidad efectiva para diagnosticar de forma oportuna las causas reales de la degradación de rendimiento y de los episodios de contención en sus bases de datos relacionales. Esta carencia se manifiesta en la práctica como un diagnóstico artesanal, dependiente de personal DBA especializado (un perfil escaso en la mayoría de las organizaciones) y en la resolución superficial de incidentes recurrentes, que se atienden sin corregir su causa estructural. No se trata de la ausencia de una herramienta específica, sino de una situación deficiente y verificable: la información necesaria para explicar estos incidentes existe dentro de los motores de base de datos, pero resulta inaccesible en la práctica para quien no cuenta con formación especializada, debido a su heterogeneidad y complejidad técnica. Esta problemática es relevante porque afecta tanto la eficiencia operativa de los equipos técnicos como la experiencia de los usuarios finales de las aplicaciones, y es transferible a cualquier organización que dependa de bases de datos relacionales sin contar con un especialista dedicado, lo que la sitúa por encima de un caso particular de empresa.

### 3.1 Descripción del problema

Los equipos de desarrollo y operación de aplicaciones empresariales que dependen de bases de datos relacionales enfrentan de forma recurrente incidentes de degradación de rendimiento y de contención (consultas que se vuelven lentas, transacciones que se bloquean mutuamente, escaneos de tabla innecesarios) cuya causa raíz permanece sin diagnosticar o se diagnostica de forma tardía e incompleta. Esto ocurre porque la información necesaria para explicar estos incidentes existe dentro del motor de base de datos, pero se encuentra dispersa en vistas y estadísticas internas cuyo nombre, formato y semántica cambian de un motor a otro, y cuya interpretación requiere conocimiento especializado en administración de bases de datos (DBA). La mayoría de los equipos de desarrollo no cuenta con ese perfil de forma dedicada, por lo que el diagnóstico recae en desarrolladores sin la formación necesaria para leer planes de ejecución o reconstruir manualmente las causas de un bloqueo entre transacciones.
 
Como consecuencia directa, estos equipos resuelven los incidentes de forma reactiva y superficial: reintentan una transacción que falló por un abrazo mortal en lugar de corregir el orden de acceso a los datos que lo originó, o normalizan como "lentitud esporádica" una consulta que en realidad dejó de usar un índice disponible. Esta forma de resolución no elimina la causa estructural del problema, por lo que los mismos incidentes tienden a repetirse con el tiempo, generando una acumulación de deuda técnica no diagnosticada. Las consecuencias afectan tanto a los equipos técnicos (que dedican tiempo desproporcionado a depurar manualmente incidentes recurrentes sin herramientas adecuadas) como a los usuarios finales de las aplicaciones, que experimentan tiempos de respuesta degradados y, en los casos más severos, fallas transaccionales visibles. Esta problemática no es exclusiva de una organización particular: se manifiesta en cualquier contexto donde exista una aplicación empresarial sobre una base de datos relacional y un equipo de desarrollo sin un especialista en bases de datos dedicado, lo que la convierte en una situación transferible a un amplio espectro de organizaciones de distintos tamaños y sectores.

### 3.2 Restricciones y supuestos de diseño
 
El desarrollo de QUERYLENS estará condicionado por restricciones de tiempo, recursos, infraestructura y disponibilidad de entornos de validación, las cuales determinan las decisiones de alcance y las características de la solución. Asimismo, se establecen supuestos iniciales sobre las condiciones técnicas necesarias para implementar y validar el sistema.
 
#### Restricciones
 
* **Tiempo y alcance:** el proyecto deberá desarrollarse dentro del cronograma académico establecido, por lo que el soporte se limitará a PostgreSQL 17 y MySQL 8.0 con motor de almacenamiento InnoDB, versiones sobre las cuales se declara el compromiso del MVP. La incorporación de SQL Server será opcional y estará condicionada al cumplimiento de los objetivos principales del proyecto.
* **Recursos disponibles:** el desarrollo se realizará con los recursos técnicos y humanos disponibles para el proyecto académico. Por esta razón, se priorizará la implementación de las funcionalidades esenciales del MVP sobre características de operación a escala productiva, como alta disponibilidad, multi-tenencia o despliegue en la nube.
* **Acceso a entornos reales:** no se asumirá la disponibilidad permanente de bases de datos empresariales o de equipos de desarrollo y operación para realizar las pruebas. La validación principal deberá poder realizarse de manera independiente mediante un banco de pruebas reproducible con patologías inyectadas de forma controlada.
* **Infraestructura de pruebas:** el banco de pruebas deberá poder desplegarse en contenedores y contar con cargas de trabajo y scripts que permitan reproducir de manera controlada las patologías contempladas en el catálogo de detección.
* **Acceso a la telemetría:** la recolección dependerá de las interfaces públicas de observabilidad disponibles en cada motor soportado y de los permisos necesarios para consultar dicha información. QUERYLENS no podrá depender de modificaciones internas del motor ni de la instalación de agentes dentro de la base de datos.
* **Sobrecosto de recolección:** la capa de recolección deberá mantener un sobrecosto inferior al 5 % sobre la métrica de rendimiento de la carga observada, medido como la reducción de transacciones por segundo (TPS) y el aumento de la latencia p95 de la carga sysbench al activar el recolector. Este requisito limita la frecuencia y cantidad de información que puede recolectarse y obliga a evaluar el impacto de las estrategias de observabilidad implementadas.
* **Heterogeneidad entre motores:** las diferencias de nomenclatura, estructura y semántica de las interfaces de observabilidad de PostgreSQL y MySQL pueden limitar la equivalencia de la información recolectada. Por ello, la normalización deberá preservar la información diagnóstica relevante sin asumir que todos los motores proporcionan los mismos datos.
* **Privacidad de la información:** los literales potencialmente sensibles deberán anonimizarse durante la recolección y antes de cualquier persistencia, limitando la exposición de información propia de las bases de datos analizadas.
* **Intervención sobre las bases de datos:** QUERYLENS operará en modo de solo lectura. La herramienta no ejecutará acciones correctivas, modificará parámetros, alterará esquemas ni intervendrá automáticamente sobre las transacciones analizadas.
#### Supuestos iniciales
 
* **Disponibilidad de interfaces de observabilidad:** se asume que PostgreSQL y MySQL proporcionarán interfaces públicas de observabilidad suficientemente accesibles para obtener las estadísticas, planes de ejecución, sesiones, bloqueos y demás telemetría requerida por el modelo canónico. En particular, se asume que en PostgreSQL la extensión pg_stat_statements está cargada y el registro del servidor, con log_lock_waits activo, es legible en modo de solo lectura; y que en MySQL performance_schema está habilitado e innodb_print_all_deadlocks está activo. Estas condiciones las configura el operador del entorno, no QUERYLENS.
* **Permisos de consulta:** se asume que el entorno donde se utilice QUERYLENS podrá proporcionar las credenciales y permisos necesarios para consultar la información de observabilidad sin requerir privilegios que impliquen modificar la configuración o los datos de la base de datos.
* **Reproducibilidad del banco de pruebas:** se asume que las patologías definidas en el catálogo pueden ser reproducidas mediante cargas controladas y scripts de inyección dentro de un entorno contenerizado, permitiendo establecer etiquetas conocidas para la evaluación de la herramienta.
* **Validación determinista:** se asume que las patologías incluidas en el catálogo pueden ser identificadas mediante reglas y umbrales observables y auditables, sin depender de modelos de aprendizaje automático entrenados.
* **Entorno real como enriquecimiento:** se asume que la participación de equipos de desarrollo u operación reales puede aportar retroalimentación adicional sobre la utilidad de QUERYLENS, pero su disponibilidad no constituye una condición necesaria para completar la validación principal del proyecto.
* **Versiones controladas de los motores:** se asume que las versiones de PostgreSQL (17) y MySQL (8.0) utilizadas durante el desarrollo y las pruebas podrán mantenerse fijadas en las imágenes de contenedor dentro del banco de pruebas, permitiendo reproducir los resultados y realizar pruebas de regresión ante cambios en las interfaces de observabilidad.

### 3.3 Alcance actualizado

El alcance de QUERYLENS comprende el diseño, construcción y validación de un prototipo funcional documentado para el análisis de rendimiento de consultas y problemas de contención en bases de datos relacionales. La solución se desarrollará bajo un enfoque agnóstico del motor, mediante la recolección no intrusiva de telemetría, su normalización a un modelo común y la generación de hallazgos explicables y accionables. El alcance se establece de la siguiente manera:
 
### Incluye
 
**Funcionalidades principales:**
 
- **Soporte multi-motor:** soporte para PostgreSQL 17 y MySQL 8.0 (InnoDB), que son las versiones fijadas en el banco de pruebas y sobre las cuales se declara el catálogo comprometido. SQL Server se contempla como una extensión opcional, condicionada al cumplimiento del cronograma.
- **Recolección sin agente:** obtención de telemetría mediante interfaces públicas de observabilidad de los motores, incluyendo estadísticas agregadas por sentencia, muestreo de sesiones activas, planes de ejecución, eventos de bloqueo y estadísticas de objetos. En PostgreSQL se consultan pg_stat_statements, pg_stat_activity junto con pg_blocking_pids(), pg_locks, pg_stat_user_tables, pg_stat_user_indexes, pg_stat_database, EXPLAIN (FORMAT JSON) sin ANALYZE y el registro del servidor para los informes de abrazo mortal. En MySQL se consultan performance_schema.events_statements_summary_by_digest, performance_schema.data_locks y data_lock_waits, information_schema.INNODB_TRX, performance_schema.table_io_waits_summary_by_index_usage, EXPLAIN FORMAT=JSON y los informes de abrazo mortal de InnoDB (performance_schema.error_log o SHOW ENGINE INNODB STATUS). Las frecuencias declaradas son: sesiones, bloqueos e informes de abrazo mortal cada 5 s; sentencias, tablas e índices cada 60 s (ventana de evaluación); y planes de ejecución solo para un máximo de 10 sentencias candidatas por ventana. Ninguna de estas consultas ejecuta las sentencias analizadas.
- **Normalización y anonimización:** transformación de la información recolectada hacia un modelo canónico común y anonimización de literales en el punto de recolección, antes de cualquier persistencia.
- **Detección de anti-patrones de rendimiento:** identificación determinista de un catálogo cerrado de ocho anti-patrones, aplicables tanto a PostgreSQL como a MySQL: degradación del tiempo respecto a una línea base (AP-01), escaneo completo evitable (AP-02), predicado no sargable (AP-03), índice ausente (AP-04), índice no utilizado o redundante (AP-05), patrón N+1 (AP-06), vertido a disco por memoria de trabajo insuficiente (AP-07) y error grave de estimación de cardinalidad (AP-08). La Tabla 2 documenta para cada uno la evidencia por motor, la regla con su umbral inicial y el resultado esperado en el banco de pruebas.
- **Análisis de contención y abrazos mortales:** reconstrucción del grafo de espera, detección de ciclos, normalización de firmas y clasificación en cinco patologías de contención (Tabla 3): cuatro patrones estructurales de abrazo mortal, que son orden inconsistente de acceso (CT-01), conversión de modo de bloqueo compartido a exclusivo (CT-02), contención de rango por bloqueos de hueco (CT-03) y ampliación de bloqueos por ausencia de índice (CT-04), más la cascada de bloqueo por transacción larga (CT-05), que se detecta como cadena de espera sin ciclo. El patrón denominado inicialmente escalada de bloqueo se redefine como conversión de modo (CT-02), porque ni PostgreSQL ni InnoDB escalan bloqueos de fila a bloqueos de tabla. CT-03 y CT-04 dependen de la semántica de bloqueo de InnoDB y solo aplican a MySQL. En consecuencia, el compromiso del MVP es de **11 patologías en PostgreSQL** (AP-01 a AP-08, CT-01, CT-02 y CT-05) y **13 en MySQL** (AP-01 a AP-08 y CT-01 a CT-05), es decir, 24 combinaciones patología-motor.
- **Priorización de hallazgos:** organización de los problemas detectados de acuerdo con su impacto, permitiendo identificar las patologías que requieren mayor atención. El impacto se calcula de forma determinista: para los anti-patrones, como el tiempo de base de datos atribuible a las sentencias afectadas en la ventana (diferencia de total_exec_time en PostgreSQL o de SUM_TIMER_WAIT en MySQL); para las patologías de contención, como el tiempo de espera acumulado por el número de sesiones afectadas. La misma entrada produce siempre el mismo orden.
- **Interfaz web de análisis:** presentación de los hallazgos mediante un inventario priorizado, detalle de la evidencia y planes de ejecución, línea de tiempo de la degradación y vista de grafo para los interbloqueos.
- **Explicaciones accionables:** generación de explicaciones para cada hallazgo que indiquen la causa identificada, la evidencia que la sustenta, la acción recomendada y el riesgo asociado a su aplicación.
- **Banco de pruebas reproducible:** ejecución de pruebas en contenedores mediante cargas generadas con una herramienta de *benchmark* (sysbench, perfil oltp_read_write con 4 hilos) y scripts para la inyección controlada de las patologías contempladas en el catálogo. Se definen 22 escenarios de inyección: 7 de anti-patrones por motor (AP-02 se evalúa dentro de los escenarios de AP-01, AP-03 y AP-04), 3 de contención en PostgreSQL y 5 en MySQL, además de corridas de control sin inyección. Cada escenario tiene un manifiesto con los hallazgos esperados por construcción; los hallazgos propios de la carga base, como índices de sysbench que la carga no consulta, se registran en el manifiesto de la línea base y no se contabilizan como falsos positivos.
- **Documentación de ingeniería:** elaboración de la especificación de requerimientos, matriz de evaluación de alternativas, vistas de arquitectura, registro de decisiones de diseño, plan de pruebas y manual de despliegue.
El catálogo de las Tablas 2 y 3 constituye el compromiso del MVP. Los umbrales indicados son valores iniciales: se calibran en la iteración de detección con corridas distintas de las de validación y se congelan en una versión del archivo de reglas antes de la validación final (sección 8.3). Se usan las siguientes convenciones: la *ventana de evaluación* es de 60 s; la *huella* de una sentencia es su identificador normalizado (queryid en PostgreSQL, DIGEST en MySQL); y las tablas de escenario de anti-patrones tienen 1.000.000 de filas, salvo que se indique otra cantidad.
 
**Tabla 2.** Anti-patrones de rendimiento comprometidos (aplican a PostgreSQL 17 y MySQL 8.0).
 
| Código y patología | Descripción | Evidencia en PostgreSQL 17 | Evidencia en MySQL 8.0 | Regla de detección (umbral inicial) | Inyección y resultado esperado |
|---|---|---|---|---|---|
| **AP-01** Degradación respecto a la línea base | Una sentencia que antes respondía en un tiempo estable se vuelve notablemente más lenta. | pg_stat_statements: diferencias de calls y total_exec_time por queryid entre instantáneas. | events_statements_summary_by_digest: diferencias de COUNT_STAR y SUM_TIMER_WAIT por DIGEST. | Huella con ≥ 30 ejecuciones en la ventana cuya latencia media es ≥ 2,0 veces la mediana de las 10 ventanas previas (línea base) y al menos 1 ms mayor en valor absoluto. | Tras 10 min de línea base con la sentencia de referencia `SELECT … WHERE k = ?` a ritmo constante, se elimina el índice k_1. Esperado: AP-01 sobre la huella de referencia en ≤ 2 ventanas, vinculado a AP-02 y AP-04 como causa. |
| **AP-02** Escaneo completo evitable | La consulta recorre toda una tabla grande para devolver solo una pequeña parte de sus filas. | Plan con nodo Seq Scan; filas estimadas del nodo; n_live_tup de pg_stat_user_tables; rows/calls de pg_stat_statements. | Plan con access_type = ALL; SUM_ROWS_EXAMINED, SUM_ROWS_SENT y SUM_NO_INDEX_USED del digest. | Huella con ≥ 10 ejecuciones en la ventana cuyo plan recorre completa una tabla de ≥ 10.000 filas vivas y devuelve ≤ 1 % de las filas recorridas. Es el hallazgo base: se vincula a AP-03 o AP-04 cuando alguna de esas causas se verifica. | Se evalúa en los escenarios de AP-01, AP-03 y AP-04. Esperado: AP-02 sobre la sentencia inyectada, con la tabla recorrida y la fracción de filas devueltas. |
| **AP-03** Predicado no sargable | Existe un índice, pero la condición está escrita de forma que el motor no puede usarlo (por ejemplo, una función sobre la columna). | Texto normalizado de pg_stat_statements analizado como árbol sintáctico; índices de pg_index; plan con Seq Scan. | DIGEST_TEXT analizado como árbol sintáctico; índices de information_schema.STATISTICS; plan con access_type = ALL. | Se cumple AP-02 y la columna del predicado es la primera columna de un índice existente, pero aparece envuelta en una función, una expresión aritmética o una conversión explícita de tipo (p. ej., `LOWER(col)`, `col + 0`, `DATE(col)`). | Sentencia `SELECT … WHERE k + 0 = ?` sobre la columna indexada k, ≥ 10 veces por ventana. Esperado: AP-03 que identifica la columna k, el índice k_1 no aprovechado y la reescritura que despeja la columna. |
| **AP-04** Índice ausente | La consulta filtra por columnas que no tienen un índice que la respalde. | Plan con Seq Scan y filtro; índices de pg_index; seq_scan, seq_tup_read e idx_scan de pg_stat_user_tables. | Plan con access_type = ALL y condición adjunta; information_schema.STATISTICS; SUM_NO_INDEX_USED. | Se cumple AP-02 y ninguna columna del predicado de igualdad o rango es primera columna de un índice de la tabla. Se recomienda un índice con las columnas de igualdad primero y luego las de rango. | Sentencia `SELECT … WHERE c = ?` sobre la columna c, sin índice. Esperado: AP-04 con la recomendación de un índice sobre c. |
| **AP-05** Índice no utilizado o redundante | Un índice que ninguna consulta usa, o que duplica a otro, encarece las escrituras sin aportar beneficio. | pg_stat_user_indexes (idx_scan, last_idx_scan); pg_index (columnas y unicidad); pg_relation_size; stats_reset de pg_stat_database. | table_io_waits_summary_by_index_usage (COUNT_STAR por índice); information_schema.STATISTICS; Uptime como inicio de la ventana. | No utilizado: índice que no es primario ni único con 0 lecturas en una ventana de observación ≥ 30 min de carga desde el último reinicio de estadísticas. Redundante: sus columnas son prefijo izquierdo, en el mismo orden, de otro índice de la misma tabla y no respalda una restricción de unicidad. | Creación de un índice sobre pad, que la carga no consulta, y de un duplicado de k_1. Esperado: un AP-05 «no utilizado» sobre pad y un AP-05 «redundante» sobre el duplicado, al cerrar la ventana de observación. |
| **AP-06** Patrón N+1 | La aplicación ejecuta una consulta por cada registro de una lista en lugar de obtenerlos todos a la vez. | pg_stat_statements: calls y rows por queryid; claves foráneas de pg_constraint. | Digest: COUNT_STAR y SUM_ROWS_SENT; information_schema.KEY_COLUMN_USAGE. | Una huella «hija» con predicado de igualdad sobre la clave foránea o primaria que relaciona su tabla con la de una huella «padre», con ≥ 100 ejecuciones en la ventana, cuyo número de ejecuciones coincide (±20 %) con las filas devueltas por la huella padre en la misma ventana. | Script que lee 50 registros de una tabla padre y consulta la tabla hija una vez por registro, en bucle. Esperado: AP-06 que vincula ambas huellas y recomienda una consulta con JOIN o IN. |
| **AP-07** Vertido a disco | Una operación de ordenamiento o agrupamiento excede la memoria de trabajo y escribe datos temporales en disco. | temp_blks_written por queryid en pg_stat_statements; temp_files de pg_stat_database. | SUM_CREATED_TMP_DISK_TABLES y SUM_SORT_MERGE_PASSES del digest. | Huella con ≥ 5 ejecuciones en la ventana y, en PostgreSQL, un promedio ≥ 128 bloques temporales escritos (1 MB) por ejecución; en MySQL, SUM_CREATED_TMP_DISK_TABLES / COUNT_STAR ≥ 0,5 o SUM_SORT_MERGE_PASSES > 0. | Scripts simulate_diskspill_*: GROUP BY y ORDER BY con work_mem = 64 kB (PostgreSQL) o con tmp_table_size y sort_buffer_size reducidos (MySQL) en la sesión inyectora. Esperado: AP-07 con la recomendación de ajustar la memoria de trabajo o reducir el volumen ordenado. |
| **AP-08** Error grave de estimación de cardinalidad | El optimizador predice un número de filas muy distinto del real y puede elegir un plan inadecuado. | Filas estimadas del nodo raíz en EXPLAIN (FORMAT JSON) frente a rows/calls de pg_stat_statements. | rows_produced_per_join del último acceso en EXPLAIN FORMAT=JSON frente a SUM_ROWS_SENT / COUNT_STAR. | max(estimadas / reales, reales / estimadas) ≥ 10, con al menos 100 filas en el mayor de ambos valores, excluyendo planes cuya raíz sea un LIMIT o una agregación sin GROUP BY. Limitación declarada: solo se compara la cardinalidad de salida, no la de nodos intermedios, porque QUERYLENS no ejecuta EXPLAIN ANALYZE. | Tabla de 100.000 filas con tres columnas perfectamente correlacionadas (a = b = c, 10 valores), sin estadísticas extendidas ni histogramas, consultada con `WHERE a = ? AND b = ? AND c = ?`. Esperado: AP-08 (≈ 100 filas estimadas frente a ≈ 10.000 reales) con la recomendación de CREATE STATISTICS en PostgreSQL o de histogramas en MySQL. |
 
**Tabla 3.** Patologías de contención comprometidas por motor.
 
| Código y patología | Descripción | Evidencia en PostgreSQL 17 | Evidencia en MySQL 8.0 | Regla de detección (umbral inicial) | Inyección y resultado esperado |
|---|---|---|---|---|---|
| **CT-01** Abrazo mortal por orden inconsistente de acceso | Dos transacciones bloquean los mismos recursos en órdenes opuestos y cada una espera a la otra. | Mensaje «deadlock detected» del registro del servidor (procesos, modos, relaciones y sentencias); diferencia de pg_stat_database.deadlocks. | Informe de abrazo mortal de InnoDB (transacciones, sentencias, índice, modo y tipo de bloqueo). | Ciclo de ≥ 2 transacciones en el grafo de espera en el que cada una mantiene en modo exclusivo el recurso que la otra solicita, adquiridos en orden opuesto. Firma normalizada: conjunto ordenado de (huella, tabla, índice, modo). | Dos sesiones actualizan las filas A y B en órdenes opuestos con una pausa entre ambas sentencias. Esperado: CT-01 con el ciclo de dos transacciones, las filas implicadas y la recomendación de imponer un orden de acceso único. |
| **CT-02** Abrazo mortal por conversión de modo (compartido a exclusivo) | Dos transacciones leen el mismo registro con bloqueo compartido y luego intentan modificarlo. | Igual que CT-01. | Igual que CT-01. | Ciclo en el que todas las transacciones mantienen un bloqueo compartido (FOR SHARE) sobre el mismo recurso y cada una solicita un bloqueo exclusivo sobre él. | Dos sesiones ejecutan `SELECT … FOR SHARE` sobre la misma fila y luego `UPDATE` sobre ella. Esperado: CT-02 con la recomendación de adquirir el bloqueo exclusivo desde el inicio (`SELECT … FOR UPDATE`). |
| **CT-03** Abrazo mortal por contención de rango | Los bloqueos sobre los huecos entre registros de un índice impiden inserciones y forman un ciclo (solo MySQL). | No aplica: PostgreSQL no usa bloqueos de hueco. | Informe de abrazo mortal con modos X,GAP y X,INSERT_INTENTION; performance_schema.data_locks. | Ciclo en el que al menos un bloqueo esperado es de intención de inserción y está bloqueado por un bloqueo de hueco o *next-key* sobre el mismo índice. | Con REPEATABLE READ, dos sesiones ejecutan `SELECT … FOR UPDATE` sobre un valor inexistente del mismo rango y luego insertan en él. Esperado: CT-03 con el índice y el rango afectados. |
| **CT-04** Ampliación de bloqueos por ausencia de índice | Una modificación sin índice utilizable bloquea muchas más filas de las que cambia y provoca un ciclo (solo MySQL). | No aplica: PostgreSQL solo bloquea las filas que cumplen el predicado. | Informe de abrazo mortal (bloqueos de fila y entradas de *undo* por transacción); INNODB_TRX (trx_rows_locked, trx_rows_modified); EXPLAIN de la sentencia. | Ciclo en el que una transacción ejecuta UPDATE o DELETE sin índice utilizable en su predicado (access_type = ALL) y mantiene ≥ 10 veces más bloqueos de fila que filas modificadas. | Una sesión actualiza una fila por clave primaria; otra ejecuta `UPDATE … WHERE c = ?` sin índice y queda en espera; la primera ejecuta un UPDATE análogo y cierra el ciclo. Esperado: CT-04 vinculado a AP-04, con la recomendación de un índice sobre c. |
| **CT-05** Cascada de bloqueo por transacción larga | Una transacción abierta sin confirmar retiene bloqueos y detiene en cadena a otras sesiones. | pg_stat_activity (xact_start, state), pg_blocking_pids() y pg_locks, muestreados cada 5 s. | INNODB_TRX (trx_started), data_lock_waits y processlist, muestreados cada 5 s. | En dos muestras consecutivas, una transacción raíz con antigüedad ≥ 30 s bloquea directa o transitivamente a ≥ 2 sesiones que esperan ≥ 5 s. | Una sesión abre una transacción, actualiza una fila y permanece inactiva 120 s sin confirmar, mientras 5 sesiones intentan actualizar la misma fila. Esperado: CT-05 que identifica la sesión raíz, su antigüedad y estado, y las sesiones bloqueadas. |
 
**Tipo de usuarios:**
 
- **Desarrolladores y equipos de desarrollo u operación:** la solución estará orientada principalmente a usuarios que necesiten diagnosticar problemas de rendimiento y contención sin contar necesariamente con formación especializada en administración de bases de datos.
**Nivel de madurez:**

**Nivel de madurez:**

- **MVP / prototipo funcional documentado:** el proyecto tendrá como resultado una versión funcional y validable de QUERYLENS, acompañada de la documentación técnica necesaria para demostrar su diseño, implementación, pruebas y despliegue.
**Entornos cubiertos:**

**Entornos cubiertos:**

- **Arquitectura de la solución:** la solución contemplará una arquitectura compuesta por una capa de recolección agentless y backend de análisis, una interfaz web para la consulta y visualización de resultados, y un banco de pruebas reproducible desplegado en contenedores, con cargas de trabajo y scripts para la inyección controlada de las patologías. La arquitectura estará preparada para trabajar con los motores relacionales soportados mediante funciones de traducción hacia un modelo canónico común.
### No incluye
 
- **Ejecución automática de acciones correctivas:** QUERYLENS funcionará bajo un principio de solo lectura. La herramienta identificará, explicará y recomendará acciones, pero no ejecutará modificaciones sobre consultas, esquemas, parámetros o transacciones de las bases de datos.
- **Motores y tecnologías fuera del alcance definido:** no se contempla soporte para bases de datos no relacionales ni para almacenes analíticos. El soporte de SQL Server será únicamente una extensión opcional y no una condición necesaria para el cumplimiento del alcance base.
- **Patologías fuera del catálogo:** solo se comprometen las patologías de las Tablas 2 y 3. Quedan fuera, entre otras, el crecimiento excesivo de tablas por versiones muertas (*bloat*), el retraso de replicación, la saturación de conexiones, la configuración general del servidor, las fallas de serialización bajo aislamiento SERIALIZABLE y la estimación de cardinalidad en nodos intermedios del plan.
- **Modelos de aprendizaje automático entrenados:** la detección de patologías será determinista, basada en reglas, umbrales calibrados y evidencia trazable. El uso opcional de un modelo de lenguaje, si se incorpora, estará restringido a la redacción de explicaciones fundamentadas en evidencia estructurada y no podrá introducir causas no sustentadas.
- **Sintonización y corrección automática:** no se desarrollarán mecanismos para ajustar automáticamente parámetros del motor, rediseñar esquemas o aplicar cambios correctivos sobre las bases de datos.
- **Escala y operación productiva:** no se contempla implementar alta disponibilidad, capacidades de multi-tenencia ni despliegue en la nube de la propia herramienta. Estas características quedan fuera del alcance del MVP y corresponden a posibles evoluciones posteriores del producto.
- **Soporte post-proyecto:** el alcance se limita a la construcción, documentación, pruebas y validación de la solución durante el proyecto. No comprende la operación permanente, mantenimiento evolutivo ni soporte posterior a la finalización del proyecto.

## 4. Objetivos
 
### 4.1 Objetivo General
 
Diseñar, construir y validar, al cierre del proyecto, una herramienta funcional y agnóstica del motor de base de datos que, a partir de telemetría recolectada de forma no intrusiva y en modo de solo lectura, detecte, priorice y explique las patologías del catálogo declarado en la sección 4 (11 en PostgreSQL 17 y 13 en MySQL 8.0), alcanzando sobre fallas inyectadas en el banco de pruebas una exhaustividad y una precisión de al menos 0,90 por patología y motor, con un sobrecosto de recolección inferior al 5 %, y presentando cada hallazgo con evidencia trazable, causa, recomendación accionable y riesgo, para facilitar su diagnóstico por parte de equipos de desarrollo.
 
### 4.2 Objetivos Específicos
 
1. Definir un modelo canónico para representar sentencias, planes de ejecución, eventos de espera y bloqueos, junto con las funciones de traducción de PostgreSQL 17 y MySQL 8.0, de modo que todos los campos de evidencia que exigen las reglas de las Tablas 2 y 3 se obtengan de ambos motores o queden marcados explícitamente como no aplicables. Se comprueba con pruebas de contrato automatizadas sobre instantáneas de referencia de cada motor, que deben aprobarse en su totalidad.
2. Evaluar las alternativas de recolección, almacenamiento y visualización mediante una matriz de criterios ponderados, con al menos tres alternativas por decisión y criterios que incluyan el sobrecosto medido en el banco de pruebas, documentando la alternativa seleccionada, sus compromisos y las razones que la sustentan en un registro de decisión por cada una.
3. Implementar la capa de recolección multi-motor *agentless* sobre las interfaces y con las frecuencias declaradas en la sección 4, verificando en el banco de pruebas que el sobrecosto sea inferior al 5 % tanto en TPS como en latencia p95 de la carga sysbench en ambos motores, y que ningún literal sensible se persista (0 apariciones de los literales marcadores inyectados en el almacenamiento de QUERYLENS al finalizar la campaña de validación).
4. Implementar el motor de detección determinista para los anti-patrones AP-01 a AP-08 en PostgreSQL y MySQL (16 combinaciones patología-motor), alcanzando en cada combinación una exhaustividad y una precisión de al menos 0,90 sobre 10 inyecciones por escenario más las corridas de control, con las latencias de detección declaradas en la métrica M4 y con el 100 % de los hallazgos trazables a la instantánea de telemetría que los sustenta.
5. Implementar el módulo de análisis de contención para CT-01, CT-02 y CT-05 en PostgreSQL y CT-01 a CT-05 en MySQL (8 combinaciones patología-motor), reconstruyendo el grafo de espera y clasificando el patrón con una exhaustividad y una precisión de al menos 0,90 por combinación, identificando correctamente las transacciones y recursos del ciclo o de la cadena en al menos el 95 % de los casos detectados, y emitiendo el hallazgo dentro de las latencias declaradas en la métrica M4 (≤ 15 s tras el abrazo mortal para CT-01 a CT-04).
6. Construir la interfaz web de análisis y la capa de explicación accionable, orientadas a desarrolladores sin formación especializada en administración de bases de datos, de modo que el 100 % de los hallazgos generados en la campaña de validación muestre causa, evidencia enlazada, recomendación y riesgo, y que la priorización produzca el mismo orden ante la misma entrada, verificado mediante una lista de chequeo automatizada.
7. Validar la solución mediante un banco de pruebas reproducible en contenedores con versiones fijadas, que ejecute con un solo comando los 22 escenarios de inyección (10 repeticiones cada uno) y 10 corridas de control por motor, calcule automáticamente las métricas de la Tabla 4 frente a las etiquetas conocidas por construcción y entregue los mismos veredictos de cumplimiento al repetirse desde un entorno limpio.
8. Recopilar retroalimentación complementaria de al menos dos equipos de desarrollo u operación, cuando sea posible, mediante sesiones en las que cada participante diagnostique con QUERYLENS al menos dos hallazgos del banco de pruebas y califique la claridad de las explicaciones en una escala de 1 a 5, para contrastar los requerimientos y la utilidad de los hallazgos. Estos resultados se reportan como evidencia complementaria, sin umbral de aprobación, y su disponibilidad no constituye una dependencia para la validación principal del proyecto.

## 5. Estado del arte / soluciones relacionadas

El diagnóstico del rendimiento y de los problemas de contención en bases de datos relacionales cuenta actualmente con diversas soluciones comerciales y de código abierto orientadas a la observabilidad, el análisis de consultas y la identificación de problemas de rendimiento. Para este estado del arte se seleccionaron cinco soluciones representativas: **pganalyze, SolarWinds Database Performance Analyzer, Quest Foglight, Redgate Monitor y Percona Monitoring and Management (PMM)**. La selección permite analizar diferentes enfoques, desde herramientas especializadas en un motor hasta plataformas de observabilidad multi-motor.

### 5.1 Soluciones comerciales

#### 5.1.1 pganalyze

pganalyze es una solución comercial especializada en la observabilidad y optimización del rendimiento de PostgreSQL. Entre sus funcionalidades se encuentran el análisis de estadísticas de consultas, seguimiento de latencia, análisis de planes de ejecución, recomendaciones de optimización, análisis de índices, información sobre conexiones y alertas. Su módulo Query Advisor analiza automáticamente los planes de ejecución para identificar oportunidades de optimización y proporcionar sugerencias accionables. [19]

Una característica relevante es que pganalyze utiliza un componente denominado *pganalyze Collector* para recopilar información del entorno. Este componente debe instalarse en el servidor de base de datos o en un contenedor o máquina virtual que pueda conectarse a este. [20]

pganalyze constituye un antecedente importante para QUERYLENS por su profundidad en el análisis de consultas y planes de ejecución. Sin embargo, su especialización se encuentra en PostgreSQL y su arquitectura de recolección utiliza un componente dedicado. QUERYLENS plantea, en cambio, una arquitectura multi-motor con una capa de recolección *agentless* y un modelo común para representar la información obtenida de los diferentes motores.

#### 5.1.2 SolarWinds Database Performance Analyzer

SolarWinds Database Performance Analyzer (DPA) es una plataforma comercial orientada al monitoreo, diagnóstico y optimización del rendimiento de bases de datos. La herramienta utiliza análisis basado en tiempos de espera para identificar cuellos de botella y permite investigar consultas con tiempos de espera elevados, consultas ineficientes y anomalías de rendimiento. Además, soporta diferentes sistemas gestores y proporciona una vista centralizada de su rendimiento. [21]

Una característica especialmente relevante para QUERYLENS es su arquitectura *agentless*. La documentación de DPA indica que la herramienta utiliza este enfoque y que el consumo de recursos en sistemas de producción es inferior al 1 %. [22]

DPA representa uno de los antecedentes técnicos más relevantes para QUERYLENS, debido a la combinación de recolección sin agente, análisis de rendimiento y soporte multi-motor. La diferencia principal se encuentra en el enfoque de análisis: QUERYLENS plantea un catálogo explícito de anti-patrones, reglas deterministas, trazabilidad de la evidencia y un modelo canónico que permita abstraer las diferencias entre PostgreSQL y MySQL.

#### 5.1.3 Quest Foglight

Quest Foglight es una plataforma comercial de observabilidad y diagnóstico para diferentes tecnologías de bases de datos. Su arquitectura permite trabajar con distintos motores mediante componentes específicos, incluyendo PostgreSQL y MySQL. [23]

La solución proporciona capacidades orientadas al análisis del rendimiento y de las consultas, además de herramientas para investigar problemas relacionados con eventos y actividad de las bases de datos. Su soporte para múltiples tecnologías la convierte en un antecedente relevante para el enfoque multi-motor de QUERYLENS.

La principal diferencia se encuentra en el objetivo de la solución. Foglight aborda la observabilidad de bases de datos desde una perspectiva empresarial y general, mientras que QUERYLENS plantea una arquitectura centrada en la detección de un conjunto definido de patologías de rendimiento y contención, con reglas deterministas y explicaciones orientadas al usuario final del diagnóstico.

#### 5.1.4 Redgate Monitor

Redgate Monitor es una plataforma comercial de monitoreo que permite supervisar diferentes motores de bases de datos desde una interfaz común. Actualmente soporta, entre otras tecnologías, SQL Server, PostgreSQL, Oracle, MySQL y MongoDB. Proporciona funcionalidades relacionadas con consultas, planes de ejecución, estadísticas de espera, rendimiento, alertas y análisis histórico. [24]

Una característica importante para el análisis de QUERYLENS es que las capacidades disponibles no son idénticas para todos los motores. Por ejemplo, la documentación de Redgate muestra que PostgreSQL dispone de determinadas capacidades de análisis de consultas y esperas, mientras que algunas funcionalidades de bloqueo y deadlocks se encuentran disponibles únicamente para determinados motores. [24]

Esta diferencia evidencia uno de los retos principales del enfoque multi-motor: aunque una plataforma pueda soportar diferentes sistemas gestores, la información disponible y las capacidades de diagnóstico pueden variar entre ellos. QUERYLENS aborda este problema mediante la definición de un modelo canónico y funciones de traducción específicas para cada motor.

### 5.2 Solución de código abierto

#### 5.2.1 Percona Monitoring and Management

Percona Monitoring and Management (PMM) es una plataforma de código abierto orientada a la observabilidad, monitoreo y administración de bases de datos. Actualmente proporciona visibilidad sobre MySQL, PostgreSQL y MongoDB, desde métricas generales de los clústeres hasta información sobre consultas individuales. También permite desplegarse en entornos locales, cloud e híbridos. [25]

PMM constituye un antecedente relevante porque demuestra que es posible construir una plataforma de observabilidad multi-motor utilizando componentes de código abierto. Su arquitectura utiliza dos componentes principales, denominados *Server* y *Client*, para recopilar y centralizar la información de las bases de datos monitorizadas. [25]

Frente a este enfoque, QUERYLENS plantea una arquitectura de recolección *agentless*, en la que no se requiere instalar agentes dentro del servidor de base de datos. Además, mientras PMM busca proporcionar una plataforma general de observabilidad, QUERYLENS concentra el análisis en un catálogo específico de patologías y en la generación de explicaciones trazables y accionables.

### 5.3 Comparación y oportunidad de QUERYLENS

Las soluciones analizadas evidencian que el monitoreo y diagnóstico del rendimiento de bases de datos es un campo con herramientas consolidadas. pganalyze ofrece capacidades especializadas para PostgreSQL, mientras que SolarWinds Database Performance Analyzer, Quest Foglight, Redgate Monitor y Percona Monitoring and Management proporcionan diferentes niveles de observabilidad sobre múltiples motores. Estas soluciones permiten analizar consultas, planes de ejecución, métricas de rendimiento, eventos de espera, bloqueos y otros indicadores relevantes para identificar problemas. A partir de este panorama, QUERYLENS se plantea sobre una problemática concreta: transformar la telemetría heterogénea de PostgreSQL y MySQL en diagnósticos comprensibles, trazables y accionables, manteniendo una arquitectura independiente del motor.

En cuanto a costos y usabilidad, las soluciones comerciales requieren modelos de licenciamiento o suscripción y están orientadas principalmente a equipos especializados en administración y monitoreo de bases de datos. Aunque proporcionan interfaces completas y numerosas capacidades de diagnóstico, su amplitud puede resultar innecesaria para el usuario objetivo de QUERYLENS. La propuesta se orientará específicamente a desarrolladores sin formación especializada en DBA, priorizando una interfaz de análisis que presente los hallazgos de forma comprensible y accionable. En lugar de limitarse a mostrar métricas o alertas, cada detección buscará relacionar la patología identificada con la evidencia que la sustenta, su nivel de riesgo y una posible acción de mejora.

Desde el punto de vista técnico, las soluciones existentes demuestran la viabilidad del monitoreo multi-motor y del análisis de rendimiento, pero las capacidades disponibles pueden variar entre motores y las plataformas estudiadas tienen objetivos más amplios que los definidos para QUERYLENS. La oportunidad del proyecto se encuentra en combinar, dentro de una solución de alcance controlado, una capa de recolección *agentless*, un modelo canónico para sentencias, planes de ejecución, eventos de espera y bloqueos, y un motor determinista para la detección de un catálogo definido de ocho anti-patrones de rendimiento y problemas de contención. A esto se suma la reconstrucción del grafo de espera, la trazabilidad de cada hallazgo hasta su evidencia y la generación de explicaciones accionables. Finalmente, el banco de pruebas reproducible con patologías inyectadas de forma controlada permitirá evaluar objetivamente la precisión y exhaustividad de la detección. De esta manera, QUERYLENS no busca reemplazar las plataformas empresariales existentes, sino abordar de manera específica y auditable el diagnóstico de patologías de rendimiento y contención desde la perspectiva de un desarrollador.

## 6. Solución propuesta

Para responder al problema descrito, se propone QUERYLENS, una herramienta de análisis que observa bases de datos relacionales en operación y traduce su telemetría interna en hallazgos explicables sobre problemas de rendimiento y de contención, sin requerir que quien la use tenga formación especializada en administración de bases de datos. La solución está dirigida principalmente a desarrolladores y equipos de desarrollo u operación que necesitan diagnosticar por qué una aplicación se volvió lenta o por qué ocurren bloqueos entre transacciones, pero que no cuentan con un DBA dedicado que interprete manualmente planes de ejecución y vistas internas del motor.
 
A alto nivel, QUERYLENS funciona en tres etapas encadenadas. En la primera, una capa de recolección sin agente se conecta a la base de datos como lo haría cualquier cliente SQL autorizado, y consulta periódicamente las interfaces públicas de observabilidad que cada motor ya expone (estadísticas por sentencia, sesiones activas, planes de ejecución, eventos de bloqueo y estadísticas de tablas e índices), sin instalar ni modificar nada dentro del propio motor. En la segunda etapa, esa información heterogénea se normaliza a un modelo canónico común, de modo que una consulta, un plan de ejecución o un evento de bloqueo se representen de la misma forma sin importar si provienen de PostgreSQL o de MySQL; en este mismo punto se anonimizan los literales sensibles antes de guardar cualquier dato. Sobre esa representación uniforme actúa la tercera etapa, un motor de detección determinista que aplica las reglas y umbrales de la Tabla 2 para identificar los ocho anti-patrones de rendimiento (AP-01 a AP-08), y un módulo específico que reconstruye el grafo de espera entre transacciones para detectar abrazos mortales, identificar el ciclo que los origina y clasificar el patrón estructural responsable según la Tabla 3 (CT-01 a CT-05, de acuerdo con los que aplican a cada motor). Cada hallazgo generado se presenta al usuario a través de una interfaz web que lo prioriza por impacto y lo acompaña de evidencia trazable, una explicación en lenguaje claro, una recomendación concreta y el riesgo asociado a aplicarla, dejando siempre la decisión final y su ejecución en manos del equipo, ya que la herramienta opera en modo de solo lectura.
 
Esta propuesta constituye una respuesta adecuada al problema dentro del alcance definido porque ataca directamente su causa: no sustituye al DBA con una caja negra que emite alertas sin justificación, sino que hace explícito y auditable el razonamiento detrás de cada hallazgo, en un formato accesible para quien no tiene ese conocimiento especializado. Al mismo tiempo, mantiene una ambición controlada y consistente con las restricciones de un proyecto de este tipo: se compromete con dos motores relacionales en versiones fijas (PostgreSQL 17 y MySQL 8.0) y con un catálogo cerrado de patologías cuyos umbrales se declaran antes de la validación, en lugar de intentar cubrir todo el ecosistema de bases de datos, renuncia deliberadamente al aprendizaje automático en favor de una detección determinista y verificable, y evita cualquier capacidad de intervención automática sobre los sistemas analizados, priorizando la profundidad y confiabilidad del diagnóstico sobre la amplitud de funcionalidades.

## 7. Metodología de desarrollo y plan de trabajo
 
### 7.1 Enfoque metodológico
 
El desarrollo de QUERYLENS se realizará mediante un **enfoque de prototipado iterativo**, adecuado para un proyecto que integra diferentes componentes técnicos y requiere validar progresivamente su funcionamiento. El proceso se organizará en ciclos de **diseño, construcción, prueba y ajuste**, permitiendo detectar problemas y realizar modificaciones antes de avanzar a las siguientes etapas.
 
La solución se desarrollará de manera modular, trabajando sobre la capa de recolección y normalización, el motor de detección, el módulo de contención, la interfaz de análisis y el banco de pruebas. Posteriormente, estos componentes serán integrados para validar el funcionamiento completo de QUERYLENS.
 
### 7.2 Iteraciones o fases de desarrollo
 
El desarrollo de QUERYLENS se realizará mediante iteraciones progresivas, en las que cada ciclo permitirá construir, probar y ajustar un conjunto de componentes antes de continuar con el siguiente. Las iteraciones previstas son:
 
1. **Iteración de requisitos y diseño:** tendrá como propósito establecer las necesidades de la solución y sus bases arquitectónicas. Se especificarán los requisitos a partir del catálogo declarado en las Tablas 2 y 3, se detallará el modelo canónico, se redactarán los manifiestos de resultados esperados de los 22 escenarios y se tomarán las decisiones tecnológicas iniciales. Los resultados de esta iteración servirán como referencia para orientar la implementación y evitar modificaciones importantes en etapas posteriores.
2. **Iteración de recolección y normalización:** tendrá como propósito obtener y unificar la telemetría proveniente de PostgreSQL y MySQL. Se implementará la recolección *agentless*, las funciones de traducción al modelo canónico y la anonimización de literales. Las pruebas permitirán identificar diferencias entre motores y ajustar el modelo para conservar la información necesaria para el diagnóstico.
3. **Iteración de detección y contención:** tendrá como propósito desarrollar las capacidades principales de análisis. Se implementarán las reglas deterministas para los anti-patrones y el análisis de bloqueos y abrazos mortales. Los resultados de corridas de calibración, distintas de las de validación, permitirán ajustar los umbrales iniciales, reducir falsos positivos y mejorar la trazabilidad de las detecciones. Al cierre de esta iteración los umbrales se congelan en una versión del archivo de reglas.
4. **Iteración de interfaz y explicación:** tendrá como propósito presentar los resultados de forma comprensible para el usuario objetivo. Se desarrollarán las vistas de análisis, la visualización de grafos y las explicaciones accionables. La revisión de los resultados permitirá ajustar la información presentada y mejorar la claridad de los diagnósticos.
5. **Iteración de integración y validación:** tendrá como propósito comprobar el funcionamiento conjunto de la solución. Se integrarán los componentes y se ejecutará la campaña de validación completa con los umbrales congelados, sobre los 22 escenarios y las corridas de control. Los resultados obtenidos permitirán realizar los últimos ajustes sobre las reglas, la normalización, el rendimiento y la interfaz antes del cierre del proyecto.
De esta manera, cada iteración no solo incorpora nuevas funcionalidades, sino que utiliza los resultados de las pruebas y revisiones para **refinar progresivamente la solución** hasta obtener una versión integrada y validada de QUERYLENS.
 
 
### 7.3 Estrategia de validación
 
La validación se realizará de forma progresiva mediante **pruebas funcionales, técnicas y de integración**. El banco de pruebas reproducible será la principal fuente de validación, utilizando patologías inyectadas de forma controlada y etiquetas conocidas para comparar los resultados obtenidos por QUERYLENS.
 
Se medirán principalmente la **precisión y exhaustividad** del motor de detección, además del sobrecosto generado por la recolección. También se verificará la correcta normalización de la telemetría entre motores, la reconstrucción de los grafos de espera, la anonimización de la información y la presentación de explicaciones comprensibles. La Tabla 4 declara las métricas y los umbrales que constituyen el criterio de cumplimiento del MVP.
 
**Tabla 4.** Métricas y umbrales comprometidos para la validación.
 
| Métrica | Definición | Umbral comprometido | Procedimiento |
|---|---|---|---|
| **M1.** Exhaustividad | VP / (VP + FN) por combinación patología-motor. | ≥ 0,90 en cada una de las 24 combinaciones. | 10 inyecciones por escenario y motor, comparadas con el manifiesto. |
| **M2.** Precisión | VP / (VP + FP) por combinación patología-motor. | ≥ 0,90 en cada combinación. | Se cuentan como FP los hallazgos no declarados en el manifiesto, tanto en corridas con inyección como en 10 corridas de control de 30 min por motor. |
| **M3.** Exactitud del análisis de contención | Proporción de ciclos o cadenas detectados con transacciones y recursos correctos, y proporción con el patrón (CT-01 a CT-05) correcto. | ≥ 0,95 en identificación de miembros; ≥ 0,90 en clasificación. | Comparación con las transacciones y recursos declarados por el script de inyección. |
| **M4.** Latencia de detección | Tiempo entre el inicio de la inyección, o el evento de abrazo mortal, y la emisión del hallazgo. | ≤ 2 ventanas (120 s) para AP-01 a AP-04 y AP-06 a AP-08; cierre de la ventana de observación para AP-05; ≤ 15 s para CT-01 a CT-04; ≤ 45 s desde que la transacción raíz cumple 30 s para CT-05. | Marcas de tiempo del script de inyección y del hallazgo. |
| **M5.** Sobrecosto de recolección | Reducción de TPS y aumento de latencia p95 de sysbench con el recolector activo frente a inactivo. | < 5 % en ambas medidas y en ambos motores. | 5 repeticiones de 10 min por condición, con las frecuencias de recolección declaradas en la sección 4. |
| **M6.** Anonimización | Apariciones de literales marcadores inyectados en el almacenamiento de QUERYLENS. | 0 apariciones. | Búsqueda de los marcadores en toda la base de QUERYLENS al terminar la campaña. |
| **M7.** Completitud y trazabilidad | Proporción de hallazgos con causa, evidencia enlazada a su instantánea, recomendación y riesgo. | 100 %. | Lista de chequeo automatizada sobre todos los hallazgos de la campaña. |
 
Los umbrales de detección de las Tablas 2 y 3 se ajustan únicamente con corridas de calibración y quedan congelados antes de la campaña final; cualquier cambio posterior obliga a repetir la campaña completa. Los umbrales de la Tabla 4 no se modifican durante el proyecto, de modo que el cumplimiento de cada objetivo específico pueda comprobarse de forma directa con los resultados que genera el banco de pruebas.
 
La retroalimentación del tutor se incorporará durante las diferentes iteraciones para revisar los requisitos, las decisiones de diseño y la utilidad de los resultados. Cuando sea posible, la evaluación con usuarios o entornos reales se utilizará como fuente adicional de retroalimentación, sin constituir una dependencia para la validación principal.
 
### 7.4 Plan de trabajo, cronograma o hitos
 
El plan de trabajo se organizará mediante hitos que permitan realizar un seguimiento del avance del proyecto y verificar la obtención de los principales resultados. Cada hito estará asociado a un entregable concreto.
 
| Hito | Resultado esperado | Entregable | Temporalidad |
|---|---|---|---|
| **H1. Definición y diseño** | Bases funcionales y arquitectónicas establecidas; catálogo, modelo canónico y manifiestos de los 22 escenarios aprobados. | Requisitos, catálogo de patologías, arquitectura y decisiones de diseño. | Semanas 1-2 |
| **H2. Recolección y normalización** | Telemetría de PostgreSQL 17 y MySQL 8.0 disponible en el modelo definido, con pruebas de contrato aprobadas, sobrecosto medido < 5 % (M5) y 0 literales persistidos (M6). | Capa de recolección y normalización. | Semanas 3-6 |
| **H3. Motor de análisis** | Reglas AP-01 a AP-08 y CT-01 a CT-05 implementadas por motor, calibradas y con umbrales congelados. | Motor de detección y módulo de contención. | Semanas 7-10 |
| **H4. Interfaz funcional** | Resultados disponibles para consulta y análisis, con el 100 % de los hallazgos completos y trazables (M7). | Interfaz web y capa de explicación. | Semanas 10-12 |
| **H5. Sistema integrado** | Componentes integrados en un entorno reproducible que ejecuta con un solo comando los 22 escenarios y las corridas de control. | Versión integrada y banco de pruebas. | Semanas 13-14 |
| **H6. Validación y cierre** | Métricas M1 a M7 de la Tabla 4 dentro de los umbrales comprometidos y consolidación del proyecto. | Resultados de validación, documentación y versión final de QUERYLENS. | Semanas 15-16 |
 
El trabajo será distribuido entre los integrantes por componentes, manteniendo actividades conjuntas de **integración, validación y documentación** para garantizar la coherencia de la solución.

## 8. Requerimientos

Presenta los requerimientos que guían el desarrollo de la solución.

### 8.1 Funcionales

Describe las funcionalidades y comportamientos que el sistema debe ofrecer.

### 8.2 No funcionales

Define atributos de calidad y restricciones del sistema, como rendimiento, seguridad, usabilidad, mantenibilidad o escalabilidad.

## 9. Evaluación de alternativas

Para diagnosticar una consulta lenta hace falta conocer su texto completo tal y como se ejecutó, porque los datos que se recogen están resumidos. Se compararon dos maneras de conseguir ese texto:

- **Opción A — Mirar la actividad del momento.** En el instante del análisis se pregunta a la base de datos qué consultas están en marcha. La consulta se encuentra solo si sigue ejecutándose justo en ese instante.
- **Opción B — Leer los registros (logs) del servidor.** El servidor deja por escrito cada consulta que ejecuta. Se leen los registros más recientes, se buscan las consultas que interesan y se toma su texto completo desde ahí, aunque ya hayan terminado.

## Resultado de las pruebas

### Prueba 1 — Comparación directa

Se probaron las dos opciones sobre el mismo banco, varias veces y con tráfico de relleno después de las consultas pesadas.

| Motor | Consultas analizadas | Opción A | Opción B |
|-------|----------------------|----------|----------|
| PostgreSQL | 30 | 0 % | 100 % |
| MySQL | 30 | 0 % | 100 % |

**Por qué la Opción A da 0 %.** La actividad solo muestra lo que se está ejecutando en el momento exacto de la revisión. Las consultas pesadas de la prueba terminan muy rápido (en milisegundos), así que cuando se mira, ya no queda nada en marcha. Solo la encuentra si la consulta sigue viva en ese instante.

**Por qué se usó entonces.** Fue la primera versión: lo más rápido de construir, sin configurar ni leer logs, y válido para las pruebas de diseño donde se puede dejar una consulta larga abierta a propósito. No era sólido para producción: las consultas cortas, que son la mayoría del tráfico real, nunca se encuentran.

### Prueba 2 — Límite de la Opción B (cuándo empieza a perder)

El 100 % de la Opción B depende de cuánto registro se lee (50 000 líneas por defecto, leídas desde el final). Si después de las consultas pesadas se escribe mucho tráfico nuevo, ese tráfico queda al final y las consultas pesadas pueden quedar **antes de lo leído** y perderse. La prueba midió el efecto de distintos volúmenes de tráfico posterior. Para cruzar los umbrales grandes sin gastar horas de batería, las celdas con tráfico alto se escribieron directamente en el log con el mismo formato que usan los motores (mismo efecto sobre la ventana):

| líneas leídas | tráfico posterior | MySQL | PostgreSQL |
|---|---|---|---|
| 50 000 | ninguno | 100 % | 100 % |
| 50 000 | 900 consultas (normal) | 100 % | 100 % |
| 50 000 | 10 000 consultas | 0 % | 100 % |
| 50 000 | 20 000 consultas | 0 % | 100 % |
| 50 000 | 48 000 consultas | 0 % | 100 % |
| 50 000 | 49 990 consultas | 0 % | 0 % |
| 50 000 | 51 000 consultas | 0 % | 0 % |
| 50 000 | 60 000 consultas | 0 % | 0 % |

Dos cosas importantes:

1. **El umbral no es una pendiente, es un corte.** No aparece una degradación gradual del 90 % o 80 %: hasta un punto se recupera todo y un poco más allá no se recupera nada. En producción, si las consultas pesadas siguen apareciendo en el tráfico, la ventana siempre cubre las más recientes; el corte solo importa si una consulta pesada ocurre una sola vez y queda enterrada.
2. **MySQL se entierra antes que PostgreSQL** (≈10 000 consultas frente a ≈50 000) porque cada consulta del slow log ocupa unas 5 líneas (cabecera + consulta), mientras que en PostgreSQL ocupa 1 línea. El consumo de ventana no se mide en consultas sino en líneas físicas: ambas se pierden alrededor de las 50 000 líneas.

### Pregunta 1. ¿Cuál alternativa ofrece mejor desempeño bajo carga esperada?

**Criterios de comparación:**

- **Latencia promedio y máxima:** tiempo de respuesta de operaciones críticas.
- **Throughput (capacidad de procesamiento):** número de solicitudes que el sistema puede manejar por unidad de tiempo.
- **Comportamiento bajo carga concurrente:** degradación del sistema cuando aumenta el número de usuarios simultáneos.

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Latencia promedio y máxima** | Casi ninguna: solo compara datos que ya estaban en memoria, sin trabajo extra. | Baja y acotada: lee una parte fija del log, no todo el historial; el trabajo extra no crece con el tiempo de vida del log. |
| **Throughput (capacidad de procesamiento)** | No añade casi coste, pero ese ahorro no sirve: bajo la carga esperada (muchas consultas cortas) encuentra 0 % de ellas. | Soporta el tráfico esperado siempre que la cantidad de log leída alcance a cubrir el volumen de consultas ejecutadas. |
| **Comportamiento bajo carga concurrente** | Solo ve lo que esté en marcha al mirar; con muchos usuarios atrapa algunas consultas largas, pero las cortas y ya terminadas se pierden por diseño. | No depende de cuántos usuarios haya ni de cuánto duren sus consultas: cada consulta ejecutada queda registrada por escrito. |

**Conclusión:** la Opción B funciona bajo la carga esperada; la Opción A, aunque casi no cuesta, no cumple su función con esa carga.

### Pregunta 2. ¿Qué grado de acoplamiento introduce cada opción?

**Criterios de comparación:**

- **Dependencia de servicios externos:** nivel en que el sistema depende de plataformas como APIs externas.
- **Interdependencia entre módulos internos:** qué tanto un cambio en un módulo afecta a otros.
- **Facilidad de sustitución de componentes:** capacidad de reemplazar una tecnología (ej: backend) sin rediseñar todo el sistema.

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Dependencia de servicios externos** | Ninguna nueva: usa información de la propia base de datos que ya se consultaba. | Mayor: requiere que el servidor tenga activos y accesibles sus registros (ruta y formato según el motor). |
| **Interdependencia entre módulos internos** | Fuerte con el propio mecanismo de recolección: depende de que la consulta aparezca a la vez en dos listas internas. | Contenida: vive en módulos dedicados a leer e interpretar los registros; el resto del proceso no depende del formato de cada motor. |
| **Facilidad de sustitución de componentes** | Muy alta: es una simple comparación, reemplazable en minutos. | Media: cambiarla requeriría reescribir el lector por motor, pero está separado del resto. |

**Conclusión:** la Opción A acopla menos, pero ese bajo acoplamiento es justamente lo que la vuelve inútil: queda atada a un instante de la actividad y no tiene memoria. La Opción B añade dependencia de los registros del servidor, pero queda contenida en módulos propios y no obligó a rediseñar el resto.

### Pregunta 3. ¿Qué nivel de disponibilidad y tolerancia a fallos ofrece cada alternativa?

**Criterios de comparación:**

- **Tiempo de disponibilidad (uptime esperado):** porcentaje de tiempo en que el sistema está operativo.
- **Mecanismos de recuperación ante fallos:** existencia de redundancia, backups o reintentos automáticos.
- **Impacto de fallos parciales:** qué ocurre si un componente falla (¿cae todo el sistema o solo una parte?).

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Tiempo de disponibilidad** | Bajo: solo "funciona" si la consulta pesada sigue en marcha justo cuando se mira, algo casi imposible con consultas cortas. | Alto: los registros guardan lo ejecutado recientemente, así que la cobertura es la historia reciente completa y no un solo instante. |
| **Mecanismos de recuperación ante fallos** | Ninguno: si la consulta no está en marcha, simplemente no aparece, sin aviso ni reintento. | Varios: límite de líneas leídas (no reprocesa todo el historial), filtro por tiempo, salto de registros incompletos y aviso si el archivo de registro no existe. |
| **Impacto de fallos parciales** | Falla en silencio: si la base no muestra actividad o no hay permisos, el informe queda incompleto sin que se note la causa. | Controlado: si el registro no está disponible, se avisa y el proceso continúa; una consulta no encontrada no afecta a las demás. |

**Conclusión:** la Opción B ofrece disponibilidad y tolerancia a fallos; la Opción A no tiene manera de recuperarse y sus fallos pasan desapercibidos.

### Opción seleccionada y justificación

Se elige la **Opción B (registros del servidor)**:

- Encuentra el texto completo de las consultas aunque ya hayan terminado, lo que cubre el caso típico de una consulta que se degradó y volvió a estar bien.
- Mantiene un límite claro de trabajo extra: solo lee una ventana fija del registro.
- Si algo falla con los registros, avisa y sigue, en vez de entregar un informe incompleto en silencio.
- Separó el lector de registros en módulos propios, sin tocar el resto del diseño.

La **Opción A queda descartada para producción**, pero se conserva documentada como la versión rápida de pruebas.

## 10. Diseño y arquitectura

Explica cómo se estructura la solución a nivel conceptual y técnico.

### 10.1 Descripción general de la arquitectura

**Objetivo:** que el lector entienda cómo está pensado el sistema antes de ver cualquier representación visual.

Debe incluir:

- Tipo de arquitectura (cliente-servidor, basada en Backend as a Service, etc.).
- Enfoque general de la solución.
- Relación con la alternativa seleccionada previamente.

### 10.2 Componentes del sistema

Deben identificarse y explicarse:

- **Componentes principales** del sistema (frontend, backend, base de datos, servicios externos).
- **Responsabilidad** de cada uno.
- **Relación con los requerimientos** del sistema.

Esta parte debe terminar con el **diagrama de arquitectura del sistema**.

### 10.3 Interacción entre módulos

Debe explicarse:

- Cómo se comunican los componentes.
- Flujos de datos.
- Dependencias.
- Nivel de acoplamiento.

Esta parte debe terminar con el **diagrama de interacción entre módulos**.

### 10.4 Comportamiento

Debe explicarse cómo se comportan los componentes, describiendo las principales secuencias de la arquitectura y respondiendo preguntas como:

- ¿El flujo es eficiente? (latencia, pasos innecesarios).
- ¿Existen cuellos de botella?
- ¿La interacción refleja buen desacoplamiento?

En esta parte se utilizan **diagramas de secuencia**.

## 11. Implementación y avance actual

Documenta el estado real de construcción del sistema y el grado de avance alcanzado.

### 11.1 Stack tecnológico

Lista y justifica las tecnologías, frameworks, librerías y herramientas utilizadas.

### 11.2 Componentes implementados

Describe qué módulos o componentes ya fueron construidos, qué funcionalidades cubren y cuál es su estado actual.

### 11.3 Integraciones realizadas

Explica las integraciones ya desarrolladas con servicios externos, bases de datos, autenticación u otros componentes.

### 11.4 Pendientes para la entrega final

Indica qué elementos faltan por implementar, integrar, corregir o validar antes del cierre del proyecto.

## 12. Despliegue y operación preliminar

Describe cómo se ejecuta actualmente la solución, en qué entorno funciona, qué dependencias requiere y cuál es su estado de despliegue o configuración.

## 13. Validación preliminar

Presenta las pruebas o validaciones realizadas hasta el momento para verificar el comportamiento del sistema y su grado de cumplimiento frente a los requerimientos.

### 13.1 Pruebas por componentes

### 13.2 Pruebas de integración

### 13.3 Pruebas de usabilidad

## 14. Resultados parciales y discusión

Presenta los principales hallazgos obtenidos hasta el momento, interpreta su significado y analiza el nivel de avance del proyecto frente a los objetivos planteados.

## 15. Plan de cierre hacia la entrega final

Describe las actividades restantes, prioridades, riesgos y estrategia de cierre para completar el proyecto en las semanas finales.

## 16. Referencias

[1] T. Haerder y A. Reuter. (1983). Principles of transaction-oriented database recovery. *ACM Computing Surveys*, 15(4), 287–317. https://doi.org/10.1145/289.291
 
[2] PostgreSQL Global Development Group. (2024). *Chapter 13. Concurrency Control*. PostgreSQL 17 Documentation. https://www.postgresql.org/docs/17/mvcc.html
 
[3] Oracle. (2026). *Chapter 17. The InnoDB Storage Engine*. MySQL 8.0 Reference Manual. https://dev.mysql.com/doc/refman/8.0/en/innodb-storage-engine.html
 
[4] P. G. Selinger, M. M. Astrahan, D. D. Chamberlin, R. A. Lorie y T. G. Price. (1979). Access path selection in a relational database management system. En *Proceedings of the 1979 ACM SIGMOD International Conference on Management of Data* (pp. 23–34). https://doi.org/10.1145/582095.582099
 
[5] G. Moerkotte, T. Neumann y G. Steidl. (2009). Preventing bad plans by bounding the impact of cardinality estimation errors. *Proceedings of the VLDB Endowment*, 2(1), 982–993. https://doi.org/10.14778/1687627.1687738
 
[6] M. Winand. (2012). *SQL Performance Explained*. Markus Winand. https://use-the-index-luke.com
 
[7] M. Fowler. (2002). *Patterns of Enterprise Application Architecture*. Addison-Wesley.
 
[8] Oracle. (2026). *InnoDB Locking and Transaction Model*. MySQL 8.0 Reference Manual. https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-transaction-model.html
 
[9] E. G. Coffman, M. Elphick y A. Shoshani. (1971). System deadlocks. *ACM Computing Surveys*, 3(2), 67–78. https://doi.org/10.1145/356586.356588
 
[10] Oracle. (2026). *Deadlocks in InnoDB*. MySQL 8.0 Reference Manual. https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks.html
 
[11] C. Majors, L. Fong-Jones y G. Miranda. (2022). *Observability Engineering*. O'Reilly Media.
 
[12] PostgreSQL Global Development Group. (2024). *pg_stat_statements — track statistics of SQL planning and execution*. PostgreSQL 17 Documentation. https://www.postgresql.org/docs/17/pgstatstatements.html
 
[13] Oracle. (2026). *MySQL Performance Schema*. MySQL 8.0 Reference Manual. https://dev.mysql.com/doc/refman/8.0/en/performance-schema.html
 
[14] G. Hohpe y B. Woolf. (2003). *Enterprise Integration Patterns: Designing, Building, and Deploying Messaging Solutions*. Addison-Wesley.
 
[15] Congreso de la República de Colombia. (2012). *Ley Estatutaria 1581 de 2012, por la cual se dictan disposiciones generales para la protección de datos personales*. Diario Oficial 48.587.
 
[16] M.-C. Hsueh, T. K. Tsai y R. K. Iyer. (1997). Fault injection techniques and tools. *Computer*, 30(4), 75–82. https://doi.org/10.1109/2.585157
 
[17] C. D. Manning, P. Raghavan y H. Schütze. (2008). *Introduction to Information Retrieval*. Cambridge University Press.
 
[18] A. Kopytov. (2026). *sysbench: Scriptable database and system performance benchmark*. GitHub. https://github.com/akopytov/sysbench
 
[19] pganalyze. (2026). *Query Performance*. pganalyze Documentation. https://pganalyze.com/docs/query-performance
 
[20] pganalyze. (2026). *pganalyze Documentation*. https://pganalyze.com/docs
 
[21] SolarWinds. (2026). *Database Performance Analyzer*. https://www.solarwinds.com/database-performance-analyzer
 
[22] SolarWinds. (2026). *Introduction to Database Performance Analyzer*. SolarWinds Documentation. https://documentation.solarwinds.com/en/success_center/dpa/content/dpa-introduction.htm
 
[23] Quest Software. (2026). *Foglight for Databases*. https://www.quest.com/products/foglight-for-cross-platform-databases/
 
[24] Redgate Software. (2026). *Comparison of functionality by database engine*. Redgate Monitor Documentation. https://documentation.red-gate.com/monitor/comparison-of-functionality-by-database-engine-342852844.html
 
[25] Percona. (2026). *Percona Monitoring and Management*. Percona Documentation. https://docs.percona.com/percona-monitoring-and-management/2/index.html
