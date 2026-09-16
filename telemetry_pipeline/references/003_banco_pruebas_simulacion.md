# Banco de pruebas y simulación de carga

Cómo generar carga sobre las bases (sysbench) ejecutando `simulate_queries.sh`, y cómo inspeccionar las queries en vivo dentro de los contenedores.

## Generar carga con simulate_queries.sh

1. Abrir una shell dentro del contenedor de sysbench:

```bash
docker exec -it ql_sysbench bash
```

2. Instalar los clientes de ambos motores (una sola vez):

```bash
apt-get update && apt-get install -y --no-install-recommends postgresql-client
apt-get update && apt-get install -y --no-install-recommends default-mysql-client
```

3. Ejecutar la carga (inunda los motores con queries de los scripts de simulación):

```bash
for i in $(seq 1 100); do bash /scripts/script.sh; done
```

## Ver las queries generadas en vivo

Los SELECT capturados por los motores (para confirmar que la telemetría tiene material) se consultan dentro de cada contenedor:

```bash
# MySQL
docker exec -it ql_mysql mysql -uroot -pql_root

# PostgreSQL
docker exec -it ql_postgres postgres -uroot -pql_root
```