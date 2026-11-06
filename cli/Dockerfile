# CLI y scheduler de QueryLens en modo headless (querylens.sh). La misma
# imagen corre los dos servicios de docker-compose.yml: cli (bajo demanda) y
# scheduler (command: scheduler).
#
# Version fija (tag + digest), como las demas imagenes base. postgresql17-client
# es el psql de la misma version que el servicio postgres; tzdata, para que
# TZ (QL_TZ) cambie la hora de date y de los nombres de los reportes.
FROM alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f

RUN apk add --no-cache bash curl jq postgresql17-client tzdata

COPY share/ /usr/local/share/querylens/
COPY --chmod=755 querylens.sh /usr/local/bin/querylens

ENTRYPOINT ["querylens"]
