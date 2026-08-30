# QUERYLENS — Sandbox

## Arrancar todo

```bash
docker compose up
```

Esto hace tres cosas solo:
1. Levanta PostgreSQL 17 y MySQL 8.0
2. Espera a que ambos estén listos
3. sysbench crea las tablas, inserta datos e inyecta carga por 2 minutos

Cuando veas `Carga completada` en los logs, las bases están listas.

## Conectarse

**PostgreSQL**
```bash
psql -h localhost -U ql_user -d ql_demo
# password: ql_pass
```

**MySQL**
```bash
mysql -h 127.0.0.1 -u ql_user -pql_pass ql_demo
```

O conéctate con DBeaver / TablePlus usando los mismos datos.

## Volver a inyectar carga cuando quieras

```bash
docker compose start sysbench
```

## Ajustar la carga (opcional)

Edita las primeras líneas de `scripts/run.sh`:

```bash
TABLES=10        # tablas que crea sysbench
TABLE_SIZE=10000 # filas por tabla
THREADS=4        # hilos concurrentes
DURATION=120     # segundos de carga
```

## Apagar y limpiar

```bash
docker compose down -v
```