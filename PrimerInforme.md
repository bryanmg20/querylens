# Primer Informe - querylens

## Resumen / Abstract

Presenta una síntesis breve del problema abordado, la solución propuesta, el alcance del proyecto, la metodología de desarrollo y el plan general de trabajo. Debe permitir al lector comprender la esencia del proyecto sin necesidad de leer el documento completo.

## 1. Introducción

Redacta la introducción como un texto continuo de 4 párrafos. El primero debe describir el dominio o sector del proyecto, las tendencias tecnológicas relevantes y el rol del software en ese contexto. El segundo debe exponer la situación actual: limitaciones del mercado, carencias funcionales y el impacto en los usuarios. El tercero debe presentar la necesidad técnica identificada y la oportunidad de diseño tecnológico. El cuarto, opcional, debe cerrar con una presentación general de la solución propuesta (nombre, funcionalidades clave e impacto esperado), sirviendo de transición hacia las secciones siguientes.

### Contextos

- **Dominio o sector** (ej. educación, industria, salud, ciudades inteligentes, TI).
- **Tendencias tecnológicas relevantes**.
- **Rol de los sistemas de información / software / datos** en ese contexto.

### Situación actual

- **Limitaciones del mercado actual**.
- **Carencias funcionales o de diseño**.
- **Impacto en usuarios**.

### Necesidad identificada

- **Necesidad técnica clara**.
- **Oportunidad de diseño tecnológico**.

### Propuesta general

- **Nombre del sistema**.
- **Funcionalidades clave**.
- **Impacto esperado**.

## 2. Planteamiento del problema

Define y delimita el problema central, explicando qué se busca resolver y por qué es relevante.

El problema se define como una **carencia o déficit** que se manifiesta como un **estado negativo** en una situación real (no teórica), localizado en una **población objetivo bien definida**. No debe confundirse con la falta de un servicio específico ni con la inexistencia de una solución tecnológica. El problema no es "hace falta un sistema que integre X", sino la evidencia de una situación deficiente: por ejemplo, "existen aplicaciones diferentes e incompatibles en los distintos departamentos de la empresa, lo que genera desconexión entre las unidades y pérdida de calidad en la información para la toma de decisiones". Tampoco se trata de un trabajo para una empresa en particular, sino de una **problemática transferible** a contextos similares.

### 2.1 Descripción del problema

Expone con claridad la problemática, sus causas, a quién afecta y cuáles son sus principales consecuencias.

### 2.2 Justificación

Explica por qué el problema debe ser atendido y cuál es la pertinencia académica, técnica, social o práctica del proyecto.

### 2.3 Restricciones y supuestos iniciales

Indica las principales limitaciones y condiciones asumidas para plantear la solución, tales como tiempo, recursos, acceso a información, disponibilidad de usuarios, infraestructura o restricciones técnicas.

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

Describe a alto nivel la solución planteada para abordar el problema identificado. Explica qué se propone construir, quiénes serían sus usuarios, cómo funcionaría de manera general y por qué constituye una respuesta adecuada dentro del alcance definido.

## 6. Estado del arte / soluciones relacionadas

Presenta antecedentes o soluciones existentes relevantes, con el fin de contextualizar la propuesta y mostrar oportunidades de diferenciación, mejora o aporte.

Responde a las preguntas: ¿qué soluciones existen hoy?, ¿cómo abordan el problema?, ¿qué limitaciones presentan?

### Revisar

- Productos comerciales.
- Soluciones open-source.
- Arquitecturas o enfoques técnicos relevantes.

### Comparar

- Funcionalidad.
- Escalabilidad.
- Costos.
- Usabilidad.
- Limitaciones técnicas.

### Resultados esperados

- Identificación de **vacíos, oportunidades o problemas no resueltos**.
- **Justificación técnica** de por qué se requiere una nueva solución.

## 7. Metodología de desarrollo y plan de trabajo

Describe el enfoque metodológico que orientará el desarrollo del proyecto y la forma en que este se traducirá en actividades, iteraciones y entregables concretos. Debe explicar cómo se construirá, validará y refinará la solución a lo largo del proceso.

### 7.1 Enfoque metodológico

Explica la metodología adoptada para el desarrollo del proyecto, justificando su elección. En particular, debe describirse el uso de un enfoque de prototipado iterativo, indicando cómo se plantea avanzar mediante ciclos sucesivos de diseño, construcción, prueba y ajuste de la solución.

### 7.2 Iteraciones o fases de desarrollo

Describe las principales fases o iteraciones previstas para el proyecto, indicando el propósito de cada una, las actividades principales a realizar y la manera en que cada ciclo contribuirá al refinamiento progresivo de la solución.

### 7.3 Estrategia de validación

Explica cómo se evaluarán los avances en cada iteración, por ejemplo mediante retroalimentación de usuarios, pruebas funcionales, revisión de requerimientos o validaciones técnicas y de usabilidad.

### 7.4 Plan de trabajo, cronograma o hitos

Presenta la planificación general del proyecto en forma de cronograma, tabla o listado de hitos, indicando las actividades principales, los entregables esperados y, cuando aplique, la temporalidad estimada de cada fase.

## 8. Referencias

Incluye las fuentes consultadas y citadas en el documento, en el formato de citación definido para el curso o proyecto.
