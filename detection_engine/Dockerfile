# Imagen del Detection Engine (antipatrones): consume la cola PGMQ analyze_job
# que llena el pipeline, corre las reglas y guarda los hallazgos.
#
# El contexto de build es la RAIZ del repo (ver docker-compose.yml), no esta
# carpeta: storage.py lee los DDL de ../querylens_database, asi que esa carpeta
# se copia junto al codigo.
#
# psycopg2 se compila contra libpq (no trae wheel), por eso el toolchain en la
# misma capa y libpq5 como dependencia explicita para sobrevivir al purge.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY detection_engine/requirements.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev libpq5 \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY detection_engine/ .

# /querylens_database queda como hermana de /app: storage.py la resuelve como
# Path(__file__).parent.parent / "querylens_database".
COPY querylens_database/ /querylens_database/

# Los logs (rotacion 5 MB x 3) caen en /app/logs, con volumen en docker-compose.
CMD ["python", "main.py"]
