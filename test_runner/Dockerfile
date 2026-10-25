# Imagen de tests: solo dependencias. El codigo se monta en /repo desde
# docker-compose (servicio tests), asi que no hay que reconstruir al cambiar
# un test; si cambian los requirements, docker compose build tests.
#
# Corre dentro de la red de compose: llega a la base por postgres:5432 sin
# que la base publique un puerto en el host.
# Tag + digest: el mismo build en cualquier maquina. Para actualizar,
# cambiar el digest (docker buildx imagetools inspect python:3.11-slim).
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# postgresql-client: run.sh crea y prepara la base de tests con psql. Las
# dependencias de Python traen wheel (psycopg2-binary): sin compilador.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Los requirements vienen de los contextos adicionales de docker-compose
# (pipeline, detector, contract): esta carpeta no los contiene. Son locks con
# hashes que comparten versiones (scripts/lock_requirements.sh); el contrato
# va aparte y con --no-deps porque un directorio local no tiene hash.
COPY --from=pipeline requirements-dev.txt /deps/pipeline/
COPY --from=detector requirements.txt /deps/detector/
COPY --from=contract . /deps/contract
RUN pip install --no-cache-dir --require-hashes \
        -r /deps/pipeline/requirements-dev.txt \
        -r /deps/detector/requirements.txt \
    && pip install --no-cache-dir --no-deps /deps/contract

# run.sh tambien sale del repo montado: cambiarlo no exige reconstruir.
WORKDIR /repo
ENTRYPOINT ["sh", "/repo/test_runner/run.sh"]
