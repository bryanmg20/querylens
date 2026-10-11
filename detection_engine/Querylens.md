# QueryLens

QueryLens es una herramienta que diagnostica problemas de rendimiento en bases de datos PostgreSQL y MySQL. Este repositorio incluye:

- *ql_sandbox/* — Entorno de pruebas (sandbox) con PostgreSQL, MySQL y un contenedor sysbench para generar datos de prueba y carga de trabajo (workload).
- *telemetry_pipeline/* — Pipeline en Python que recolecta telemetría de las bases del sandbox y la encola en la base de datos de QueryLens (colas pgmq).
- *querylens_database/* — Base de datos de QueryLens (PostgreSQL + extensión pgmq).

---

## Requisitos previos

- [Docker](https://docs.docker.com/get-docker/) y [Docker Compose](https://docs.docker.com/compose/install/) instalados.
- *Python 3.10 o superior* (para el pipeline).
- Un terminal con *bash* para los scripts .sh (en Windows puedes usar Git Bash, WSL o ejecutarlos dentro del contenedor).

---

## Paso 1 — Levantar las bases de datos (sandbox)

Todo el entorno de prueba (PostgreSQL 17, MySQL 8.0 y el contenedor de sysbench) se levanta con el docker-compose.yml de la carpeta ql_sandbox.

bash
cd ql_sandbox
docker compose up -d


Esto inicia los tres contenedores:

| Contenedor | Servicio | Descripción |
|------------|----------|-------------|
| ql_postgres | PostgreSQL 17 | Puerto 5432 del host |
| ql_mysql | MySQL 8.0 | Puerto 3307 del host (el 3306 interno) |
| ql_sysbench | sysbench + clientes (psql/mysql) | Herramienta para crear datos e inyectar carga |

> El contenedor ql_sysbench solo instala las herramientas y queda esperando comandos. Las instrucciones para generar la carga están en el Paso 3.

Verifica que ambos motores estén listos:

bash
docker compose ps


---

## Paso 2 — Levantar la base de datos de QueryLens (opcional, para el pipeline)

Si vas a ejecutar el pipeline de Python (main.py), también necesitas la base de datos de QueryLens que recibe los mensajes por cola. Esta se levanta desde la raíz del repositorio (usa el puerto 5433):

bash
# Desde la raíz del proyecto
docker compose up -d


Esta base (contenedor querylens_db) crea la extensión pgmq y la cola analyze_job automáticamente al primer arranque (ver querylens_database/init.sql: 

CREATE ROLE postgres WITH SUPERUSER LOGIN;

CREATE EXTENSION IF NOT EXISTS pgmq;

SELECT pgmq.create('analyze_job'); ).

---

## Paso 3 — Entrar a la consola de sysbench y cargar los datos

Las tablas, los datos y el workload se generan con el script scripts/run.sh, que ejecuta sysbench en dos fases para cada motor: prepare (crea tablas e inserta filas) y run (dispara transacciones OLTP concurrentes durante 2 minutos).

1. Entra a la consola del contenedor ql_sysbench:

   bash
   # Desde la carpeta ql_sandbox
   docker compose exec -it sysbench bash
   

2. Dentro del contenedor, ejecuta el script para cargar todo:

   bash
   bash /scripts/run.sh
   

   Verás los logs de PostgreSQL y MySQL. Cuando aparezca Load complete. Both databases ready, las bases tienen datos y carga lista para consultar.

3. Sal del contenedor cuando termines:

   bash
   exit
   

> *Volver a inyectar carga sin recrear los contenedores:* vuelve al Paso 3 y ejecuta de nuevo bash /scripts/run.sh. El script limpia y regenera los datos.
>
> *Ajustar la carga (opcional):* edita las variables al inicio de ql_sandbox/scripts/run.sh: TABLES=10 (tablas), TABLE_SIZE=10000 (filas por tabla), THREADS=4 (hilos concurrentes), DURATION=120 (segundos de carga).

---

## Paso 4 — Ejecutar los scripts para provocar queries

Dentro de la carpeta ql_sandbox/scripts hay scripts que generan consultas y condiciones problemáticas específicas para que QueryLens las detecte. Hay dos subcarpetas: postgres/ y mysql/.

Puedes ejecutarlos de dos formas:

*Opción A — Dentro de la consola del contenedor sysbench:*

bash
cd ql_sandbox
docker compose exec -it sysbench bash
bash /scripts/mysql/simulate_high_impact_mysql.sh


*Opción B — Directo desde el host con docker compose exec:*

bash
cd ql_sandbox
docker compose exec sysbench bash /scripts/postgres/simulate_instability_postgres.sh


### Catálogo de scripts

| Script | Motor | Qué provoca |
|--------|-------|-------------|
| simulate_high_impact_*.sh | PostgreSQL / MySQL | Query pesada con JOIN, subconsulta, DISTINCT, GROUP BY y ORDER BY. Corre en **bucle infinito**, detén con Ctrl+C. |
| simulate_instability_*.sh | PostgreSQL / MySQL | 1000 consultas con pg_sleep/SLEEP aleatorio para simular tiempos de respuesta inestables. Termina solo. |
| simulate_diskspill_*.sh | PostgreSQL / MySQL | Consulta de GROUP BY con work_mem / tmp_table_size reducidos para forzar uso de disco (disk spill). Termina solo. |
| locks.sh | PostgreSQL / MySQL | 5 procesos concurrentes con SELECT ... FOR UPDATE para generar bloqueos. Corre en *bucle infinito*, detén con Ctrl+C. |
| generate_locks.sh | MySQL | Transacciones largas, contenido inverso y lecturas intensivas para forzar locks y posibles deadlocks. Corre en *bucle infinito*, detén con Ctrl+C. |

*Sugerencia de orden de trabajo:* carga primero con run.sh (Paso 3), lanza uno o más scripts de este catálogo en segundo plano para tener consultas activas y, mientras corren, ejecuta el pipeline (Paso 5).

---

## Paso 5 — Ejecutar el pipeline de Python (main.py)

El pipeline recolecta telemetría de PostgreSQL y MySQL, y envía el resultado como mensaje a la cola analyze_job de la base de datos QueryLens.

### Crear el entorno virtual e instalar dependencias

1. Ubícate en la carpeta del pipeline:

   bash
   cd telemetry_pipeline
   

2. Crea y activa el entorno virtual. En *Windows (PowerShell)*:

   powershell
   python -m venv venv
   venv\Scripts\Activate.ps1
   

   En *Windows (Git Bash)* o *Linux/Mac*:

   bash
   python -m venv venv
   source venv/bin/activate
   

   > Si la carpeta venv ya existe, solo actívala (no es necesario crearla de nuevo).

3. Instala las dependencias del archivo requirements.txt:

   bash
   pip install -r requirements.txt
   

   El archivo contiene:

   | Paquete | Para qué se usa |
   |---------|-----------------|
   | sqlalchemy | Motor de base de datos y conexiones ORM |
   | psycopg2 | Conector de PostgreSQL |
   | pymysql | Conector de MySQL |
   | sqlglot | Análisis y normalización de consultas SQL |
   | python-dotenv | Carga las variables del archivo .env |

### Ejecutar el pipeline

Con las bases levantadas (Pasos 1 y 2) y el entorno virtual activo:

bash
python main.py


Si todo funciona verás una salida como:


Diccionario encolado con ID: 1


> *Cambiar el motor a enviar:* por defecto main.py encola la telemetría de *PostgreSQL. Para enviar la de **MySQL*, edita en telemetry_pipeline/main.py la línea payload["source"] = "postgres" y cámbiala por "mysql".

---

## Credenciales y puertos de conexión

| Base | Host | Puerto | Usuario | Contraseña | Base de datos |
|------|------|--------|---------|------------|---------------|
| PostgreSQL (sandbox) | localhost | 5432 | querylens_monitor | monitor_pass | ql_demo |
| MySQL (sandbox) | localhost | 3307 | querylens_monitor | monitor_pass | ql_demo |
| Usuario de aplicación PostgreSQL | localhost | 5432 | ql_user | ql_pass | ql_demo |
| Usuario de aplicación MySQL | localhost | 3307 | app_user | app_pass | ql_demo |
| QueryLens (pgmq) | localhost | 5433 | ql_user | ql_pass | ql_demo |

> Las variables del pipeline se leen de .env (raíz del proyecto) y de telemetry_pipeline/sysbench_databases.py.

---

## Apagar y limpiar

Detén el sandbox:

bash
cd ql_sandbox
docker compose down


Detén la base de QueryLens:

bash
# Desde la raíz del proyecto
docker compose down


Elimina *todos* los datos y volúmenes de la base QueryLens:

bash
docker compose down -v


Para borrar también los datos del sandbox:

bash
cd ql_sandbox
docker compose down -v