# Imagen del pipeline: solo extrae de las bases registradas y encola en PGMQ.
#
# psycopg2 se compila contra libpq (no trae wheel), por eso el toolchain en la
# misma capa: build-essential trae libc6-dev, sin el cual Python.h no encuentra
# stdlib.h, y libpq5 queda como dependencia explicita para sobrevivir al purge.
# El resto de los paquetes traen wheel.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev libpq5 \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# Los logs del logger (rotacion 5 MB x 3) caen en /app/logs, con volumen
# montado desde docker-compose.
CMD ["python", "runner.py"]
