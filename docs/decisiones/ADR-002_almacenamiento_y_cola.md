# ADR-002. Almacenamiento y cola de mensajes

- **Estado:** aceptada. Se adoptó el 11 de septiembre de 2026 y se evaluó de forma retrospectiva el 12 de octubre de 2026.
- **Evaluación completa:** Segundo Informe, sección 9.5 (Tablas 11 y 12).

## Contexto

El recolector produce una instantánea por base registrada en cada ciclo, y el motor de detección debe procesarla sin que el recolector espere. Además, QUERYLENS necesita almacenar las bases registradas, el estado de la recolección, el historial de ventanas por sentencia y los hallazgos.

La carga esperada es pequeña: un mensaje por base y ciclo, normalmente de menos de 1 MB. El MVP corre en un anfitrión de 2 GB de memoria. Una instantánea perdida es una ventana sin diagnóstico, así que la cola debe conservar los mensajes y reintentar los que fallan.

Esta decisión no genera carga sobre las bases observadas, porque la cola y el almacenamiento son internos a QUERYLENS. Por eso el sobrecosto medido no forma parte de sus criterios.

## Alternativas consideradas

- **A. PostgreSQL con la extensión PGMQ.** La cola vive en tablas de la misma base que ya se necesita para el almacenamiento. Ofrece mensajes ocultos durante el procesamiento (tiempo de visibilidad), reintento al vencer ese tiempo y archivo de los mensajes procesados.
- **B. PostgreSQL con RabbitMQ.** Agrega un intermediario de mensajes con confirmaciones de entrega, colas persistentes y réplicas en clúster.
- **C. Apache Kafka con PostgreSQL.** Agrega una plataforma de registros distribuida que replica cada partición según un factor de replicación.

## Decisión

Se adopta la **alternativa A**, con la mayor puntuación ponderada (4,2 frente a 3,8 de B y 3,8 de C). Los pesos fueron 20 % para el desempeño, 40 % para el acoplamiento y 40 % para la disponibilidad.

- **Desempeño:** las tres alternativas sobran para la carga esperada, así que este criterio no las distingue.
- **Acoplamiento:** A no agrega ningún servicio y se usa con SQL desde los mismos componentes. B y C agregan un intermediario o un clúster con su propia operación.
- **Disponibilidad:** B y C son superiores gracias a la replicación. Sin embargo, esa ventaja aporta poco mientras el almacenamiento sea un único PostgreSQL del que depende todo el sistema.

## Consecuencias

- **Positivas:** una sola base ofrece persistencia, transacciones y cola, y los mensajes no procesados se reintentan sin perderse.
- **Compromiso aceptado:** la cola comparte la disponibilidad de la base de QUERYLENS. Si la base cae, cae la cola, aunque el sistema tampoco funciona sin la base.
- **Reversibilidad:** el acceso a la cola está encapsulado en pocas funciones de lectura, archivo y envío (`detection_engine/main.py`, `telemetry_pipeline/enqueue.py`). Puede reemplazarse sin cambiar el resto del sistema.
- **Revisión:** si QUERYLENS se desplegara con alta disponibilidad, habría que reevaluar esta decisión.
