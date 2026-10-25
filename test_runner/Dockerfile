# Imagen de tests: solo dependencias. El codigo se monta en /repo desde
# docker-compose (servicio tests), asi que no hay que reconstruir al cambiar
# un test; si cambian los requirements, docker compose build tests.
#
# Corre dentro de la red de compose: llega a la base por postgres:5432 sin
# que la base publique un puerto en el host.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential + libpq-dev: psycopg2 se compila (no trae wheel).
# postgresql-client: run.sh crea y prepara la base de tests con psql.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Los requirements vienen de los contextos adicionales de docker-compose
# (pipeline, detector, contract): esta carpeta no los contiene.
COPY --from=pipeline requirements.txt requirements-dev.txt /deps/pipeline/
COPY --from=detector requirements.txt /deps/detector/
COPY --from=contract . /deps/contract
RUN pip install --no-cache-dir \
        -r /deps/pipeline/requirements-dev.txt \
        -r /deps/detector/requirements.txt \
        /deps/contract

# run.sh tambien sale del repo montado: cambiarlo no exige reconstruir.
WORKDIR /repo
ENTRYPOINT ["sh", "/repo/test_runner/run.sh"]
