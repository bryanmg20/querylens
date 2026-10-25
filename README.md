# querylens

## Resumen ejecutivo
<p align="justify">
QUERYLENS surge en un contexto donde diagnosticar problemas de rendimiento en bases de datos relacionales sigue siendo un proceso artesanal que depende de expertos escasos y herramientas comerciales costosas, dejando a la mayoría de equipos de desarrollo sin una solución accesible. La herramienta se conecta de forma no intrusiva a bases de datos PostgreSQL y MySQL, detecta automáticamente ocho anti-patrones de rendimiento y abrazos mortales entre transacciones, y entrega cada hallazgo con evidencia, explicación en lenguaje claro y recomendación accionable, todo sin instalar nada dentro del servidor. Su valor radica en democratizar el diagnóstico de rendimiento, poniendo en manos de cualquier desarrollador una capacidad que hoy solo está al alcance de especialistas o de organizaciones con presupuesto para soluciones empresariales.
</p>

## Levantar el stack

Requiere Docker con Compose v2.17 o superior (`docker compose version`).

```bash
python3 scripts/gen_env.py > .env   # o copiar .env.example y llenarlo
docker compose up -d --build --wait
```

La aplicación queda en http://querylens.localhost:8080 (nginx, el único puerto publicado). Los navegadores resuelven `*.localhost` a 127.0.0.1 sin tocar el archivo hosts; http://127.0.0.1:8080 también sirve. Las variables están documentadas en [.env.example](.env.example). Las dependencias de Python están congeladas en los `requirements.txt` de cada servicio; se regeneran con `scripts/lock_requirements.sh` (ver [TESTING.md](telemetry_pipeline/TESTING.md#por-qué-hay-dos-requirements-y-dos-archivos-por-cada-uno)).

## Documentación del repositorio

### Primer informe
[Primer Informe.md](PrimerInforme.md): Documento que presenta el planteamiento del problema, los objetivos, la solución propuesta, el estado del arte, la metodología de desarrollo y el plan de trabajo del proyecto.

### Segundo informe
[Segundo Informe.md](SegundoInforme.md): Documento que presenta el estado actual del proyecto, incluyendo los avances logrados, las validaciones realizadas y los aspectos pendientes.

### Informe final

|Documento |	Descripción|
|----------|-------------|
|[InformeFinal.md](InformeFinal.md)|	Documento principal del proyecto|
|[instalacion.md](instalacion.md)|	Guía de instalación, desarrollo y despliegue|
|[Desarrollo.md](Desarrollo.md)|	Detalles técnicos del desarrollo|

### Planes de ejecución

[plan_patterns.md](telemetry_pipeline/plan_patterns.md): patrones canónicos de `EXPLAIN` para PostgreSQL y MySQL, con consultas SQL de ejemplo.

## Estudiantes

|Nombre|	GitHub|
|------|--------|
|Bryan Montoya|	[@bryanmg20](https://github.com/bryanmg20)|
|Alan García |	[@AlanSan25](https://github.com/AlanSan25)|
|Alejandra Restrepo|	[@alejarc](https://github.com/alejarc)|

## Tutores

- Daniel Romero Martínez
