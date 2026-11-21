# Manual de desarrollo

## 1. Propósito del documento

Este documento sirve de guía técnica para comprender, mantener, extender y dar continuidad al desarrollo de QueryLens. Está dirigido a futuros equipos que necesiten familiarizarse rápido con la estructura del repositorio, los contenedores, los scripts, las variables de entorno y el flujo de trabajo. Para instalar el sistema, ver [Instalacion.md](Instalacion.md); para el diseño, la validación y los resultados, ver el [Informe final](InformeFinal.md).

## 2. Descripción general del proyecto desde la perspectiva de desarrollo

QueryLens es una herramienta de diagnóstico de rendimiento para bases PostgreSQL y MySQL. Se conecta a una base existente con un usuario de solo lectura, recolecta sus estadísticas y planes de ejecución, y detecta con reglas deterministas ocho antipatrones de rendimiento y cinco patologías de contención. Cada hallazgo se entrega con evidencia, explicación, recomendación, riesgo e impacto.

Es un **conjunto de servicios** en Python y una interfaz en TypeScript, que se comunican a través de una base PostgreSQL con cola de mensajes (PGMQ) y se ejecutan con Docker Compose. El flujo es lineal: el recolector toma una instantánea de la base observada cada 10 s, la traduce a un modelo canónico sin literales, la valida contra un contrato y la encola; el motor de detección consume la cola, ejecuta los detectores y guarda los hallazgos; la API los sirve a la interfaz y la línea de comandos los exporta como reportes.

### 2.1 Tecnologías principales

- **Lenguajes:** Python 3.11, TypeScript 5.5 y Bash.
- **Servicios:** FastAPI y Uvicorn.
- **Acceso a datos:** SQLAlchemy 2, psycopg2 y PyMySQL.
- **Análisis de SQL:** sqlglot.
- **Contratos:** Pydantic 2, en el paquete local `contract/`.
- **Interfaz:** Next.js 14, React 18 y Tailwind CSS 3.
- **Almacenamiento y cola:** PostgreSQL 17 con la extensión PGMQ.
- **Seguridad:** Fernet (`cryptography`) para cifrar credenciales y PyJWT para los tokens.
- **Contenedores e infraestructura:** Docker, Docker Compose y nginx 1.30.
- **Pruebas y CI:** pytest, ruff y GitHub Actions; sysbench para la carga del banco de pruebas.
- **Línea de comandos:** Bash, `psql` y `jq`.

### 2.2 Componentes principales

- **Recolector (`telemetry_pipeline/`):** extrae de cada base registrada las estadísticas por sentencia, sesiones, bloqueos, tablas, índices y planes; anonimiza los literales; valida la instantánea y la encola. Registra su propio estado de salud con un catálogo de problemas.
- **Motor de detección (`detection_engine/`):** consume la cola y ejecuta los ocho detectores de antipatrones y el análisis de contención; guarda cada problema una sola vez, con una fila por detección.
- **Servicio de registro (`auth_service/`):** registra bases con credenciales cifradas, prueba conexiones, emite el token de consulta y enciende o pausa el monitoreo.
- **API de diagnósticos (`api_rest/`):** devuelve a la interfaz los problemas de la sesión de detección de una base, con el estado de evaluación y de recolección.
- **Interfaz web (`frontend/`):** conexión de bases, página de diagnósticos y una página por patrón.
- **Línea de comandos y scheduler (`cli/`):** `querylens` para administrar bases, ventanas y reportes, y un servicio que abre y cierra las ventanas y escribe los reportes.
- **Banco de pruebas (`ql_sandbox/`):** PostgreSQL 17, MySQL 8.0 y sysbench, con escenarios que inyectan cada patología y miden la validación.
- **Servicios externos:** ninguno. Todo funciona con los componentes propios y las bases observadas (ver la sección 9).

## 3. Estructura del repositorio

### 3.1 Árbol general del repositorio

```text
/
├── telemetry_pipeline/     recolector (stages, collectors, health, tests, ci)
├── detection_engine/       motor de detección (anti_patterns, deadlocks, tests, references)
├── contract/               paquete querylens_contract: contrato de la instantánea
├── auth_service/           servicio de registro (FastAPI)
├── api_rest/               API de diagnósticos (FastAPI)
├── frontend/               interfaz web (Next.js)
├── cli/                    línea de comandos, scheduler y plantillas de reportes
├── nginx/                  configuración del proxy
├── querylens_database/     DDL de la base de QueryLens y scripts de la base observada
├── ql_sandbox/             banco de pruebas, escenarios, mediciones y campañas
├── test_runner/            imagen que corre las pruebas del recolector y del detector
├── scripts/                gen_env.py y lock_requirements.sh
├── docs/                   decisiones (ADR), evidencia de la validación y reporte final
├── reports/                reportes generados (no se versionan)
├── .github/workflows/      CI (pipeline.yml)
├── docker-compose.yml      stack de QueryLens
├── .env.example            plantilla de variables
├── pytest.ini              configuración de pytest del recolector
├── README.md, PrimerInforme.md, SegundoInforme.md, InformeFinal.md
├── Instalacion.md, Desarrollo.md
└── INSTALL_HEADLESS.md, PREPARAR_BASE.md
```

### 3.2 Descripción de directorios y archivos relevantes

- **`telemetry_pipeline/`**: `runner.py` es el demonio que repite `main.py` cada `EXTRACT_INTERVAL_S`. `stages/` contiene las etapas (recolección, candidatas, planes, normalización, anonimización, traducción de planes de ambos motores al modelo canónico). `collectors/postgres/` y `collectors/mysql/` leen las vistas de cada motor. `health/` implementa el catálogo de 43 problemas de recolección. `ci/` tiene los goldens y el arnés de mutación. Documentos: `PIPELINE_FLOW.md`, `TESTING.md`, `OVERHEAD_V2.md` y `MEASURE_OVERHEAD.md`.
- **`detection_engine/`**: `main.py` consume la cola; `anti_patterns/` tiene un archivo por antipatrón (`baseline_degradation.py` para AP-01 degradación respecto a la línea base, `avoidable_full_scan.py` para AP-02 escaneo completo evitable, y así los demás) y `engine.py` los ejecuta; `deadlocks/` tiene el análisis de contención, un archivo por patología CT-01 a CT-05; `prioritization.py` ordena por impacto; `umbrales_congelados.json` fija los umbrales validados; `references/` guarda notas de diseño numeradas (`INDEX.md` las lista).
- **`contract/`**: el contrato Pydantic de la instantánea; lo comparten el recolector y el detector. Se instala con `pip install -e contract`.
- **`auth_service/`, `api_rest/`**: cada uno con `app/` (routers, esquemas, seguridad, acceso a datos), `tests/`, `requirements*.in` y `requirements*.txt`.
- **`frontend/`**: `app/` (rutas `conectar` y `diagnostics`), `components/` (paneles, tarjeta de hallazgo, gráficos, páginas de patrones) y `lib/` (cliente de la API, formato, textos de cada antipatrón).
- **`cli/`**: `querylens.sh` es la línea de comandos y el scheduler; `share/` tiene la consulta y las plantillas `jq` de los reportes.
- **`querylens_database/`**: DDL (`init.sql`, `registered_databases.sql`, `pipeline_health.sql`, `hallazgos.sql`, `estado_evaluacion.sql`, `contention_state.sql`, `statement_samples.sql`), roles por servicio (`service_roles.sql`, `apply_service_roles.sh`) y los scripts que prepara el administrador de la base observada (`setup_target_*.sql`, `teardown_target_*.sql`).
- **`ql_sandbox/`**: `docker-compose.yml` propio; `scripts/` con la carga; `scenarios/` con el ejecutor de escenarios; `mediciones/` con las mediciones de CT y de AP; `banco/` con la campaña de un solo comando y el cálculo de las métricas M1 a M4; `explain_*` y `campana_*` con los experimentos que respaldan las reglas.
- **`docs/`**: `decisiones/` (ADR-001 a ADR-003), `evidencia/` (corridas de la validación) y `reporte_validacion_final.md`.

## 4. Organización de la solución a nivel de código

### 4.1 Organización por módulos o capas

Se organiza **por componentes**, cada uno en su carpeta y con su imagen, sus dependencias y sus pruebas. Dentro de ellos hay capas:

- **Recolección:** colectores por motor → etapas de enriquecimiento y normalización → modelo canónico → validación del contrato → cola.
- **Detección:** lectura de la instantánea → detectores independientes → priorización → almacenamiento.
- **Servicios y presentación:** routers FastAPI → esquemas Pydantic → acceso a datos; interfaz que consume la API.

Dos reglas de diseño que conviene respetar:

1. **El motor no tiene ramas por motor.** Las diferencias entre PostgreSQL y MySQL se resuelven en la recolección y quedan absorbidas por el modelo canónico; los detectores no preguntan qué motor es.
2. **Los detectores son independientes.** Ningún antipatrón depende de otro: la condición base que comparten, por ejemplo el escaneo completo en AP-02 escaneo completo evitable, AP-03 predicado no sargable y AP-04 índice ausente, se reimplementa en cada uno.

### 4.2 Relación entre componentes del sistema y código fuente

| Componente | Dónde está | Punto de entrada |
|---|---|---|
| Recolector | `telemetry_pipeline/` | `runner.py` (demonio) y `main.py` (un ciclo) |
| Motor de detección | `detection_engine/` | `main.py` |
| Detectores de antipatrones | `detection_engine/anti_patterns/` | `engine.py` |
| Análisis de contención (CT-01 a CT-05) | `detection_engine/deadlocks/` | `facade.py` |
| Servicio de registro | `auth_service/app/` | `main.py` (`app.main:app`) |
| API de diagnósticos | `api_rest/app/` | `main.py` (`app.main:app`) |
| Interfaz | `frontend/app/` | `layout.tsx`, `page.tsx` |
| Línea de comandos y scheduler | `cli/querylens.sh` | subcomandos y `scheduler` |
| Contrato de la instantánea | `contract/querylens_contract/` | — |
| Esquema de la base de QueryLens | `querylens_database/*.sql` | `init.sql` en el `initdb` |
| Banco de pruebas y validación | `ql_sandbox/` | `banco/banco.py`, `scenarios/run_scenario.py` |

## 5. Contenedores

### 5.1 Contenedores utilizados

| Servicio | Imagen | Función |
|---|---|---|
| `postgres` | `ghcr.io/pgmq/pg17-pgmq` (tag y digest fijos) | Base de QueryLens y cola PGMQ |
| `db-roles` | la misma imagen | Aplica los roles por servicio y termina |
| `auth-service` | `auth_service/Dockerfile` (Python 3.11) | Registro de bases |
| `api-rest` | `api_rest/Dockerfile` | API de diagnósticos |
| `frontend` | `frontend/Dockerfile` (Node 20, tres etapas) | Interfaz |
| `nginx` | `nginx:1.30-alpine` | Punto de entrada único |
| `pipeline` | `telemetry_pipeline/Dockerfile` | Recolector |
| `detection-engine` | `detection_engine/Dockerfile` | Detectores |
| `scheduler` y `cli` | `cli/Dockerfile` (Alpine) | Ventanas, reportes y línea de comandos |
| `tests` | `test_runner/Dockerfile` | Pruebas del recolector y del detector |

Los perfiles de Compose separan los modos: sin perfil es el modo headless; `ui` agrega `api-rest`, `frontend` y `nginx`; `cli` y `test` son para uso bajo demanda.

### 5.2 Archivos relacionados con contenedores

- `docker-compose.yml`: el stack de QueryLens, con healthchecks, topes de CPU y memoria, límites de log y orden de arranque (`depends_on` con condiciones de salud).
- Un `Dockerfile` por servicio, y `detection_engine/Dockerfile.dockerignore`, `telemetry_pipeline/.dockerignore`.
- `ql_sandbox/docker-compose.yml`: el banco de pruebas, con perfiles para otras versiones del motor (`pg15`, `pg16`, `pg18`, `pg19`, `mysql84`).
- `.env` y `.env.example`: variables que Compose sustituye.

### 5.3 Construcción y ejecución de contenedores

```bash
docker compose --profile ui build
docker compose --profile ui up -d --build --wait
docker compose --profile ui logs -f detection-engine
docker compose --profile ui --profile cli down
```

Tras cambiar código de un servicio hay que reconstruir su imagen (`up -d --build <servicio>`); si no, el contenedor sigue con el código anterior.

### 5.4 Redes, puertos y volúmenes

- **Redes:** `edge` (nginx, frontend, auth-service, api-rest) y `backend` (base, recolector, detector, scheduler, CLI, auth-service y api-rest). `backend` no es interna, porque el recolector y el servicio de registro alcanzan bases externas con `host.docker.internal`.
- **Puertos:** solo nginx publica, en `127.0.0.1:8080` (`NGINX_PORT`). Dentro de la red: servicio de registro 8000, API 8001 e interfaz 3000.
- **Volúmenes:** `pgdata` (la base), `pipeline_logs` y `detection_engine_logs`. La carpeta `reports/` del repositorio se monta en `scheduler` y `cli`.
- **Roles:** cada servicio entra a la base con su propio rol (`ql_auth`, `ql_api`, `ql_pipeline`, `ql_detector`, `ql_cli`), con los permisos mínimos que define `service_roles.sql`.

### 5.5 Recomendaciones para modificar contenedores

- Para actualizar una imagen base, cambiar etiqueta **y** digest, y probar la cola y los DDL.
- Al agregar un servicio, darle un tope de recursos, un healthcheck, `logging: *default-logging` y un rol propio con permisos mínimos.
- `NEXT_PUBLIC_*` se incrusta en el bundle durante la construcción: por eso `/auth` y `/api` están fijos en `docker-compose.yml`. No convertirlos en `${VARIABLE}`: en Windows con Git Bash, una variable de la terminal las convertía en rutas como `C:/Program Files/Git/auth` y el registro dejaba de funcionar sin error visible.
- No publicar puertos de la base ni de los servicios. El registro de bases y la prueba de conexión no piden autenticación de usuario; solo nginx, en `127.0.0.1`, queda expuesto.
- Un volumen existente no se debe reutilizar con otra `AUTH_ENCRYPTION_KEY`.

## 6. Scripts y automatizaciones

### 6.1 Scripts principales

| Comando | Para qué |
|---|---|
| `python3 scripts/gen_env.py > .env` | Genera un `.env` con secretos aleatorios y uid/gid |
| `sh scripts/lock_requirements.sh` | Regenera los bloqueos de dependencias de Python (requiere `uv`) |
| `docker compose -p qltest --profile test run --rm tests` | Pruebas del recolector y del detector |
| `docker compose run --rm cli <subcomando>` | Línea de comandos (`add`, `list`, `schedule`, `resume`, `report`, `health`, …) |
| `python -m pytest -m "not integration"` | Pruebas unitarias y de contrato del recolector |
| `ruff check telemetry_pipeline` | Lint |
| `python ql_sandbox/scenarios/run_scenario.py <escenario> --engine postgres --docker` | Inyecta un antipatrón y verifica que se detecte |
| `python ql_sandbox/banco/banco.py [--smoke]` | Campaña de validación de un solo comando (completa: unas 18 h; `--smoke`: cerca de 1 h) |
| `python ql_sandbox/banco/metricas.py <corrida>` | Recalcula M1 a M4 sin correr nada |
| `python telemetry_pipeline/measure_overhead.py` | Medición del sobrecosto (ver `MEASURE_OVERHEAD.md`) |
| `npm run dev`, `build`, `start`, `lint` (en `frontend/`) | Interfaz |

### 6.2 Ubicación de scripts auxiliares

- `scripts/`: generación de `.env` y bloqueo de dependencias.
- `ql_sandbox/scripts/`: carga base de sysbench y simulación de bloqueos y abrazos mortales, por motor.
- `ql_sandbox/mediciones/`, `ql_sandbox/scenarios/` y `ql_sandbox/banco/`: ejecutores de la validación.
- `telemetry_pipeline/ci/`: goldens (`regenerate_goldens.py`), tráfico para integración (`load.py`) y mutaciones (`mutants.py`).
- `querylens_database/setup_target_*.sql` y `teardown_target_*.sql`: preparan y deshacen la base observada.

### 6.3 Consideraciones para su uso

- Los scripts del banco de pruebas solo usan la biblioteca estándar de Python.
- `run_scenario.py` necesita el banco levantado (`docker start ql_postgres ql_mysql ql_sysbench`) y el stack de la raíz con las imágenes al día. Con `--docker` el recolector corre dentro del contenedor, lo que evita el problema de `psycopg2` en Windows.
- Los scripts de `ql_sandbox/` y los `.sh` deben tener finales de línea `LF`; `.gitattributes` lo fuerza para `*.sh`.
- Con Git Bash en Windows, exportar `MSYS_NO_PATHCONV=1` antes de comandos `docker` con rutas absolutas.
- La campaña completa del banco dura unas 18 horas y deja sus resultados en `ql_sandbox/banco/resultados/`, carpeta que no se versiona.

## 7. Variables de entorno

### 7.1 Variables requeridas

`QUERYLENS_USER`, `QUERYLENS_PASSWORD`, `QUERYLENS_DB`, `QL_AUTH_DB_PASSWORD`, `QL_API_DB_PASSWORD`, `QL_PIPELINE_DB_PASSWORD`, `QL_DETECTOR_DB_PASSWORD`, `QL_CLI_DB_PASSWORD`, `AUTH_ENCRYPTION_KEY` y `JWT_SECRET_KEY`. La tabla completa, con las opcionales y sus valores por defecto, está en la sección 2.2 de [Instalacion.md](Instalacion.md) y en [.env.example](.env.example).

### 7.2 Variables por ambiente

No hay archivos separados por ambiente: un solo `.env` por instalación. Lo que cambia entre desarrollo, pruebas y despliegue:

- **Desarrollo:** valores generados por `gen_env.py`; opcionalmente `COMPOSE_PROFILES=ui`.
- **Pruebas:** el servicio `tests` usa la base `QL_TEST_DB` (`ql_test`), distinta de la del stack, porque sus pruebas insertan y desactivan filas de `registered_databases`. El CI genera un `.env` desechable con `gen_env.py`.
- **Despliegue:** `.env` con permisos `600`, `HOST_UID` y `HOST_GID` del dueño del repositorio, y `QL_TZ` y `REPORT_FORMAT` según el cliente.

### 7.3 Archivos de configuración

- `.env`: no se versiona (está en `.gitignore`).
- `.env.example`: plantilla comentada, sí se versiona.
- `telemetry_pipeline/ruff.toml`, `pytest.ini` (raíz) y `detection_engine/pytest.ini`, `auth_service/pytest.ini`, `api_rest/pytest.ini`: configuración de lint y pruebas. Cada componente tiene su propio `pytest.ini` para que los módulos con el mismo nombre (`models`) no se mezclen.
- `detection_engine/umbrales_congelados.json`: umbrales validados de los detectores.

### 7.4 Manejo seguro de secretos

- Son sensibles `QUERYLENS_PASSWORD`, las cinco `QL_*_DB_PASSWORD`, `AUTH_ENCRYPTION_KEY` y `JWT_SECRET_KEY`. Nunca se suben al repositorio ni se pegan en issues o PR.
- `AUTH_ENCRYPTION_KEY` es insustituible: sin ella se pierden las credenciales cifradas de las bases registradas.
- Las credenciales del banco de pruebas (`ql_pass`, `monitor_pass`) no son secretos reales: solo valen para ese banco local.
- No exportar `QL_*_DB_PASSWORD` en la terminal en que se corre `docker compose up`: una variable de la terminal gana sobre el `.env`.
- Los reportes de `reports/` y la evidencia de los hallazgos contienen texto normalizado de consultas; se tratan como datos del cliente y no se versionan.

## 8. Flujo de trabajo de desarrollo

### 8.1 Preparación del entorno

```bash
git clone https://github.com/bryanmg20/querylens.git
cd querylens
git checkout develop
python3 scripts/gen_env.py > .env
docker compose --profile ui up -d --build --wait
```

Para trabajar en un componente con su entorno virtual, ver la sección 3.1 de [Instalacion.md](Instalacion.md). El banco de pruebas se levanta aparte con `cd ql_sandbox && docker compose up -d`.

### 8.2 Desarrollo de nuevas funcionalidades

1. Partir de `develop` y crear una rama `feature/<tema>` o `fix/<tema>`.
2. Para un cambio en los detectores: **las reglas se declaran antes de implementarse**. Cada regla de antipatrón está declarada en el informe (Tabla 2 del segundo informe, y el informe final) y el código debe coincidir con ella; si el código y la regla difieren, se actualiza la declaración, no se cambia el código en silencio. Las notas de diseño van en `detection_engine/references/` y se listan en su `INDEX.md`.
3. Agregar o actualizar pruebas junto con el cambio: una prueba por antipatrón en `detection_engine/tests/anti_patterns/`, y goldens del contrato si cambia la instantánea (`telemetry_pipeline/ci/regenerate_goldens.py`).
4. Si cambia un umbral de un detector, actualizar `umbrales_congelados.json`: la prueba `test_umbrales_congelados.py` falla si no coincide.
5. Si se agrega un antipatrón o se cambia su explicación, actualizar también `frontend/lib/antipatterns.ts` y las páginas de patrones; deben decir lo mismo que los detectores.
6. Validar en el banco de pruebas con el escenario del patrón antes de integrar.

### 8.3 Ejecución de pruebas y validaciones

| Qué | Comando |
|---|---|
| Lint | `ruff check telemetry_pipeline` |
| Recolector, unitarias y de contrato | `python -m pytest -m "not integration"` |
| Motor de detección | `cd detection_engine && python -m pytest` |
| Servicio de registro y API | `cd auth_service && python -m pytest`; `cd api_rest && python -m pytest` |
| Todo en contenedor | `docker compose -p qltest --profile test run --rm tests` y, al final, `docker compose -p qltest --profile test down -v` |
| Interfaz | `npm run lint` y `npx tsc --noEmit` en `frontend/` |

Con `-p qltest`, las pruebas usan un proyecto de Compose aparte y no tocan la base del stack que esté corriendo. Las pruebas de integración y las de extremo a extremo necesitan el banco de pruebas levantado: con el banco detenido fallan siete pruebas del recolector por conexión rechazada, sin que sea un defecto del código. Las pruebas de integración de MySQL leen `performance_schema`, que se vacía al reiniciar el motor; hay que generar tráfico antes.

El CI (`.github/workflows/pipeline.yml`) corre en cada push y tiene estos trabajos:

| Trabajo | Qué comprueba |
|---|---|
| `lint` | `ruff` sobre `telemetry_pipeline` |
| `unit` | Pruebas unitarias y de contrato del recolector |
| `detector` | Pruebas del motor de detección y del contrato contra los goldens |
| `services` (auth_service, api_rest) | Pruebas de los dos servicios contra un PostgreSQL 17 |
| `stack` | Levanta `docker-compose.yml` completo, prueba por nginx el registro y la API, y el CLI y el scheduler |
| `integration` (varias versiones) | Integración contra PostgreSQL 15 a 19 y MySQL 8.0 y 8.4 |

### 8.4 Integración de cambios

- La rama de integración es **`develop`**; `main` recibe los cambios por un PR desde `develop`.
- Cada cambio entra por **pull request** desde su rama de trabajo hacia `develop`, con los trabajos del CI en verde.
- El CI corre en cada push a cualquier rama, así que una rama de trabajo se valida sin esperar el PR.
- Antes de fusionar, el PR debe tener: pruebas nuevas o ajustadas, el informe o la documentación actualizados si cambia una regla o un comportamiento, y los trabajos del CI en verde. Este último criterio es la práctica observada en el repositorio, no una regla impuesta por una protección de rama.

## 9. Dependencias y servicios externos

### 9.1 Servicios externos integrados

Ninguno. QueryLens no envía datos a terceros ni depende de servicios en la nube. Las únicas conexiones externas son:

- Las **bases observadas** (PostgreSQL y MySQL), a las que accede con un usuario de solo lectura.
- **En la construcción**, el registro de imágenes (Docker Hub, GitHub Container Registry), PyPI, npm y Google Fonts (el frontend descarga sus fuentes al compilar).

### 9.2 Requisitos de acceso

- Para desarrollar: acceso al repositorio de GitHub y a internet para construir las imágenes.
- Para observar una base: el administrador debe correr una vez el script `setup_target_*.sql` de su motor y crear el usuario `querylens_monitor` (ver [PREPARAR_BASE.md](PREPARAR_BASE.md)). No sirve en servicios administrados como RDS, Aurora, Cloud SQL o Azure.
- Para el CI: no se necesitan secretos; el `.env` se genera desechable.

### 9.3 Consideraciones de desarrollo y pruebas

- El **banco de pruebas** (`ql_sandbox/`) hace las veces de bases de cliente: PostgreSQL 17 con `pg_stat_statements` y `auto_explain`, y MySQL 8.0 con `performance_schema`. Los escenarios inyectan cada patología con una tabla de un millón de filas y carga continua de sysbench.
- Las pruebas unitarias y de contrato corren solo con fixtures locales, sin bases.
- El sobrecosto sobre la base observada debe mantenerse por debajo del 5 % (métrica M5); toda decisión que afecte a la base observada se mide con `measure_overhead.py`.
- Las mediciones en Windows con todo el sistema activo tienen ruido que supera el margen del 5 %; el informe documenta que se repitieron en Linux.

## 10. Convenciones del proyecto

### 10.1 Convenciones de código

- Python 3.11. Lint con `ruff` (`telemetry_pipeline/ruff.toml`): solo reglas que encuentran defectos (pyflakes, errores de sintaxis y bugbear), no de estilo.
- Dependencias fijas: archivos `.in` con las dependencias directas y `.txt` generados con `uv`, con versiones exactas y hashes. Se instalan con `--require-hashes`. Se regeneran con `scripts/lock_requirements.sh` y se versionan juntos.
- Comentarios y textos para el usuario en español.
- **Los detectores no tienen ramas por motor:** si hace falta una diferencia entre motores, se normaliza en el recolector.
- Cada vez que un texto mencione un antipatrón por su código, debe llevar también su nombre: «AP-04 índice ausente», no solo «AP-04». Los códigos y nombres son: AP-01 degradación respecto a la línea base, AP-02 escaneo completo evitable, AP-03 predicado no sargable, AP-04 índice ausente, AP-05 índice no utilizado o redundante, AP-06 patrón N+1, AP-07 vertido a disco, AP-08 error grave de estimación de cardinalidad, CT-01 abrazo mortal por orden inconsistente, CT-02 abrazo mortal por conversión de modo, CT-03 abrazo mortal por contención de rango, CT-04 ampliación de bloqueos por ausencia de índice y CT-05 cascada por transacción larga.
- Interfaz: TypeScript con `next lint`. Los tipos de las respuestas de la API se declaran una sola vez en `frontend/lib/`.

### 10.2 Convenciones de repositorio

- **Ramas:** `feature/<tema>` y `fix/<tema>`; integración en `develop`; `main` para lo entregado.
- **Commits:** en español, con el formato `tipo(ámbito): descripción (#issue)`. Los tipos más usados son `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `ci` y `build`. Ejemplos del historial: `test(api): pruebas de GET /diagnostics (#56)` y `ci: job services con las pruebas del auth service y de la API (#56)`.
- **Issues:** el trabajo se organiza en issues de GitHub, referenciados en el commit con `(#N)`.
- **Autoría:** los commits llevan como autor a la persona del equipo que los hizo.
- **Datos que no se versionan:** `.env`, `reports/*` (se versiona solo el `.gitkeep`), `ql_sandbox/mediciones/resultados/` y `ql_sandbox/banco/resultados/`.
- **Versionado:** no hay etiquetas de versión (`git tag` está vacío); el estado entregado es el de `main`.

### 10.3 Convenciones de documentación

- Los informes (`PrimerInforme.md`, `SegundoInforme.md`, `InformeFinal.md`) son entregables; el informe final es el documento principal y debe coincidir con el código y con la evidencia de `docs/evidencia/`.
- Las decisiones de arquitectura van como ADR numerados en `docs/decisiones/`, con contexto, alternativas, decisión y consecuencias.
- Las notas de diseño de los detectores van en `detection_engine/references/`, con su índice.
- Si cambia una regla, un umbral o un comportamiento visible, se actualiza en el mismo cambio el documento que lo describe, y los textos de las páginas de patrones de la interfaz.
- El README lista los documentos del repositorio; un documento nuevo se agrega ahí.

## 11. Problemas frecuentes y recomendaciones

### 11.1 Problemas frecuentes

| Problema | Causa y solución |
|---|---|
| `falta en .env` al levantar | Falta una variable obligatoria; completarla o regenerar con `gen_env.py` en un volumen nuevo |
| `password authentication failed` en el recolector o el scheduler | Variables `QL_*_DB_PASSWORD` exportadas en la terminal; `docker compose up -d --force-recreate db-roles` en una terminal limpia |
| El registro no funciona y no llega ninguna petición a nginx | Frontend construido con rutas convertidas por Git Bash; reconstruir con `up -d --build --no-deps frontend` |
| La construcción del frontend falla en `next/font` | Corte de red al bajar Google Fonts; reintentar |
| `psycopg2` no carga en un entorno virtual de Windows | Control de aplicaciones del sistema; usar contenedores, `--docker` en los escenarios o WSL |
| Pruebas de extremo a extremo fallan con conexión rechazada | El banco de pruebas está detenido: `docker start ql_postgres ql_mysql ql_sysbench` |
| Pruebas de MySQL fallan por `performance_schema` vacío | Se reinició el motor; generar tráfico antes |
| Reportes con «relation does not exist» (`cycle_samples`) | Volumen anterior al 25 de octubre de 2026 |
| `querylens add` en Windows responde «motor no soportado» | Finales de línea `\r\n`; enviar los bytes sin `\r` |
| Un escenario del banco corre con detectores viejos | La imagen del motor no se reconstruyó: `up -d --build detection-engine` |

### 11.2 Deuda técnica conocida

- **Sin autenticación de usuarios.** El registro de bases y la prueba de conexión están abiertos a quien llegue a nginx, por lo que solo se publica en `127.0.0.1`. Exponerlo exige agregar autenticación.
- **Sin despliegue real.** Nunca se ejecutó en un servidor ni en la nube; el sobrecosto no se midió con el recolector en una máquina aparte.
- **Usabilidad evaluada solo de forma parcial.** El 20 de noviembre de 2026, diez compañeros de la carrera respondieron un formulario de usabilidad (informe, sección 13.3; respuestas anonimizadas en `docs/evidencia/usabilidad-20261120/`). Falta la evaluación con equipos de desarrollo u operación del objetivo 8. Quedan sin atender cuatro pedidos de esa prueba: un mensaje más sencillo sobre las patologías no evaluadas, una aclaración de que el orden es por impacto acumulado, la explicación de los patrones visible sin esperar a una detección, y el inicio de sesión.
- **AP-01 degradación respecto a la línea base** quitó a propósito el filtro de puntuación z robusta, para que el código coincida con la regla declarada. Una consulta siempre errática puede cumplir razón y aumento mínimo sin degradarse de verdad. Si se reintroduce, debe declararse antes en el informe.
- **AP-07 vertido a disco** solo evalúa sentencias con plan (no CREATE INDEX, REINDEX, CREATE TABLE AS ni UPDATE con ordenamiento). En MySQL puede subcontar por el Bug #112556 de MySQL y por el tamaño de `temptable_max_ram`. Está declarado en la Tabla 2.
- **AP-08 error grave de estimación de cardinalidad** en MySQL depende de que la ejecución siga en `events_statements_history`; si salió del historial, la sentencia no se evalúa. Mejora posible: leer también `events_statements_history_long`.
- **Selección de candidatas:** los planes solo se obtienen para las sentencias de mayor tiempo acumulado; una sentencia reciente o liviana puede tardar en evaluarse.
- **Servicios administrados:** sin acceso al registro del servidor se usa el plan genérico, que con datos sesgados puede no coincidir con el real.
- **Migración de `cycle_samples`:** `pipeline_health.sql` solo corre al inicializar el volumen y `db-roles` no lo aplica; un volumen antiguo no gana la tabla.
- **Construcción del frontend con Google Fonts:** el trabajo `stack` del CI falló de forma intermitente por esto. La corrección de fondo es autohospedar las fuentes con `next/font/local` dentro del repositorio.
- **Solo dos motores.** El modelo canónico permite sumar SQL Server sin cambiar el motor de detección, pero falta el recolector y su traducción.
- **Validación con 10 repeticiones por combinación.** El intervalo de Wilson del 95 % de una exhaustividad de 1,00 es [0,72; 1,00]; llegar a un límite inferior de 0,90 exige unas 35 inyecciones.

### 11.3 Recomendaciones para continuidad

1. Antes de tocar un detector, leer su regla en el informe y su nota en `detection_engine/references/`. Las reglas y los umbrales están congelados porque la validación se hizo contra ellos.
2. Correr la suite completa y, si el cambio toca una regla, el escenario del banco que la cubre, en los dos motores.
3. Mantener el flujo «recolector → modelo canónico → detector» y no meter lógica por motor en los detectores.
4. Probar con un volumen nuevo y también con uno antiguo: las pruebas desechables pasan aunque falle la migración de un volumen que ya existía.
5. Empezar por la deuda con más efecto: autenticación de usuarios (para poder exponer el servicio), fuentes autohospedadas (para el CI) y la aplicación de `pipeline_health.sql` desde `db-roles`.
6. No guardar literales de las consultas: la anonimización es una restricción del proyecto. Verificarla con `ql_sandbox/scenarios/check_literal_leak.py` cuando cambie la normalización.

## 12. Historial de decisiones técnicas relevantes

El detalle de las tres primeras está en `docs/decisiones/`, con su evaluación en el informe (sección 9).

| Decisión | Alternativas | Elegida y por qué |
|---|---|---|
| **Fuente del plan de ejecución** (ADR-001) | A: actividad del momento; B: registro de sentencias; C: lo que el motor ya guarda de cada consulta | **C** (puntuación 4,3 frente a 3,1 y 1,8): en PostgreSQL, el plan de `auto_explain` leído del registro, con el plan genérico de respaldo; en MySQL, la muestra de la sentencia en `performance_schema`. Obtuvo el plan real en 18 de 18 consultas con datos sesgados, y su sobrecosto no se distinguió del ruido. Se ajustó el 29 de octubre de 2026 para traer las filas reales que pide AP-08 |
| **Almacenamiento y cola** (ADR-002) | A: PostgreSQL con PGMQ; B: con RabbitMQ; C: con Kafka | **A** (4,2 frente a 3,8): una sola base para almacenamiento y cola, sin sumar un intermediario que operar, con mensajes ocultos, reintento y archivo |
| **Visualización** (ADR-003) | A: interfaz propia con Next.js; B: Grafana; C: Streamlit | **A** (4,4 frente a 3,25 y 2,7): controla el flujo de registro y la presentación de evidencia, explicación y riesgo, y depende solo del contrato de la API |
| **Ventana de recolección de 10 s** | Ventanas de 60 s | Se pasó a 10 s el 2 de noviembre de 2026; los umbrales tuvieron que reajustarse y se volvieron a validar |
| **Detectores independientes y sin ramas por motor** | Detectores que reutilizan la condición de otros, o con lógica por motor | Cada detector reimplementa su condición base; las diferencias entre motores se normalizan en el recolector. Cada regla se audita sola |
| **Roles de base por servicio** | Un superusuario compartido | Cada servicio con permisos mínimos; `db-roles` los aplica en cada `up` |
| **Aislar la base de pruebas del stack** | Probar sobre la base de desarrollo | El servicio `tests` usa `ql_test`, porque sus pruebas desactivan bases registradas |
| **Umbrales congelados** | Seguir ajustando durante la validación | Se congelaron en `umbrales_congelados.json` antes de la campaña final, para que la validación no se ajustara a sus propios resultados |
| **AP-07 vertido a disco sin cambio de alcance** (20 de noviembre de 2026) | Ampliarlo a sentencias sin plan | Se dejó el código y se declaró la limitación en la Tabla 2 |
| **Seguimiento de problemas** (referencia 005) | Una fila por detección | Cada problema se guarda una sola vez con su clave estable y una fila por detección; la sesión de detección decide qué se muestra como reciente |
| **Rutas del frontend fijas en `/auth` y `/api`** | Variables de `.env` | Fijas en `docker-compose.yml`, para evitar la conversión de rutas de Git Bash |
| **Frontend detrás de nginx, sin CORS** | Frontend publicado en otro puerto | Un único punto de entrada, en `127.0.0.1` |

## 13. Referencias relacionadas

- [Informe final](InformeFinal.md), sobre todo las secciones 9 (alternativas), 10 (arquitectura), 11 (implementación), 12 (despliegue) y 13 (validación).
- [Instalacion.md](Instalacion.md), [INSTALL_HEADLESS.md](INSTALL_HEADLESS.md) y [PREPARAR_BASE.md](PREPARAR_BASE.md).
- [Primer informe](PrimerInforme.md) y [Segundo informe](SegundoInforme.md) (la Tabla 2 declara las reglas de cada patología).
- [telemetry_pipeline/PIPELINE_FLOW.md](telemetry_pipeline/PIPELINE_FLOW.md), [TESTING.md](telemetry_pipeline/TESTING.md), [OVERHEAD_V2.md](telemetry_pipeline/OVERHEAD_V2.md) y [MEASURE_OVERHEAD.md](telemetry_pipeline/MEASURE_OVERHEAD.md).
- [detection_engine/references/INDEX.md](detection_engine/references/INDEX.md).
- [ql_sandbox/instruction.md](ql_sandbox/instruction.md) y [ql_sandbox/banco/README.md](ql_sandbox/banco/README.md).
- [docs/decisiones/](docs/decisiones/) y [docs/evidencia/](docs/evidencia/).
- Documentación oficial: FastAPI, SQLAlchemy, sqlglot, Next.js, PGMQ, PostgreSQL (`pg_stat_statements`, `auto_explain`) y MySQL (`performance_schema`).
