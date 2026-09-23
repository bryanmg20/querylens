# Guía para el segundo informe del proyecto

En el segundo informe se reflejará el trabajo desarrollado durante el semestre y se avanzará hacia una estructura más formal y cercana a la versión final del documento​
​
Por esta razón, cada componente deberá desarrollarse con un mínimo de tres párrafos, con el fin de asegurar el nivel de profundidad y coherencia esperado en un informe final.

## Resumen / Abstract

Presenta una síntesis del proyecto, incluyendo el problema abordado, la solución propuesta, el estado actual del desarrollo, los principales avances logrados, las validaciones realizadas hasta el momento y los aspectos pendientes hacia la entrega final.

## 1. Introducción

Presenta el contexto del proyecto, la necesidad u oportunidad identificada y una breve descripción del estado actual del trabajo.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#1-introducción).

## 2. Marco conceptual

Presenta los conceptos, métodos, técnicas y términos fundamentales necesarios para comprender adecuadamente el problema, la solución propuesta y las decisiones técnicas del proyecto.

## 3. Planteamiento del problema

Define el problema central que aborda el proyecto y su relevancia.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#2-planteamiento-del-problema).

### 3.1 Descripción del problema

Expone la problemática, sus causas, a quién afecta y sus principales consecuencias.

### 3.2 Restricciones y supuestos de diseño

Indica las limitaciones y condiciones consideradas para el desarrollo de la solución.

### 3.3 Alcance actualizado

Delimita qué incluye y qué no incluye el proyecto en su estado actual, señalando si hubo ajustes respecto al planteamiento inicial.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#3-alcance-del-proyecto).

## 4. Objetivos

Presenta el objetivo general y los objetivos específicos que orientan el proyecto.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#4-objetivos).

## 5. Estado del arte / soluciones relacionadas

Resume soluciones o antecedentes relevantes y explica cómo se posiciona la propuesta frente a ellos.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#6-estado-del-arte--soluciones-relacionadas).

## 6. Solución propuesta

Describe la solución desarrollada a alto nivel, explicando su enfoque general, sus usuarios objetivo, su propuesta de valor y su relación con el problema y el alcance definidos.

## 7. Metodología de desarrollo

Describe el enfoque metodológico seguido durante el proyecto, las iteraciones realizadas, las validaciones ejecutadas y los ajustes introducidos a partir de los hallazgos obtenidos.

Ver detalle completo en el [Primer Informe](./PrimerInforme.md#7-metodología-de-desarrollo-y-plan-de-trabajo).

## 8. Requerimientos

Presenta los requerimientos que guían el desarrollo de la solución.

### 8.1 Funcionales

Describe las funcionalidades y comportamientos que el sistema debe ofrecer.

### 8.2 No funcionales

Define atributos de calidad y restricciones del sistema, como rendimiento, seguridad, usabilidad, mantenibilidad o escalabilidad.

## 9. Evaluación de alternativas

Expone las alternativas tecnológicas o arquitectónicas consideradas, los criterios de comparación utilizados y la justificación de la opción seleccionada.

### Pregunta: ¿Cuál alternativa ofrece mejor desempeño bajo carga esperada?

**Criterios de comparación:**

- **Latencia promedio y máxima**: tiempo de respuesta de operaciones críticas.
- **Throughput (capacidad de procesamiento)**: número de solicitudes que el sistema puede manejar por unidad de tiempo.
- **Comportamiento bajo carga concurrente**: degradación del sistema cuando aumenta el número de usuarios simultáneos.

### Pregunta: ¿Qué grado de acoplamiento introduce cada opción?

**Criterios de comparación:**

- **Dependencia de servicios externos**: nivel en que el sistema depende de plataformas como APIs externas.
- **Interdependencia entre módulos internos**: qué tanto un cambio en un módulo afecta a otros.
- **Facilidad de sustitución de componentes**: capacidad de reemplazar una tecnología (ej: backend) sin rediseñar todo el sistema.

### Pregunta: ¿Qué nivel de disponibilidad y tolerancia a fallos ofrece cada alternativa?

**Criterios de comparación:**

- **Tiempo de disponibilidad (uptime esperado)**: porcentaje de tiempo en que el sistema está operativo.
- **Mecanismos de recuperación ante fallos**: existencia de redundancia, backups o reintentos automáticos.
- **Impacto de fallos parciales**: qué ocurre si un componente falla (¿cae todo el sistema o solo una parte?).

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

Incluye las fuentes consultadas y citadas en el documento.
