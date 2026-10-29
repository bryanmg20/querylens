# Imagen del pipeline: solo extrae de las bases registradas y encola en PGMQ.
#
# requirements.txt es un lock con hashes (scripts/lock_requirements.sh) y todo
# trae wheel (psycopg2-binary incluye su libpq): sin apt ni compilador, el
# build no depende del mirror de Debian del dia.
# Tag + digest: el mismo build en cualquier maquina. Para actualizar,
# cambiar el digest (docker buildx imagetools inspect python:3.11-slim).
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Contrato del snapshot (querylens_contract): viene del contexto adicional
# "contract" de docker-compose, porque vive fuera de esta carpeta y lo comparte
# con detection_engine. --no-deps: su pydantic ya vino del lock.
COPY --from=contract . /contract
RUN pip install --no-cache-dir --no-deps /contract

COPY . .

# Los logs del logger (rotacion 5 MB x 3) caen en /app/logs, con volumen
# montado desde docker-compose.
CMD ["python", "runner.py"]
