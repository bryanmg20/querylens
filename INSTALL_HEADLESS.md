# Instalación sin interfaz (modo headless + CLI)

QueryLens se instala **dentro de la red del cliente**, en el mismo servidor de la base o en una VM cercana, y se administra desde la terminal. No se publica ningún puerto, no se instala ningún agente y las credenciales de la base no salen del servidor.

| Servicio | Modo headless (`up`) | Con interfaz (`--profile ui`) |
|---|---|---|
| `postgres`, `db-roles`, `auth-service`, `pipeline`, `detection-engine` | Sí | Sí |
| `scheduler` (ventanas de recolección y reportes) | Sí | Sí |
| `api-rest`, `frontend`, `nginx` | No | Sí |
| `cli` | Bajo demanda (`docker compose run --rm cli`) | Bajo demanda |

## 1. Requisitos

- Linux con Docker y Compose v2.17 o superior (`docker compose version`).
- Acceso de administrador a la base que se va a monitorear, una sola vez, para prepararla (paso 2).
- PostgreSQL 17 o superior, o MySQL 8.0.18 o superior.

## 2. Preparar la base a monitorear (DBA)

Un script por motor activa todo lo que QueryLens necesita y crea el usuario `querylens_monitor`; si lo pide, se reinicia la base una vez. El paso a paso está en [PREPARAR_BASE.md](PREPARAR_BASE.md):

```bash
psql -U postgres -d <base> -f querylens_database/setup_target_postgres.sql   # PostgreSQL
mysql -u root -p <base> < querylens_database/setup_target_mysql.sql          # MySQL
```

## 3. Instalar QueryLens

```bash
git clone <repo> /opt/querylens && cd /opt/querylens
python3 scripts/gen_env.py > .env     # secretos aleatorios y uid/gid del usuario
chmod 600 .env
docker compose up -d --build --wait
```

`.env` guarda `AUTH_ENCRYPTION_KEY`, la clave con la que se cifran las credenciales registradas. Si se pierde o se cambia, hay que volver a registrar las bases. Las demás variables están en [.env.example](.env.example). Las más útiles en este modo son:

| Variable | Por defecto | Para qué |
|---|---|---|
| `QL_TZ` | `America/Bogota` | Zona en que se escriben y se muestran las horas |
| `REPORT_FORMAT` | `md` | Formato del reporte automático: `md`, `csv` o `json` |
| `REPORT_GRACE_S` | `300` | Segundos que espera el scheduler tras el fin de una ventana antes de escribir el reporte |
| `SCHEDULER_INTERVAL_S` | `60` | Cada cuánto revisa las ventanas |
| `EXTRACT_INTERVAL_S` | `10` | Cada cuánto recolecta el pipeline |

Un alias evita escribir la ruta completa:

```bash
alias querylens='docker compose -f /opt/querylens/docker-compose.yml run --rm cli'
```

## 4. Uso

Sin argumentos, `querylens` abre un menú. Cada opción también existe como subcomando, para scripts:

| Comando | Qué hace |
|---|---|
| `add` | Pide las credenciales (la contraseña sin eco), prueba la conexión, registra la base y pide la ventana de recolección |
| `test` | Prueba una conexión sin registrarla |
| `list` | Bases registradas, su estado y su ventana |
| `schedule ID --until T [--from T]` | Programa la ventana de recolección. `--from` es ahora por defecto |
| `unschedule ID` | Quita la ventana; el monitoreo queda como esté |
| `pause ID` / `resume ID` | Pausa o reanuda el monitoreo |
| `report ID [--since 7d \| --from T --until T] [--format md\|csv\|json] [--include-evidence]` | Escribe un reporte en `reports/`. Sin rango usa la ventana programada o, si no hay, los últimos 7 días |
| `health ID` | Último ciclo del pipeline y problemas abiertos (extensión faltante, permisos, conexión) |

Las horas (`T`) se escriben como `"2026-10-27 08:00"` (en `QL_TZ`), `now` o relativas a ahora: `+2h`, `+30min`, `-1 day`.

Ejemplo:

```bash
querylens add                                   # registra y programa
querylens schedule db_a1b2c3d4 --from "2026-10-27 08:00" --until "2026-10-27 18:00"
querylens list
querylens report db_a1b2c3d4 --since 24h --format csv
```

### Ventanas de recolección

El servicio `scheduler` revisa las ventanas de todas las bases cada `SCHEDULER_INTERVAL_S`:

1. Cuando llega `--from`, enciende el monitoreo de la base.
2. Cuando llega `--until`, lo apaga.
3. `REPORT_GRACE_S` después, escribe el reporte. El detector procesa en diferido los últimos snapshots, así que el reporte incluye también lo que detecte durante ese margen.

Las ventanas se guardan en la base (`registered_databases`): un reinicio del servidor o del scheduler no pierde ninguna. Una pausa manual durante la ventana se respeta. Sin ventana, el monitoreo es continuo y el reporte se pide con `report`.

### Reportes

Los reportes quedan en `reports/` del servidor (`/opt/querylens/reports/<id>-<fecha>.md`), con permisos `600`, a nombre del usuario que generó el `.env`. QueryLens no los envía a ningún lado. Para sacarlos, usar el canal habitual de la organización, por ejemplo:

```bash
scp servidor:/opt/querylens/reports/db_a1b2c3d4-20261027-180500.md .
```

Cada reporte trae el resumen por severidad y por antipatrón, y el detalle de cada hallazgo: tabla, `query_id`, cuántas veces se detectó y entre qué fechas, la explicación y la recomendación. El detector inserta una fila por ciclo mientras el problema siga, y el reporte agrupa esas filas en un solo hallazgo. `evidencia` incluye texto normalizado de las consultas, por eso solo sale con `--include-evidence`.

### Interfaz web (opcional)

```bash
docker compose --profile ui up -d --wait
```

nginx escucha solo en `127.0.0.1:8080`. Para entrar desde otra máquina, un túnel SSH: `ssh -L 8080:127.0.0.1:8080 servidor`, y luego http://querylens.localhost:8080.

## 5. Seguridad

- Ningún puerto se publica en modo headless. El CLI y el scheduler usan la red interna de Docker.
- Las credenciales se cifran con Fernet en el `auth-service`. El CLI usa el rol `ql_cli`, que no puede leer las columnas de credenciales.
- La contraseña que pide `add` no aparece en `ps` ni en el historial de la shell.
- `pg_stat_statements` y `evidencia` contienen texto de consultas. Hace falta la autorización del dueño de la base, y conviene verificar la normalización de literales (`ql_sandbox/scenarios/check_literal_leak.py`).

## 6. Problemas frecuentes

| Síntoma | Qué revisar |
|---|---|
| `add` responde "Sin conexión" | Host, puerto y `pg_hba.conf` o `bind-address` ([PREPARAR_BASE.md](PREPARAR_BASE.md)). Desde el contenedor, `localhost` es el propio contenedor: usar `host.docker.internal` |
| `list` muestra `disponible: no` | El `auth-service` no logra conectarse; revisar `docker compose logs auth-service` |
| El reporte sale vacío | `querylens health ID`: si muestra un problema de configuración, falta un paso de [PREPARAR_BASE.md](PREPARAR_BASE.md) (por ejemplo, el reinicio) |
| No aparece el reporte de una ventana | `docker compose logs scheduler`, y que `reports/` exista y pertenezca a `HOST_UID` |
| Muchas bases a la vez | El pipeline las recorre una tras otra: con decenas de ventanas simultáneas cada ciclo tarda más. Revisar `pipeline_health.last_run` |

## 7. Actualizar

```bash
cd /opt/querylens && git pull
docker compose up -d --build --wait
```

`db-roles` aplica en cada `up` los roles y las columnas nuevas de `registered_databases`, así que un volumen existente no necesita migraciones manuales para esta versión.

## 8. Desinstalar

1. Quitar QueryLens de cada base monitoreada (usuario, `auto_explain`, configuración): ver [Quitar QueryLens de la base](PREPARAR_BASE.md#quitar-querylens-de-la-base).
2. Guardar lo que se quiera conservar de `reports/`.
3. Borrar el stack.

   **Atención:** `-v` borra los volúmenes, es decir, las bases registradas, los hallazgos y la salud del pipeline. No se puede deshacer.

   ```bash
   cd /opt/querylens
   docker compose --profile ui --profile cli down -v --rmi local
   cd / && sudo rm -rf /opt/querylens
   ```
4. Quitar el alias `querylens` del perfil de la shell, si se agregó.
