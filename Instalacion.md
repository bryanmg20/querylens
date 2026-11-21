# Instalación y despliegue

Este documento explica cómo instalar QueryLens para desarrollar y cómo desplegarlo. Complementa al [Informe final](InformeFinal.md) (sección 12, «Despliegue y operación») y al [manual de desarrollo](Desarrollo.md).

**Estado real del despliegue.** QueryLens se ha ejecutado únicamente con Docker Compose en equipos de desarrollo (Windows 11 con Docker Desktop y Ubuntu sobre WSL). No se ha desplegado en un servidor ni en la nube. Lo que este documento llama «despliegue» es el procedimiento previsto para un anfitrión Linux con Docker, que reutiliza los mismos comandos del desarrollo y que el CI ejercita en cada push. Donde algo no se probó, el texto lo dice.

## 1. Descripción general de la solución

QueryLens se conecta a una base PostgreSQL o MySQL ya existente, recolecta sus estadísticas y planes de ejecución, detecta ocho antipatrones de rendimiento y cinco patologías de contención, y entrega cada hallazgo con evidencia, explicación y recomendación. Se instala **junto a la base que observa** (en el mismo servidor o en una máquina cercana) y no instala nada dentro de ella, salvo un usuario de monitoreo y los ajustes que prepara un script del administrador de la base.

### 1.1 Lenguajes y tecnologías utilizadas

| Capa | Tecnología |
|---|---|
| Lenguajes | Python 3.11, TypeScript 5.5, Bash |
| Servicios HTTP | FastAPI y Uvicorn |
| Acceso a datos | SQLAlchemy 2, psycopg2 y PyMySQL |
| Análisis de SQL | sqlglot |
| Contratos | Pydantic 2 (paquete local `contract/`) |
| Interfaz web | Next.js 14, React 18 y Tailwind CSS 3 |
| Almacenamiento y cola | PostgreSQL 17 con la extensión PGMQ (imagen `ghcr.io/pgmq/pg17-pgmq`) |
| Proxy | nginx 1.30 |
| Seguridad | Fernet (`cryptography`) y JWT |
| Contenedores | Docker y Docker Compose |
| Línea de comandos | Bash, `psql` y `jq` (imagen Alpine) |

Las imágenes base se fijan con etiqueta y *digest*, y las dependencias de Python con archivos de bloqueo con hashes, para que la construcción sea la misma en cualquier máquina.

### 1.2 Componentes de la solución

| Componente (servicio de Compose) | Carpeta | Función | Perfil |
|---|---|---|---|
| `postgres` | `querylens_database/` | Base de QueryLens: bases registradas, cola `analyze_job`, hallazgos y estado de la recolección | siempre |
| `db-roles` | `querylens_database/` | Se ejecuta una vez en cada `up` y aplica los roles por servicio; termina con código 0 | siempre |
| `auth-service` | `auth_service/` | Registro de bases, prueba de conexión, cifrado de credenciales, emisión de tokens | siempre |
| `pipeline` | `telemetry_pipeline/` | Recolector: extrae estadísticas y planes cada 10 s y encola la instantánea | siempre |
| `detection-engine` | `detection_engine/` | Consume la cola y ejecuta los detectores | siempre |
| `scheduler` | `cli/` | Ventanas de recolección y reportes automáticos | siempre |
| `api-rest` | `api_rest/` | API de diagnósticos para la interfaz | `ui` |
| `frontend` | `frontend/` | Interfaz web | `ui` |
| `nginx` | `nginx/` | Único punto de entrada (`127.0.0.1:8080`) | `ui` |
| `cli` | `cli/` | Línea de comandos `querylens`, bajo demanda | `cli` |
| `tests` | `test_runner/` | Corre las pruebas del recolector y del detector | `test` |

Sin perfil, `docker compose up` levanta el **modo headless** (sin interfaz y sin puertos publicados), que se administra con la línea de comandos. Con el perfil `ui` se suman la API, la interfaz y nginx. Aparte existe el **banco de pruebas** (`ql_sandbox/`), un segundo proyecto de Compose con PostgreSQL 17, MySQL 8.0 y sysbench para generar carga y probar la herramienta; no forma parte de una instalación de producción.

## 2. Requisitos previos

### 2.1 Software requerido

| Software | Versión | Para qué |
|---|---|---|
| Docker Engine o Docker Desktop | reciente; probado con 29.5 | Ejecutar todos los servicios |
| Docker Compose | v2.17 o superior (`docker compose version`); probado con v5.1 | Orquestar los servicios |
| Git | cualquiera | Clonar el repositorio |
| Python 3 | 3.8 o superior | Solo para correr `scripts/gen_env.py`, que usa la biblioteca estándar |
| Python 3.11 | solo desarrollo sin contenedores | Entorno virtual de cada servicio |
| Node.js 20 | solo desarrollo sin contenedores | Interfaz web con `npm run dev` |

Recursos del anfitrión: los topes de memoria de los contenedores de `docker-compose.yml` suman unos 3,7 GiB (base 1 GiB, detector 1 GiB, recolector 768 MiB y el resto de a 64 a 256 MiB). En reposo cada servicio usa entre 5 y 75 MiB, así que 4 GiB de memoria disponible para Docker dan margen. Esta cifra sale de los topes del archivo, no de una prueba específica.

**Base de datos que se va a monitorear** (la prepara su administrador una sola vez, ver [PREPARAR_BASE.md](PREPARAR_BASE.md)):

- PostgreSQL 15 o superior; con 17 o superior se obtienen todas las detecciones. En 15 y 16 hay menos detecciones y `querylens health` avisa con `PG_VERSION_LIMITED`.
- MySQL 8.0.18 o superior (8.0.22 o superior para los informes completos de abrazo mortal); 8.4 LTS también.
- No sirve en servicios administrados (RDS, Aurora, Cloud SQL, Azure): los scripts cambian la configuración del servidor.

### 2.2 Variables de entorno

Toda la configuración está en un archivo `.env` en la raíz del repositorio, que no se versiona. La plantilla comentada es [.env.example](.env.example); `scripts/gen_env.py` genera uno completo con secretos aleatorios.

**Obligatorias** (Compose falla con «falta en .env» si no están):

| Variable | Qué es |
|---|---|
| `QUERYLENS_USER`, `QUERYLENS_DB` | Superusuario y nombre de la base de QueryLens. Solo lo usan `db-roles` y las pruebas |
| `QUERYLENS_PASSWORD` | Contraseña de ese superusuario |
| `QL_AUTH_DB_PASSWORD`, `QL_API_DB_PASSWORD`, `QL_PIPELINE_DB_PASSWORD`, `QL_DETECTOR_DB_PASSWORD`, `QL_CLI_DB_PASSWORD` | Contraseñas de los cinco roles por servicio (`ql_auth`, `ql_api`, `ql_pipeline`, `ql_detector`, `ql_cli`). `db-roles` las aplica en cada `up` |
| `AUTH_ENCRYPTION_KEY` | Clave Fernet con la que se cifran las credenciales de las bases registradas |
| `JWT_SECRET_KEY` | Clave con la que se firman los tokens de consulta |

**Opcionales** (valor por defecto entre paréntesis):

| Variable | Efecto |
|---|---|
| `COMPOSE_PROFILES` | `ui` hace que `up` incluya siempre API, interfaz y nginx |
| `NGINX_PORT` (8080) | Puerto de nginx en `127.0.0.1` |
| `JWT_EXPIRE_MINUTES` (480) | Duración del token |
| `CORS_ORIGINS` (vacío) | Orígenes CORS; solo se llena si la interfaz corre fuera de nginx |
| `EXTRACT_INTERVAL_S` (10) | Intervalo de recolección |
| `QL_LOG_LEVEL` (INFO) | Nivel de log del recolector |
| `HEALTH_PREFLIGHT_TTL_S` (300), `HEALTH_PURGE_INTERVAL_S` (3600), `HEALTH_RETENTION_DAYS` (15) | Estado de salud del recolector |
| `QL_TZ` (America/Bogota) | Zona horaria de ventanas y reportes |
| `REPORT_FORMAT` (md) | Formato del reporte automático: `md`, `csv` o `json` |
| `SCHEDULER_INTERVAL_S` (60), `REPORT_GRACE_S` (300) | Cada cuánto revisa las ventanas y cuánto espera para escribir el reporte |
| `QL_TEST_DB` (ql_test), `HOST_UID`, `HOST_GID` (1000) | Base de pruebas y uid/gid del dueño del repositorio, para que las pruebas y el scheduler puedan escribir en el repositorio montado |

Las URLs que usa la interfaz (`NEXT_PUBLIC_AUTH_SERVICE_URL` y `NEXT_PUBLIC_API_REST_URL`) **no** son variables de `.env`: están fijas en `/auth` y `/api` en `docker-compose.yml` porque Next.js las incrusta en el código durante la construcción.

## 3. Instalación para ambiente de desarrollo

### 3.1 Desarrollo sin contenedores

QueryLens **no está pensado para correr entero sin contenedores**: la base de QueryLens necesita la extensión PGMQ, los servicios esperan los roles por servicio que crea `db-roles`, y la interfaz se sirve detrás de nginx. Lo que sí se puede hacer fuera de Docker es trabajar en un componente con su entorno virtual y dejar la base en un contenedor. Las pruebas unitarias y de contrato de cada componente corren así en el CI, y esa parte está verificada. Correr los servicios a mano contra la base en contenedor se deriva de las variables de `docker-compose.yml` y del código, y **no se probó como flujo completo**.

#### 3.1.1 Clonar el repositorio

```bash
git clone https://github.com/bryanmg20/querylens.git
cd querylens
git checkout develop        # rama de integración
```

#### 3.1.2 Instalar dependencias

Cada componente tiene su propio entorno virtual y su archivo de bloqueo con hashes. Ejemplo para el recolector (en Windows, `venv\Scripts\activate`):

```bash
python3.11 -m venv telemetry_pipeline/venv
source telemetry_pipeline/venv/bin/activate
pip install --require-hashes -r telemetry_pipeline/requirements-dev.txt
```

| Componente | Archivo de dependencias de desarrollo | Instalación adicional |
|---|---|---|
| `telemetry_pipeline` | `telemetry_pipeline/requirements-dev.txt` | — |
| `detection_engine` | `detection_engine/requirements.txt` más `telemetry_pipeline/requirements-dev.txt` | `pip install --no-deps -e contract` |
| `auth_service` | `auth_service/requirements-dev.txt` | — |
| `api_rest` | `api_rest/requirements-dev.txt` | — |
| `frontend` | `frontend/package.json` | `cd frontend && npm ci` |

En algunos equipos Windows con control de aplicaciones, la biblioteca `psycopg2` no carga dentro del entorno virtual («DLL load failed … Control de aplicaciones»). Si pasa, usar los contenedores (sección 3.2) o WSL.

#### 3.1.3 Configurar variables de entorno

Los servicios leen su conexión de variables del proceso: `DB_HOST`, `DB_PORT`, `QUERYLENS_DB`, `QUERYLENS_USER` y `QUERYLENS_PASSWORD` (más `AUTH_ENCRYPTION_KEY` y `JWT_SECRET_KEY` según el servicio). Para las pruebas basta con el `.env` generado:

```bash
python3 scripts/gen_env.py > .env
```

#### 3.1.4 Ejecutar servicios requeridos

Las pruebas unitarias y de contrato no necesitan ningún servicio. Las de `auth_service` y `api_rest` crean una base temporal en un PostgreSQL y lo reciben por variables (el CI usa `postgres:17`):

```bash
docker run -d --name ql-pg-test -e POSTGRES_PASSWORD=postgres -p 55434:5432 postgres:17
export QL_TEST_PG_HOST=127.0.0.1 QL_TEST_PG_PORT=55434 QL_TEST_PG_USER=postgres QL_TEST_PG_PASSWORD=postgres
```

Para correr los servicios en sí a mano, la base de QueryLens debe ser la imagen con PGMQ y publicar un puerto. `docker-compose.yml` no publica el de `postgres`; habría que agregar un `docker-compose.override.yml` con `ports: ["127.0.0.1:5432:5432"]` para ese servicio. Este camino no está probado.

#### 3.1.5 Iniciar la aplicación

Pruebas (los comandos que ejecuta el CI):

```bash
# Recolector: unitarias y de contrato
python -m pytest -m "not integration"
# Motor de detección
cd detection_engine && python -m pytest
# Servicio de registro y API
cd auth_service && python -m pytest
cd api_rest && python -m pytest
```

Servicios a mano (no probado): con las variables de la sección 3.1.3 apuntando a la base publicada, `uvicorn app.main:app --port 8000` en `auth_service/`, `uvicorn app.main:app --port 8001` en `api_rest/`, `python runner.py` en `telemetry_pipeline/`, `python main.py` en `detection_engine/` y `npm run dev` en `frontend/`. La interfaz usa por defecto `http://localhost:8000` y `http://localhost:8001`, y los dos servicios necesitan `CORS_ORIGINS=http://localhost:3000`.

### 3.2 Desarrollo con contenedores

Es la forma recomendada y la que usa todo el equipo.

#### 3.2.1 Construcción de contenedores

```bash
git clone https://github.com/bryanmg20/querylens.git
cd querylens
python3 scripts/gen_env.py > .env     # secretos aleatorios y uid/gid
docker compose --profile ui build
```

Si el `.env` ya existe, no lo regeneres: cambiar `AUTH_ENCRYPTION_KEY` con un volumen existente deja ilegibles las contraseñas de las bases ya registradas.

#### 3.2.2 Ejecución del entorno

```bash
docker compose --profile ui up -d --build --wait     # con interfaz
docker compose up -d --build --wait                  # modo headless
```

`--wait` espera a que cada servicio quede sano y a que `db-roles` termine con código 0. La primera construcción tarda varios minutos. Con `COMPOSE_PROFILES=ui` en el `.env` no hace falta escribir `--profile ui`.

**Banco de pruebas** (opcional, para tener una base que monitorear y cargar sintética):

```bash
cd ql_sandbox
docker compose up -d                          # PostgreSQL 17 (5432) y MySQL 8.0 (3307)
docker exec ql_sysbench bash /scripts/run.sh  # crea datos y carga; termina con «Load complete»
```

Para registrar una base del banco en QueryLens, el host es `host.docker.internal`, con el puerto 5432 (PostgreSQL) o 3307 (MySQL) y el usuario de monitoreo `querylens_monitor` con contraseña `monitor_pass`. Estas credenciales son solo del banco de pruebas.

#### 3.2.3 Servicios disponibles

| Qué | Dónde |
|---|---|
| Interfaz web (perfil `ui`) | http://localhost:8080 |
| Documentación de la API de registro | http://localhost:8080/auth/docs |
| Documentación de la API de diagnósticos | http://localhost:8080/api/docs |
| Línea de comandos | `docker compose run --rm cli` |
| Reportes del scheduler y del CLI | carpeta `reports/` del repositorio |
| Base de QueryLens | sin puerto publicado: `docker compose exec postgres psql -U <QUERYLENS_USER> -d <QUERYLENS_DB>` |
| Pruebas | `docker compose -p qltest --profile test run --rm tests` |

Solo nginx publica un puerto, y únicamente en `127.0.0.1`. Es deliberado: el registro de una base y la prueba de conexión no piden autenticación de usuario, así que no deben quedar expuestos a una red.

#### 3.2.4 Apagado del entorno

```bash
docker compose --profile ui --profile cli down        # detiene y conserva los datos
docker compose --profile ui --profile cli down -v    # además BORRA los volúmenes
```

**Atención:** `-v` elimina las bases registradas, los hallazgos y el estado de salud. No se puede deshacer.

## 4. Despliegue (en caso de ser aplicable)

### 4.1 Arquitectura de despliegue

Todo corre en **un solo anfitrión con Docker**, en un proyecto de Compose con dos redes: `edge` (nginx y los servicios a los que reenvía) y `backend` (la base de QueryLens y quienes la usan). `auth-service` y `api-rest` están en ambas. El recolector y el servicio de registro salen a las bases observadas por la red del anfitrión. El diagrama de despliegue está en la Figura 6 del [Informe final](InformeFinal.md).

Puntos que importan al desplegar:

- QueryLens se instala **dentro de la red del cliente**, en el mismo servidor de la base o en una máquina cercana. Las credenciales de la base no salen de ese servidor.
- Cada contenedor tiene un tope de CPU y memoria, para no quitarle recursos a una base que comparta el servidor.
- **No tiene autenticación de usuarios.** Para entrar desde otra máquina se usa un túnel SSH (`ssh -L 8080:127.0.0.1:8080 servidor`). Exponerlo a una red exige agregar antes autenticación.
- Las mediciones de sobrecosto se hicieron con todo en una misma máquina, así que sobrestiman el costo de una instalación con el recolector aparte (Informe final, secciones 12 y 14.2).

### 4.2 Proceso de actualización

```bash
cd /opt/querylens
git pull
docker compose --profile ui up -d --build --wait
```

`db-roles` aplica en cada `up` los roles y las columnas nuevas de `registered_databases`, así que un volumen existente no necesita migraciones manuales. Hay una excepción conocida: la tabla `pipeline_health.cycle_samples` solo se crea al inicializar el volumen; un volumen anterior al 25 de octubre de 2026 no la tiene y los reportes fallan con «relation does not exist». La solución más simple es recrear la base de QueryLens (`down -v`), que borra su estado. La alternativa es aplicar el archivo `querylens_database/pipeline_health.sql` con el superusuario (sus sentencias son `IF NOT EXISTS`) y luego correr `docker compose up -d --force-recreate db-roles` para que los roles reciban permisos sobre la tabla nueva; este segundo camino no se probó.

### 4.3 Despliegue sin contenedores

**No está soportado ni se probó.** QueryLens se desarrolló, se validó y se mide únicamente sobre Docker Compose. Una instalación manual exigiría reproducir a mano lo que hoy hacen los contenedores: PostgreSQL 17 con PGMQ, los cinco roles por servicio con sus permisos, el cifrado y las claves compartidas entre servicios, nginx con sus rutas, el servicio de ventanas y la carpeta de reportes. Sin una prueba de ese camino, este documento no incluye pasos para ello.

Las subsecciones siguientes de la plantilla quedan por eso sin contenido propio:

#### 4.3.1 Preparación del servidor

No aplica. Ver la sección 4.4.2 para preparar un anfitrión con Docker.

#### 4.3.2 Instalación de dependencias

No aplica. Las dependencias van dentro de las imágenes.

#### 4.3.3 Configuración de la aplicación

No aplica. Ver las variables de entorno de la sección 2.2.

#### 4.3.4 Ejecución de la aplicación

No aplica.

#### 4.3.5 Actualización de versiones

No aplica. Ver la sección 4.2.

### 4.4 Despliegue con contenedores

#### 4.4.1 Construcción de imágenes

Las imágenes se construyen en el propio servidor, a partir del repositorio:

```bash
git clone https://github.com/bryanmg20/querylens.git /opt/querylens
cd /opt/querylens
docker compose --profile ui build
```

No hay un registro de imágenes propio. La construcción del frontend baja las fuentes de Google Fonts, así que necesita acceso a internet; ver «Solución de problemas».

#### 4.4.2 Ejecución en servidor

```bash
git clone https://github.com/bryanmg20/querylens.git /opt/querylens   # si no se clonó en 4.4.1
cd /opt/querylens
python3 scripts/gen_env.py > .env
chmod 600 .env
docker compose up -d --build --wait                  # headless: solo CLI y scheduler
# o, con interfaz:
docker compose --profile ui up -d --build --wait
```

Antes de registrar una base, su administrador debe prepararla con el script de su motor (ver [PREPARAR_BASE.md](PREPARAR_BASE.md)):

```bash
psql -U postgres -d <base> -f querylens_database/setup_target_postgres.sql   # PostgreSQL
mysql -u root -p <base> < querylens_database/setup_target_mysql.sql          # MySQL
```

Después se registra con `querylens add` o desde la interfaz. El uso del modo headless (comandos, ventanas, sesión de detección y reportes) está en [INSTALL_HEADLESS.md](INSTALL_HEADLESS.md).

Si la base monitoreada corre en el mismo servidor, fuera de Docker, el host a registrar es `host.docker.internal`; hay que permitir esa conexión en la base (ver [PREPARAR_BASE.md](PREPARAR_BASE.md)).

#### 4.4.3 Variables de entorno y secretos

- `.env` contiene todos los secretos y **no se sube al repositorio** (está en `.gitignore`). Permisos `600`.
- `AUTH_ENCRYPTION_KEY` cifra las credenciales de las bases registradas. Si se pierde o se cambia, hay que volver a registrar las bases. Conviene guardar una copia fuera del servidor.
- `JWT_SECRET_KEY` firma los tokens. Cambiarla invalida los tokens vigentes.
- Las contraseñas de los roles por servicio se pueden cambiar en `.env`: `db-roles` las aplica en el siguiente `up`.
- No se debe hacer `export` de las variables `QL_*_DB_PASSWORD` en la terminal donde se corre `docker compose up`: una variable de la terminal gana sobre el `.env`, y los roles quedarían con esa contraseña distinta a la que usan los demás servicios.

#### 4.4.4 Persistencia y redes

| Volumen o carpeta | Contenido | Si se pierde |
|---|---|---|
| `pgdata` | Base de QueryLens: bases registradas, hallazgos, cola y salud | Se pierde todo el estado; hay que registrar de nuevo |
| `pipeline_logs`, `detection_engine_logs` | Registros del recolector y del detector | Solo se pierden logs |
| `./reports/` (carpeta del repositorio) | Reportes del scheduler y del CLI, con permisos `600`, a nombre del usuario de `HOST_UID` | Los reportes ya escritos |

Los reportes contienen datos de la base del cliente y no se envían a ningún lado: para sacarlos se usa el canal habitual de la organización (por ejemplo `scp`).

No existe un procedimiento de copia de seguridad propio del proyecto. Una copia de la base de QueryLens puede hacerse con `pg_dump` desde el contenedor (`docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > respaldo.sql`); es un comando estándar de PostgreSQL y no se ejercitó en el proyecto.

Redes: `edge` y `backend`, ambas creadas por Compose. `backend` no es interna porque el recolector y el servicio de registro deben alcanzar las bases monitoreadas.

#### 4.4.5 Actualización del despliegue

Ver la sección 4.2. Para volver a una versión anterior: `git checkout <etiqueta o commit>` y `docker compose --profile ui up -d --build --wait`. El cambio de versión de PostgreSQL o de PGMQ (imagen de `postgres` y de `db-roles`) debe probarse antes con la cola y los DDL (`init.sql`, `service_roles.sql`).

## 5. Verificación de funcionamiento

1. **Estado de los contenedores.** Todos deben aparecer sanos (`healthy`); `db-roles` termina en `Exited (0)`:

   ```bash
   docker compose --profile ui ps -a
   ```

2. **Interfaz y APIs por nginx** (con el perfil `ui`). Estos son los mismos controles que hace el CI:

   ```bash
   curl -fsS http://127.0.0.1:8080/healthz
   curl -fsS http://127.0.0.1:8080/auth/health
   curl -fsS http://127.0.0.1:8080/api/health
   curl -fsS -o /dev/null http://127.0.0.1:8080/
   ```

3. **Línea de comandos y scheduler** (modo headless):

   ```bash
   docker compose run --rm cli list
   docker compose run --rm cli health <ID>      # último ciclo y problemas abiertos
   ```

4. **Flujo completo.** Registrar una base (con el banco de pruebas, ver 3.2.2), encender su monitoreo y esperar unos ciclos de 10 s. En la interfaz, la página de diagnósticos muestra el estado de la recolección y, si la base tiene problemas, sus hallazgos. Con el banco de pruebas, el ejecutor de escenarios inyecta un antipatrón y verifica que aparezca:

   ```bash
   python ql_sandbox/scenarios/run_scenario.py <escenario> --engine postgres --docker
   ```

5. **Pruebas automáticas.** Con el banco de pruebas levantado, para que pasen también las de integración:

   ```bash
   docker compose -p qltest --profile test run --rm tests
   docker compose -p qltest --profile test down -v
   ```

   Con el banco detenido fallan siete pruebas de extremo a extremo del recolector (conexión rechazada a `host.docker.internal`); no es un defecto del código.

## 6. Solución de problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `docker compose` falla con «falta en .env» | Falta una variable obligatoria. Regenerar con `python3 scripts/gen_env.py` (solo con un volumen nuevo) o completar la que falta |
| `password authentication failed` en el recolector o el scheduler | Los roles quedaron con otra contraseña, normalmente por haber exportado `QL_*_DB_PASSWORD` en la terminal. En una terminal limpia: `docker compose up -d --force-recreate db-roles` y recrear el servicio afectado |
| El formulario de «Conectar» no registra y ninguna petición llega a nginx | La imagen del frontend quedó con las rutas `/auth` y `/api` convertidas en rutas de Git Bash (`C:/Program Files/Git/auth`). Reconstruir con `docker compose --profile ui up -d --build --no-deps frontend` desde PowerShell o desde Linux, sin pasar `--build-arg` a mano |
| La construcción del frontend falla en `next/font` o al bajar Google Fonts | Corte de red intermitente. Reintentar el comando; en el CI se relanza con `gh run rerun --failed`. No se cambió el código |
| `add` responde «Sin conexión» | Revisar host, puerto y `pg_hba.conf` o `bind-address` de la base. Dentro del contenedor, `localhost` es el propio contenedor: usar `host.docker.internal` |
| `list` muestra `disponible: no` | El `auth-service` no logra conectarse: `docker compose logs auth-service` |
| El reporte sale vacío | `querylens health <ID>`: si muestra un problema de configuración, falta un paso de [PREPARAR_BASE.md](PREPARAR_BASE.md) (por ejemplo, el reinicio de la base) |
| `report` o `health` fallan con «relation does not exist» (`cycle_samples`) | Volumen anterior al 25 de octubre de 2026. Ver la sección 4.2 |
| No aparece el reporte de una ventana | `docker compose logs scheduler`, y que `reports/` exista y pertenezca a `HOST_UID` |
| Con el banco de pruebas, fallan las pruebas de MySQL por «performance_schema debería tener digests» | Esas tablas viven en memoria y se vacían al reiniciar MySQL. Generar tráfico antes de las pruebas |
| `querylens add` desde Windows responde «motor no soportado» | Finales de línea `\r\n` en la entrada. Los scripts `.sh` fuerzan `LF` desde `.gitattributes`; si se lanza el CLI a mano desde Windows, enviar los bytes sin `\r` |
| En Linux, el scheduler o las pruebas no pueden escribir en `reports/` o `logs/` | Poner `HOST_UID` y `HOST_GID` con los de `id -u` y `id -g` (`gen_env.py` ya lo hace) |

## 7. Mantenimiento y actualización

- **Actualizar:** sección 4.2. No hay migraciones manuales.
- **Datos que crecen:** el estado de salud del recolector se purga solo (`HEALTH_RETENTION_DAYS`, 15 días por defecto). Los hallazgos no se purgan.
- **Cambio de dependencias de Python:** se edita el archivo `.in` del componente y se regeneran los bloqueos con `pip install uv && sh scripts/lock_requirements.sh`, que se versionan juntos.
- **Cambio de imágenes base:** se cambian etiqueta y *digest* a la vez y se prueba la cola y los DDL antes de integrar.
- **Cambio de umbrales de los detectores:** están congelados en `detection_engine/umbrales_congelados.json` y una prueba automática lo verifica; cambiarlos exige recalibrar (ver el informe).
- **Desinstalar:** primero quitar QueryLens de cada base monitoreada con el script de `teardown_target_*.sql` (ver [PREPARAR_BASE.md](PREPARAR_BASE.md)), guardar lo que se quiera de `reports/` y luego `docker compose --profile ui --profile cli down -v --rmi local`. El detalle está en [INSTALL_HEADLESS.md](INSTALL_HEADLESS.md), sección «Desinstalar».

## 8. Referencias relacionadas

- [Informe final](InformeFinal.md), secciones 10 (arquitectura), 11 (implementación) y 12 (despliegue y operación).
- [Manual de desarrollo](Desarrollo.md).
- [INSTALL_HEADLESS.md](INSTALL_HEADLESS.md): instalación y uso sin interfaz.
- [PREPARAR_BASE.md](PREPARAR_BASE.md): preparación de la base que se va a monitorear.
- [.env.example](.env.example) y [docker-compose.yml](docker-compose.yml).
- [telemetry_pipeline/PIPELINE_FLOW.md](telemetry_pipeline/PIPELINE_FLOW.md): flujo del recolector y versiones soportadas.
- [ql_sandbox/instruction.md](ql_sandbox/instruction.md): banco de pruebas.
- [Decisiones de arquitectura](docs/decisiones/) (ADR-001 a ADR-003).
