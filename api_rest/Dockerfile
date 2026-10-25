# Tag + digest: el mismo build en cualquier maquina. Para actualizar,
# cambiar el digest (docker buildx imagetools inspect python:3.11-slim).
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Sin toolchain ni libpq-dev: psycopg2-binary trae su propia libpq.
# requirements.txt es un lock con hashes (scripts/lock_requirements.sh).

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
