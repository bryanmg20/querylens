# Primer Informe - querylens

## Resumen / Abstract

El diagnóstico de problemas de rendimiento y contención en bases de datos relacionales depende hoy de personal DBA especializado, un perfil escaso, y de herramientas comerciales costosas que operan como caja negra. Se propone QUERYLENS, una herramienta agnóstica de motor que recolecta telemetría de PostgreSQL y MySQL de forma no intrusiva, la normaliza a un modelo canónico común y detecta de manera determinista un catálogo de ocho anti-patrones de rendimiento y patologías de contención (abrazos mortales), presentando cada hallazgo con evidencia, causa y recomendación accionable a través de una interfaz web. El alcance corresponde a un MVP funcional y documentado, de solo lectura y sin aprendizaje automático. El desarrollo sigue una metodología de ingeniería de diseño: definición de modelo canónico, evaluación de alternativas mediante matriz ponderada, implementación por componentes y validación en un banco de pruebas con inyección controlada de fallas.

## 1. Introducción

Las bases de datos relacionales constituyen el componente central de la mayoría de las aplicaciones empresariales, desde sistemas transaccionales hasta plataformas de comercio electrónico y servicios financieros, cuyo funcionamiento depende de que las consultas se resuelvan en tiempos predecibles y de que las transacciones concurrentes no interfieran entre sí. En el sector de las tecnologías de la información, la adopción masiva de arquitecturas de microservicios, frameworks de mapeo objeto-relacional (ORM) y el crecimiento sostenido del volumen de datos han convertido el rendimiento de la capa de persistencia en un factor determinante tanto de la experiencia del usuario final como de los costos de infraestructura. En paralelo, la observabilidad de software se ha consolidado como práctica estándar de ingeniería, aunque su aplicación específica a bases de datos (donde la telemetría es heterogénea entre motores y su interpretación exige conocimiento especializado) sigue siendo un área con herramientas fragmentadas y de difícil acceso para equipos sin un administrador de bases de datos dedicado.

En la práctica actual, el diagnóstico de degradaciones de rendimiento depende de que alguien con experiencia interprete planes de ejecución y vistas internas cuyo nombre, formato y semántica cambian de un motor a otro, un perfil con el que no cuenta la mayoría de los equipos de desarrollo. Esto provoca que problemas recurrentes (consultas sin índice adecuado, patrones ineficientes generados por el ORM o transacciones que se bloquean mutuamente) se resuelvan de forma reactiva y superficial, por ejemplo reintentando una transacción fallida en lugar de corregir su causa estructural en el código. Las soluciones comerciales que automatizan este diagnóstico existen, pero su costo de licenciamiento y su naturaleza de caja negra, que no explica el razonamiento detrás de cada hallazgo, las hacen poco accesibles o poco confiables para buena parte de las organizaciones, en particular para equipos pequeños o con baja madurez operativa.

Esta situación revela una necesidad técnica concreta: una herramienta que traduzca la telemetría heterogénea de distintos motores a un modelo de análisis común, que aplique reglas de detección deterministas y auditables en lugar de modelos de caja negra, y que comunique sus hallazgos en un lenguaje accesible para un desarrollador sin formación especializada en administración de bases de datos. Esta necesidad constituye a su vez una oportunidad de diseño de ingeniería completa, que exige resolver la recolección no intrusiva de datos en un sistema en producción, la normalización semántica entre motores con modelos internos distintos, la anonimización de información sensible en el origen y el diseño de una capa de explicación que traduzca evidencia técnica en recomendaciones accionables.

Frente a esta oportunidad se propone QUERYLENS, una herramienta agnóstica de motor que observa bases de datos PostgreSQL y MySQL en operación sin instalar agentes dentro del motor, detecta de forma automática y determinista un catálogo de anti-patrones de rendimiento y patologías de contención (incluyendo el análisis estructural de abrazos mortales mediante la reconstrucción de su grafo de espera) y presenta cada hallazgo con evidencia, explicación y recomendación de acción. Se espera que esta herramienta reduzca el tiempo de diagnóstico de incidentes de rendimiento en equipos sin un especialista dedicado en bases de datos, sirviendo además como base extensible para incorporar motores adicionales en el futuro. Las secciones siguientes detallan el alcance específico del proyecto, la metodología de desarrollo y el plan de trabajo propuesto para su ejecución.

## 2. Planteamiento del problema

El problema central que aborda este proyecto es la carencia, en equipos de desarrollo y operación de aplicaciones empresariales, de capacidad efectiva para diagnosticar de forma oportuna las causas reales de la degradación de rendimiento y de los episodios de contención en sus bases de datos relacionales. Esta carencia se manifiesta en la práctica como un diagnóstico artesanal, dependiente de personal DBA especializado (un perfil escaso en la mayoría de las organizaciones) y en la resolución superficial de incidentes recurrentes, que se atienden sin corregir su causa estructural. No se trata de la ausencia de una herramienta específica, sino de una situación deficiente y verificable: la información necesaria para explicar estos incidentes existe dentro de los motores de base de datos, pero resulta inaccesible en la práctica para quien no cuenta con formación especializada, debido a su heterogeneidad y complejidad técnica. Esta problemática es relevante porque afecta tanto la eficiencia operativa de los equipos técnicos como la experiencia de los usuarios finales de las aplicaciones, y es transferible a cualquier organización que dependa de bases de datos relacionales sin contar con un especialista dedicado, lo que la sitúa por encima de un caso particular de empresa.

### 2.1 Descripción del problema

Los equipos de desarrollo y operación de aplicaciones empresariales que dependen de bases de datos relacionales enfrentan de forma recurrente incidentes de degradación de rendimiento y de contención (consultas que se vuelven lentas, transacciones que se bloquean mutuamente, escaneos de tabla innecesarios) cuya causa raíz permanece sin diagnosticar o se diagnostica de forma tardía e incompleta. Esto ocurre porque la información necesaria para explicar estos incidentes existe dentro del motor de base de datos, pero se encuentra dispersa en vistas y estadísticas internas cuyo nombre, formato y semántica cambian de un motor a otro, y cuya interpretación requiere conocimiento especializado en administración de bases de datos (DBA). La mayoría de los equipos de desarrollo no cuenta con ese perfil de forma dedicada, por lo que el diagnóstico recae en desarrolladores sin la formación necesaria para leer planes de ejecución o reconstruir manualmente las causas de un bloqueo entre transacciones.

Como consecuencia directa, estos equipos resuelven los incidentes de forma reactiva y superficial: reintentan una transacción que falló por un abrazo mortal en lugar de corregir el orden de acceso a los datos que lo originó, o normalizan como "lentitud esporádica" una consulta que en realidad dejó de usar un índice disponible. Esta forma de resolución no elimina la causa estructural del problema, por lo que los mismos incidentes tienden a repetirse con el tiempo, generando una acumulación de deuda técnica no diagnosticada. Las consecuencias afectan tanto a los equipos técnicos (que dedican tiempo desproporcionado a depurar manualmente incidentes recurrentes sin herramientas adecuadas) como a los usuarios finales de las aplicaciones, que experimentan tiempos de respuesta degradados y, en los casos más severos, fallas transaccionales visibles. Esta problemática no es exclusiva de una organización particular: se manifiesta en cualquier contexto donde exista una aplicación empresarial sobre una base de datos relacional y un equipo de desarrollo sin un especialista en bases de datos dedicado, lo que la convierte en una situación transferible a un amplio espectro de organizaciones de distintos tamaños y sectores.

### 2.2 Justificación

Atender este problema es pertinente desde varias dimensiones. Desde el punto de vista práctico, el rendimiento de la base de datos incide directamente en la experiencia del usuario final y en los costos de infraestructura de una organización; un incidente de contención no diagnosticado puede traducirse en indisponibilidad parcial de un servicio, mientras que una consulta degradada sostenida en el tiempo incrementa el consumo de recursos de cómputo sin que exista visibilidad clara de la causa. Desde el punto de vista técnico, el problema expone una carencia real en la disponibilidad de herramientas accesibles: las soluciones comerciales que sí resuelven este diagnóstico de forma automatizada tienen costos de licenciamiento elevados y operan como cajas negras, sin explicar el razonamiento detrás de cada hallazgo, lo que las hace poco confiables para equipos que necesitan entender y no solo recibir una alerta. Desde el punto de vista social, reducir la dependencia de un perfil especializado escaso (el DBA) democratiza el acceso a un diagnóstico de calidad para equipos pequeños o con baja madurez operativa, que hoy quedan en desventaja frente a organizaciones con mayores recursos.

### 2.3 Restricciones y supuestos iniciales

El desarrollo de QUERYLENS estará condicionado por restricciones de tiempo, recursos, infraestructura y disponibilidad de entornos de validación, las cuales determinan las decisiones de alcance y las características de la solución. Asimismo, se establecen supuestos iniciales sobre las condiciones técnicas necesarias para implementar y validar el sistema.

#### Restricciones

* **Tiempo y alcance:** el proyecto deberá desarrollarse dentro del cronograma académico establecido, por lo que el soporte se limitará inicialmente a PostgreSQL y MySQL. La incorporación de SQL Server será opcional y estará condicionada al cumplimiento de los objetivos principales del proyecto.

* **Recursos disponibles:** el desarrollo se realizará con los recursos técnicos y humanos disponibles para el proyecto académico. Por esta razón, se priorizará la implementación de las funcionalidades esenciales del MVP sobre características de operación a escala productiva, como alta disponibilidad, multi-tenencia o despliegue en la nube.

* **Acceso a entornos reales:** no se asumirá la disponibilidad permanente de bases de datos empresariales o de equipos de desarrollo y operación para realizar las pruebas. La validación principal deberá poder realizarse de manera independiente mediante un banco de pruebas reproducible con patologías inyectadas de forma controlada.

* **Infraestructura de pruebas:** el banco de pruebas deberá poder desplegarse en contenedores y contar con cargas de trabajo y scripts que permitan reproducir de manera controlada las patologías contempladas en el catálogo de detección.

* **Acceso a la telemetría:** la recolección dependerá de las interfaces públicas de observabilidad disponibles en cada motor soportado y de los permisos necesarios para consultar dicha información. QUERYLENS no podrá depender de modificaciones internas del motor ni de la instalación de agentes dentro de la base de datos.

* **Sobrecosto de recolección:** la capa de recolección deberá mantener un sobrecosto inferior al 5 % sobre la métrica de rendimiento de la carga observada. Este requisito limita la frecuencia y cantidad de información que puede recolectarse y obliga a evaluar el impacto de las estrategias de observabilidad implementadas.

* **Heterogeneidad entre motores:** las diferencias de nomenclatura, estructura y semántica de las interfaces de observabilidad de PostgreSQL y MySQL pueden limitar la equivalencia de la información recolectada. Por ello, la normalización deberá preservar la información diagnóstica relevante sin asumir que todos los motores proporcionan los mismos datos.

* **Privacidad de la información:** los literales potencialmente sensibles deberán anonimizarse durante la recolección y antes de cualquier persistencia, limitando la exposición de información propia de las bases de datos analizadas.

* **Intervención sobre las bases de datos:** QUERYLENS operará en modo de solo lectura. La herramienta no ejecutará acciones correctivas, modificará parámetros, alterará esquemas ni intervendrá automáticamente sobre las transacciones analizadas.

#### Supuestos iniciales

* **Disponibilidad de interfaces de observabilidad:** se asume que PostgreSQL y MySQL proporcionarán interfaces públicas de observabilidad suficientemente accesibles para obtener las estadísticas, planes de ejecución, sesiones, bloqueos y demás telemetría requerida por el modelo canónico.

* **Permisos de consulta:** se asume que el entorno donde se utilice QUERYLENS podrá proporcionar las credenciales y permisos necesarios para consultar la información de observabilidad sin requerir privilegios que impliquen modificar la configuración o los datos de la base de datos.

* **Reproducibilidad del banco de pruebas:** se asume que las patologías definidas en el catálogo pueden ser reproducidas mediante cargas controladas y scripts de inyección dentro de un entorno contenerizado, permitiendo establecer etiquetas conocidas para la evaluación de la herramienta.

* **Validación determinista:** se asume que las patologías incluidas en el catálogo pueden ser identificadas mediante reglas y umbrales observables y auditables, sin depender de modelos de aprendizaje automático entrenados.

* **Entorno real como enriquecimiento:** se asume que la participación de equipos de desarrollo u operación reales puede aportar retroalimentación adicional sobre la utilidad de QUERYLENS, pero su disponibilidad no constituye una condición necesaria para completar la validación principal del proyecto.

* **Versiones controladas de los motores:** se asume que las versiones de PostgreSQL y MySQL utilizadas durante el desarrollo y las pruebas podrán mantenerse controladas dentro del banco de pruebas, permitiendo reproducir los resultados y realizar pruebas de regresión ante cambios en las interfaces de observabilidad.


## 3. Alcance del proyecto

El alcance de QUERYLENS comprende el diseño, construcción y validación de un prototipo funcional documentado para el análisis de rendimiento de consultas y problemas de contención en bases de datos relacionales. La solución se desarrollará bajo un enfoque agnóstico del motor, mediante la recolección no intrusiva de telemetría, su normalización a un modelo común y la generación de hallazgos explicables y accionables. El alcance se establece de la siguiente manera:

### Incluye

**Funcionalidades principales:**

- **Soporte multi-motor:** soporte para PostgreSQL y MySQL. SQL Server se contempla como una extensión opcional, condicionada al cumplimiento del cronograma.

- **Recolección sin agente:** obtención de telemetría mediante interfaces públicas de observabilidad de los motores, incluyendo estadísticas agregadas por sentencia, muestreo de sesiones activas, planes de ejecución, eventos de bloqueo y estadísticas de objetos.

- **Normalización y anonimización:** transformación de la información recolectada hacia un modelo canónico común y anonimización de literales en el punto de recolección, antes de cualquier persistencia.

- **Detección de anti-patrones de rendimiento:** identificación determinista de un catálogo mínimo de ocho anti-patrones: degradación del tiempo respecto a una línea base, escaneo completo evitable, predicado no sargable, índice ausente, índice no utilizado o redundante, patrón N+1, vertido a disco por memoria de trabajo insuficiente y error grave de estimación de cardinalidad.

- **Análisis de contención y abrazos mortales:** reconstrucción del grafo de espera, detección de ciclos, normalización de firmas y clasificación de los patrones estructurales de los abrazos mortales, incluyendo orden inconsistente de acceso, escalada de bloqueo, contención de rango, ampliación por ausencia de índice y cascada por transacción larga.

- **Priorización de hallazgos:** organización de los problemas detectados de acuerdo con su impacto, permitiendo identificar las patologías que requieren mayor atención.

- **Interfaz web de análisis:** presentación de los hallazgos mediante un inventario priorizado, detalle de la evidencia y planes de ejecución, línea de tiempo de la degradación y vista de grafo para los interbloqueos.

- **Explicaciones accionables:** generación de explicaciones para cada hallazgo que indiquen la causa identificada, la evidencia que la sustenta, la acción recomendada y el riesgo asociado a su aplicación.

- **Banco de pruebas reproducible:** ejecución de pruebas en contenedores mediante cargas generadas con una herramienta de *benchmark* y scripts para la inyección controlada de las patologías contempladas en el catálogo.

- **Documentación de ingeniería:** elaboración de la especificación de requerimientos, matriz de evaluación de alternativas, vistas de arquitectura, registro de decisiones de diseño, plan de pruebas y manual de despliegue.

**Tipo de usuarios:**

- **Desarrolladores y equipos de desarrollo u operación:** la solución estará orientada principalmente a usuarios que necesiten diagnosticar problemas de rendimiento y contención sin contar necesariamente con formación especializada en administración de bases de datos.

**Nivel de madurez:**

- **MVP / prototipo funcional documentado:** el proyecto tendrá como resultado una versión funcional y validable de QUERYLENS, acompañada de la documentación técnica necesaria para demostrar su diseño, implementación, pruebas y despliegue.

**Entornos cubiertos:**

- **Arquitectura de la solución:** la solución contemplará una arquitectura compuesta por una capa de recolección agentless y backend de análisis, una interfaz web para la consulta y visualización de resultados, y un banco de pruebas reproducible desplegado en contenedores, con cargas de trabajo y scripts para la inyección controlada de las patologías. La arquitectura estará preparada para trabajar con los motores relacionales soportados mediante funciones de traducción hacia un modelo canónico común.

### No incluye

- **Ejecución automática de acciones correctivas:** QUERYLENS funcionará bajo un principio de solo lectura. La herramienta identificará, explicará y recomendará acciones, pero no ejecutará modificaciones sobre consultas, esquemas, parámetros o transacciones de las bases de datos.

- **Motores y tecnologías fuera del alcance definido:** no se contempla soporte para bases de datos no relacionales ni para almacenes analíticos. El soporte de SQL Server será únicamente una extensión opcional y no una condición necesaria para el cumplimiento del alcance base.

- **Modelos de aprendizaje automático entrenados:** la detección de patologías será determinista, basada en reglas, umbrales calibrados y evidencia trazable. El uso opcional de un modelo de lenguaje, si se incorpora, estará restringido a la redacción de explicaciones fundamentadas en evidencia estructurada y no podrá introducir causas no sustentadas.

- **Sintonización y corrección automática:** no se desarrollarán mecanismos para ajustar automáticamente parámetros del motor, rediseñar esquemas o aplicar cambios correctivos sobre las bases de datos.

- **Escala y operación productiva:** no se contempla implementar alta disponibilidad, capacidades de multi-tenencia ni despliegue en la nube de la propia herramienta. Estas características quedan fuera del alcance del MVP y corresponden a posibles evoluciones posteriores del producto.

- **Soporte post-proyecto:** el alcance se limita a la construcción, documentación, pruebas y validación de la solución durante el proyecto. No comprende la operación permanente, mantenimiento evolutivo ni soporte posterior a la finalización del proyecto.

## 4. Objetivos

### 4.1 Objetivo General

Diseñar, construir y validar, al cierre del proyecto, una herramienta funcional y agnóstica del motor de base de datos que detecte, priorice y explique patologías de rendimiento y de contención en bases de datos relacionales, a partir de telemetría recolectada de forma no intrusiva, proporcionando evidencia trazable y recomendaciones accionables para facilitar su diagnóstico por parte de equipos de desarrollo.

### 4.2 Objetivos Específicos

1. Definir un modelo canónico para representar sentencias, planes de ejecución, eventos de espera y bloqueos, junto con las funciones de traducción necesarias para abstraer las particularidades de cada motor soportado.

2. Evaluar las alternativas de recolección, almacenamiento y visualización mediante una matriz de criterios ponderados, documentando la alternativa seleccionada, sus compromisos y las razones que sustentan la decisión arquitectónica.

3. Implementar la capa de recolección multi-motor *agentless* sobre las interfaces públicas de observabilidad de los motores soportados, verificando mediante mediciones que el sobrecosto introducido se mantenga por debajo del 5 % sobre la métrica de rendimiento de la carga observada.

4. Implementar el motor de detección determinista para el catálogo definido de anti-patrones de rendimiento, utilizando umbrales calibrados y garantizando la trazabilidad de cada hallazgo hacia la evidencia que lo sustenta.

5. Implementar el módulo de análisis de contención mediante la reconstrucción del grafo de espera, la detección de ciclos, la normalización de firmas y la clasificación de los patrones estructurales asociados a abrazos mortales.

6. Construir la interfaz web de análisis y la capa de explicación accionable, orientadas a desarrolladores sin formación especializada en administración de bases de datos y proporcionando información sobre la causa, evidencia, recomendación y riesgo asociado a cada hallazgo.

7. Validar la solución mediante un banco de pruebas reproducible en contenedores, utilizando patologías inyectadas de forma controlada y midiendo su precisión y exhaustividad frente a etiquetas conocidas por construcción.

8. Recopilar retroalimentación complementaria de al menos dos equipos de desarrollo u operación, cuando sea posible, para contrastar los requerimientos, la utilidad de los hallazgos y la claridad de las explicaciones generadas por QUERYLENS, sin que la disponibilidad de estos entornos constituya una dependencia para la validación principal del proyecto.

## 5. Solución propuesta

Para responder al problema descrito, se propone QUERYLENS, una herramienta de análisis que observa bases de datos relacionales en operación y traduce su telemetría interna en hallazgos explicables sobre problemas de rendimiento y de contención, sin requerir que quien la use tenga formación especializada en administración de bases de datos. La solución está dirigida principalmente a desarrolladores y equipos de desarrollo u operación que necesitan diagnosticar por qué una aplicación se volvió lenta o por qué ocurren bloqueos entre transacciones, pero que no cuentan con un DBA dedicado que interprete manualmente planes de ejecución y vistas internas del motor.

A alto nivel, QUERYLENS funciona en tres etapas encadenadas. En la primera, una capa de recolección sin agente se conecta a la base de datos como lo haría cualquier cliente SQL autorizado, y consulta periódicamente las interfaces públicas de observabilidad que cada motor ya expone (estadísticas por sentencia, sesiones activas, planes de ejecución, eventos de bloqueo y estadísticas de tablas e índices), sin instalar ni modificar nada dentro del propio motor. En la segunda etapa, esa información heterogénea se normaliza a un modelo canónico común, de modo que una consulta, un plan de ejecución o un evento de bloqueo se representen de la misma forma sin importar si provienen de PostgreSQL o de MySQL; en este mismo punto se anonimizan los literales sensibles antes de guardar cualquier dato. Sobre esa representación uniforme actúa la tercera etapa, un motor de detección determinista que aplica reglas y umbrales calibrados para identificar el catálogo de ocho anti-patrones de rendimiento definidos, y un módulo específico que reconstruye el grafo de espera entre transacciones para detectar abrazos mortales, identificar el ciclo que los origina y clasificar el patrón estructural responsable. Cada hallazgo generado se presenta al usuario a través de una interfaz web que lo prioriza por impacto y lo acompaña de evidencia trazable, una explicación en lenguaje claro, una recomendación concreta y el riesgo asociado a aplicarla, dejando siempre la decisión final y su ejecución en manos del equipo, ya que la herramienta opera en modo de solo lectura.

Esta propuesta constituye una respuesta adecuada al problema dentro del alcance definido porque ataca directamente su causa: no sustituye al DBA con una caja negra que emite alertas sin justificación, sino que hace explícito y auditable el razonamiento detrás de cada hallazgo, en un formato accesible para quien no tiene ese conocimiento especializado. Al mismo tiempo, mantiene una ambición controlada y consistente con las restricciones de un proyecto de este tipo: se compromete con dos motores relacionales en lugar de intentar cubrir todo el ecosistema de bases de datos, renuncia deliberadamente al aprendizaje automático en favor de una detección determinista y verificable, y evita cualquier capacidad de intervención automática sobre los sistemas analizados, priorizando la profundidad y confiabilidad del diagnóstico sobre la amplitud de funcionalidades.

## 6. Estado del arte / soluciones relacionadas

El diagnóstico del rendimiento y de los problemas de contención en bases de datos relacionales cuenta actualmente con diversas soluciones comerciales y de código abierto orientadas a la observabilidad, el análisis de consultas y la identificación de problemas de rendimiento. Para este estado del arte se seleccionaron cinco soluciones representativas: **pganalyze, SolarWinds Database Performance Analyzer, Quest Foglight, Redgate Monitor y Percona Monitoring and Management (PMM)**. La selección permite analizar diferentes enfoques, desde herramientas especializadas en un motor hasta plataformas de observabilidad multi-motor.

### 6.1 Soluciones comerciales

#### 6.1.1 pganalyze

pganalyze es una solución comercial especializada en la observabilidad y optimización del rendimiento de PostgreSQL. Entre sus funcionalidades se encuentran el análisis de estadísticas de consultas, seguimiento de latencia, análisis de planes de ejecución, recomendaciones de optimización, análisis de índices, información sobre conexiones y alertas. Su módulo Query Advisor analiza automáticamente los planes de ejecución para identificar oportunidades de optimización y proporcionar sugerencias accionables. [1]

Una característica relevante es que pganalyze utiliza un componente denominado *pganalyze Collector* para recopilar información del entorno. Este componente debe instalarse en el servidor de base de datos o en un contenedor o máquina virtual que pueda conectarse a este. [2]

pganalyze constituye un antecedente importante para QUERYLENS por su profundidad en el análisis de consultas y planes de ejecución. Sin embargo, su especialización se encuentra en PostgreSQL y su arquitectura de recolección utiliza un componente dedicado. QUERYLENS plantea, en cambio, una arquitectura multi-motor con una capa de recolección *agentless* y un modelo común para representar la información obtenida de los diferentes motores.

#### 6.1.2 SolarWinds Database Performance Analyzer

SolarWinds Database Performance Analyzer (DPA) es una plataforma comercial orientada al monitoreo, diagnóstico y optimización del rendimiento de bases de datos. La herramienta utiliza análisis basado en tiempos de espera para identificar cuellos de botella y permite investigar consultas con tiempos de espera elevados, consultas ineficientes y anomalías de rendimiento. Además, soporta diferentes sistemas gestores y proporciona una vista centralizada de su rendimiento. [3]

Una característica especialmente relevante para QUERYLENS es su arquitectura *agentless*. La documentación de DPA indica que la herramienta utiliza este enfoque y que el consumo de recursos en sistemas de producción es inferior al 1 %. [4]

DPA representa uno de los antecedentes técnicos más relevantes para QUERYLENS, debido a la combinación de recolección sin agente, análisis de rendimiento y soporte multi-motor. La diferencia principal se encuentra en el enfoque de análisis: QUERYLENS plantea un catálogo explícito de anti-patrones, reglas deterministas, trazabilidad de la evidencia y un modelo canónico que permita abstraer las diferencias entre PostgreSQL y MySQL.

#### 6.1.3 Quest Foglight

Quest Foglight es una plataforma comercial de observabilidad y diagnóstico para diferentes tecnologías de bases de datos. Su arquitectura permite trabajar con distintos motores mediante componentes específicos, incluyendo PostgreSQL y MySQL. [5]

La solución proporciona capacidades orientadas al análisis del rendimiento y de las consultas, además de herramientas para investigar problemas relacionados con eventos y actividad de las bases de datos. Su soporte para múltiples tecnologías la convierte en un antecedente relevante para el enfoque multi-motor de QUERYLENS.

La principal diferencia se encuentra en el objetivo de la solución. Foglight aborda la observabilidad de bases de datos desde una perspectiva empresarial y general, mientras que QUERYLENS plantea una arquitectura centrada en la detección de un conjunto definido de patologías de rendimiento y contención, con reglas deterministas y explicaciones orientadas al usuario final del diagnóstico.

#### 6.1.4 Redgate Monitor

Redgate Monitor es una plataforma comercial de monitoreo que permite supervisar diferentes motores de bases de datos desde una interfaz común. Actualmente soporta, entre otras tecnologías, SQL Server, PostgreSQL, Oracle, MySQL y MongoDB. Proporciona funcionalidades relacionadas con consultas, planes de ejecución, estadísticas de espera, rendimiento, alertas y análisis histórico. [6]

Una característica importante para el análisis de QUERYLENS es que las capacidades disponibles no son idénticas para todos los motores. Por ejemplo, la documentación de Redgate muestra que PostgreSQL dispone de determinadas capacidades de análisis de consultas y esperas, mientras que algunas funcionalidades de bloqueo y deadlocks se encuentran disponibles únicamente para determinados motores. [6]

Esta diferencia evidencia uno de los retos principales del enfoque multi-motor: aunque una plataforma pueda soportar diferentes sistemas gestores, la información disponible y las capacidades de diagnóstico pueden variar entre ellos. QUERYLENS aborda este problema mediante la definición de un modelo canónico y funciones de traducción específicas para cada motor.

### 6.2 Solución de código abierto

#### 6.2.1 Percona Monitoring and Management

Percona Monitoring and Management (PMM) es una plataforma de código abierto orientada a la observabilidad, monitoreo y administración de bases de datos. Actualmente proporciona visibilidad sobre MySQL, PostgreSQL y MongoDB, desde métricas generales de los clústeres hasta información sobre consultas individuales. También permite desplegarse en entornos locales, cloud e híbridos. [7]

PMM constituye un antecedente relevante porque demuestra que es posible construir una plataforma de observabilidad multi-motor utilizando componentes de código abierto. Su arquitectura utiliza dos componentes principales, denominados *Server* y *Client*, para recopilar y centralizar la información de las bases de datos monitorizadas. [7]

Frente a este enfoque, QUERYLENS plantea una arquitectura de recolección *agentless*, en la que no se requiere instalar agentes dentro del servidor de base de datos. Además, mientras PMM busca proporcionar una plataforma general de observabilidad, QUERYLENS concentra el análisis en un catálogo específico de patologías y en la generación de explicaciones trazables y accionables.

### 6.3 Comparación y oportunidad de QUERYLENS

Las soluciones analizadas evidencian que el monitoreo y diagnóstico del rendimiento de bases de datos es un campo con herramientas consolidadas. pganalyze ofrece capacidades especializadas para PostgreSQL, mientras que SolarWinds Database Performance Analyzer, Quest Foglight, Redgate Monitor y Percona Monitoring and Management proporcionan diferentes niveles de observabilidad sobre múltiples motores. Estas soluciones permiten analizar consultas, planes de ejecución, métricas de rendimiento, eventos de espera, bloqueos y otros indicadores relevantes para identificar problemas. A partir de este panorama, QUERYLENS se plantea sobre una problemática concreta: transformar la telemetría heterogénea de PostgreSQL y MySQL en diagnósticos comprensibles, trazables y accionables, manteniendo una arquitectura independiente del motor.

En cuanto a costos y usabilidad, las soluciones comerciales requieren modelos de licenciamiento o suscripción y están orientadas principalmente a equipos especializados en administración y monitoreo de bases de datos. Aunque proporcionan interfaces completas y numerosas capacidades de diagnóstico, su amplitud puede resultar innecesaria para el usuario objetivo de QUERYLENS. La propuesta se orientará específicamente a desarrolladores sin formación especializada en DBA, priorizando una interfaz de análisis que presente los hallazgos de forma comprensible y accionable. En lugar de limitarse a mostrar métricas o alertas, cada detección buscará relacionar la patología identificada con la evidencia que la sustenta, su nivel de riesgo y una posible acción de mejora.

Desde el punto de vista técnico, las soluciones existentes demuestran la viabilidad del monitoreo multi-motor y del análisis de rendimiento, pero las capacidades disponibles pueden variar entre motores y las plataformas estudiadas tienen objetivos más amplios que los definidos para QUERYLENS. La oportunidad del proyecto se encuentra en combinar, dentro de una solución de alcance controlado, una capa de recolección *agentless*, un modelo canónico para sentencias, planes de ejecución, eventos de espera y bloqueos, y un motor determinista para la detección de un catálogo definido de ocho anti-patrones de rendimiento y problemas de contención. A esto se suma la reconstrucción del grafo de espera, la trazabilidad de cada hallazgo hasta su evidencia y la generación de explicaciones accionables. Finalmente, el banco de pruebas reproducible con patologías inyectadas de forma controlada permitirá evaluar objetivamente la precisión y exhaustividad de la detección. De esta manera, QUERYLENS no busca reemplazar las plataformas empresariales existentes, sino abordar de manera específica y auditable el diagnóstico de patologías de rendimiento y contención desde la perspectiva de un desarrollador.

## 7. Metodología de desarrollo y plan de trabajo

### 7.1 Enfoque metodológico

El desarrollo de QUERYLENS se realizará mediante un **enfoque de prototipado iterativo**, adecuado para un proyecto que integra diferentes componentes técnicos y requiere validar progresivamente su funcionamiento. El proceso se organizará en ciclos de **diseño, construcción, prueba y ajuste**, permitiendo detectar problemas y realizar modificaciones antes de avanzar a las siguientes etapas.

La solución se desarrollará de manera modular, trabajando sobre la capa de recolección y normalización, el motor de detección, el módulo de contención, la interfaz de análisis y el banco de pruebas. Posteriormente, estos componentes serán integrados para validar el funcionamiento completo de QUERYLENS.

### 7.2 Iteraciones o fases de desarrollo

El desarrollo de QUERYLENS se realizará mediante iteraciones progresivas, en las que cada ciclo permitirá construir, probar y ajustar un conjunto de componentes antes de continuar con el siguiente. Las iteraciones previstas son:

1. **Iteración de requisitos y diseño:** tendrá como propósito establecer las necesidades de la solución y sus bases arquitectónicas. Se definirán los requisitos, el catálogo de patologías, el modelo canónico y las decisiones tecnológicas iniciales. Los resultados de esta iteración servirán como referencia para orientar la implementación y evitar modificaciones importantes en etapas posteriores.

2. **Iteración de recolección y normalización:** tendrá como propósito obtener y unificar la telemetría proveniente de PostgreSQL y MySQL. Se implementará la recolección *agentless*, las funciones de traducción al modelo canónico y la anonimización de literales. Las pruebas permitirán identificar diferencias entre motores y ajustar el modelo para conservar la información necesaria para el diagnóstico.

3. **Iteración de detección y contención:** tendrá como propósito desarrollar las capacidades principales de análisis. Se implementarán las reglas deterministas para los anti-patrones y el análisis de bloqueos y abrazos mortales. Los resultados de las pruebas permitirán calibrar los umbrales, reducir falsos positivos y mejorar la trazabilidad de las detecciones.

4. **Iteración de interfaz y explicación:** tendrá como propósito presentar los resultados de forma comprensible para el usuario objetivo. Se desarrollarán las vistas de análisis, la visualización de grafos y las explicaciones accionables. La revisión de los resultados permitirá ajustar la información presentada y mejorar la claridad de los diagnósticos.

5. **Iteración de integración y validación:** tendrá como propósito comprobar el funcionamiento conjunto de la solución. Se integrarán los componentes y se utilizará el banco de pruebas reproducible para ejecutar patologías controladas. Los resultados obtenidos permitirán realizar los últimos ajustes sobre las reglas, la normalización, el rendimiento y la interfaz antes del cierre del proyecto.

De esta manera, cada iteración no solo incorpora nuevas funcionalidades, sino que utiliza los resultados de las pruebas y revisiones para **refinar progresivamente la solución** hasta obtener una versión integrada y validada de QUERYLENS.


### 7.3 Estrategia de validación

La validación se realizará de forma progresiva mediante **pruebas funcionales, técnicas y de integración**. El banco de pruebas reproducible será la principal fuente de validación, utilizando patologías inyectadas de forma controlada y etiquetas conocidas para comparar los resultados obtenidos por QUERYLENS.

Se medirán principalmente la **precisión y exhaustividad** del motor de detección, además del sobrecosto generado por la recolección. También se verificará la correcta normalización de la telemetría entre motores, la reconstrucción de los grafos de espera, la anonimización de la información y la presentación de explicaciones comprensibles.

La retroalimentación del tutor se incorporará durante las diferentes iteraciones para revisar los requisitos, las decisiones de diseño y la utilidad de los resultados. Cuando sea posible, la evaluación con usuarios o entornos reales se utilizará como fuente adicional de retroalimentación, sin constituir una dependencia para la validación principal.

### 7.4 Plan de trabajo, cronograma o hitos

El plan de trabajo se organizará mediante hitos que permitan realizar un seguimiento del avance del proyecto y verificar la obtención de los principales resultados. Cada hito estará asociado a un entregable concreto.

| Hito | Resultado esperado | Entregable | Temporalidad |
|---|---|---|---|
| **H1. Definición y diseño** | Bases funcionales y arquitectónicas establecidas. | Requisitos, arquitectura y decisiones de diseño. | Semanas 1-2 |
| **H2. Recolección y normalización** | Telemetría de PostgreSQL y MySQL disponible en el modelo definido. | Capa de recolección y normalización. | Semanas 3-6 |
| **H3. Motor de análisis** | Detección de patologías y análisis de contención implementados. | Motor de detección y módulo de contención. | Semanas 7-10 |
| **H4. Interfaz funcional** | Resultados disponibles para consulta y análisis. | Interfaz web y capa de explicación. | Semanas 10-12 |
| **H5. Sistema integrado** | Componentes integrados en un entorno reproducible. | Versión integrada y banco de pruebas. | Semanas 13-14 |
| **H6. Validación y cierre** | Cumplimiento de los criterios de validación y consolidación del proyecto. | Resultados de validación, documentación y versión final de QUERYLENS. | Semanas 15-16 |

El trabajo será distribuido entre los integrantes por componentes, manteniendo actividades conjuntas de **integración, validación y documentación** para garantizar la coherencia de la solución.

## 8. Referencias

[1] pganalyze. (2026). *Query Performance*. pganalyze Documentation. https://pganalyze.com/docs/query-performance

[2] pganalyze. (2026). *pganalyze Documentation*. https://pganalyze.com/docs

[3] SolarWinds. (2026). *Database Performance Analyzer*. https://www.solarwinds.com/database-performance-analyzer

[4] SolarWinds. (2026). *Introduction to Database Performance Analyzer*. SolarWinds Documentation. https://documentation.solarwinds.com/en/success_center/dpa/content/dpa-introduction.htm

[5] Quest Software. (2026). *Foglight for Databases*. https://www.quest.com/products/foglight-for-cross-platform-databases/

[6] Redgate Software. (2026). *Comparison of functionality by database engine*. Redgate Monitor Documentation. https://documentation.red-gate.com/monitor/comparison-of-functionality-by-database-engine-342852844.html

[7] Percona. (2026). *Percona Monitoring and Management*. Percona Documentation. https://docs.percona.com/percona-monitoring-and-management/2/index.html
