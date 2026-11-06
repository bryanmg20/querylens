# ADR-003. Tecnología de visualización

- **Estado:** aceptada. Se adoptó el 19 de septiembre de 2026 y se evaluó de forma retrospectiva el 12 de octubre de 2026.
- **Evaluación completa:** Segundo Informe, sección 9.6 (Tablas 13 y 14).

## Contexto

La interfaz debe permitir que un desarrollador sin formación de DBA registre una base y entienda sus hallazgos: qué ocurre, con qué evidencia, qué hacer y con qué riesgo. Además, debe mostrar el estado de la recolección y qué patologías no pudieron evaluarse.

La interfaz solo lee resultados ya calculados a través de la API de diagnósticos y no genera carga sobre las bases observadas. Por eso el sobrecosto medido no forma parte de sus criterios. En su lugar se agrega un criterio de **adecuación al usuario**, porque los criterios de la plantilla no capturan por sí solos el propósito de esta decisión.

## Alternativas consideradas

- **A. Interfaz propia con Next.js.** Consume la API de diagnósticos y presenta los hallazgos con componentes diseñados para el caso.
- **B. Grafana.** Sus tableros de paneles consultan una fuente de datos, que en este caso sería directamente la base de QUERYLENS.
- **C. Streamlit.** Construye aplicaciones web en Python y vuelve a ejecutar el script en cada interacción.

## Decisión

Se adopta la **alternativa A**, con la mayor puntuación ponderada (4,4 frente a 3,25 de C y 2,7 de B). Los pesos fueron 40 % para la adecuación al usuario, 25 % para el acoplamiento, 20 % para el desempeño y 15 % para la disponibilidad.

- **Adecuación al usuario:** A permite controlar el flujo de registro y la presentación de la evidencia, la explicación, la recomendación y el riesgo. Grafana está orientado a series de tiempo, y el registro de bases y las explicaciones en texto no encajan en sus tableros.
- **Acoplamiento:** A depende solo del contrato de la API. Grafana consultaría las tablas directamente, lo que acopla los tableros al esquema y elude la verificación de tokens por base.
- **Disponibilidad:** A corre en un contenedor sin estado. Si cae, la recolección y la detección siguen funcionando.

## Consecuencias

- **Positivas:** la presentación se diseña para el usuario objetivo, y el acceso a los diagnósticos pasa siempre por la API, que exige el token de la base consultada.
- **Compromiso aceptado:** el equipo debe mantener código de interfaz propio (Next.js, React, TypeScript y Tailwind CSS).
- **Evolución posible:** Grafana podría complementar la interfaz para mostrar la evolución de las métricas en el tiempo, siempre que consulte la API y no las tablas.
