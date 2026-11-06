# Imagen del Detection Engine (antipatrones): consume la cola PGMQ analyze_job
# que llena el pipeline, corre las reglas y guarda los hallazgos.
#
# El contexto de build es la RAIZ del repo (ver docker-compose.yml), no esta
# carpeta: storage.py lee los DDL de ../querylens_database, asi que esa carpeta
# se copia junto al codigo.
#
# requirements.txt es un lock con hashes (scripts/lock_requirements.sh) y todo
# trae wheel (psycopg2-binary incluye su libpq): sin apt ni compilador.
# Tag + digest: el mismo build en cualquier maquina. Para actualizar,
# cambiar el digest (docker buildx imagetools inspect python:3.11-slim).
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY detection_engine/requirements.txt .

RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Contrato del snapshot compartido con el pipeline (contract/querylens_contract):
# el detector valida cada mensaje de analyze_job con los mismos modelos.
# --no-deps: su pydantic ya vino del lock.
COPY contract/ /contract/
RUN pip install --no-cache-dir --no-deps /contract

COPY detection_engine/ .

# /querylens_database queda como hermana de /app: storage.py la resuelve como
# Path(__file__).parent.parent / "querylens_database".
COPY querylens_database/ /querylens_database/

# Los logs (rotacion 5 MB x 3) caen en /app/logs, con volumen en docker-compose.
CMD ["python", "main.py"]
