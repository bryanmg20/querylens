#!/usr/bin/env bash
# CLI de QueryLens en modo headless. Corre en el contenedor cli:
#
#   docker compose run --rm cli                  # menu interactivo
#   docker compose run --rm cli <comando> ...    # subcomandos (scripts, cron)
#
# y, con "scheduler", como el servicio scheduler de docker-compose.yml. Ver
# "querylens help" e INSTALL_HEADLESS.md.
#
# Registrar y probar una base va por el auth-service (POST /databases y
# /databases/test-connection), que cifra las credenciales con Fernet y calcula
# el fingerprint: aqui no se pueden generar. Lo demas lee y cambia
# registered_databases, hallazgos y pipeline_health con psql, con el rol
# ql_cli (sin acceso a las columnas de credenciales).
#
# Reglas: los valores llegan a psql como variables (-v id=...) y el SQL los
# usa como :'id', nunca interpolados; la contrasena solo viaja por el entorno
# de jq y por un pipe a curl (no en argv ni en el historial); umask 077 deja
# los reportes legibles solo para su dueno.
set -euo pipefail
umask 077

SHARE_DIR=${QUERYLENS_SHARE:-/usr/local/share/querylens}
REPORTS_DIR=${REPORTS_DIR:-/reports}
AUTH_URL=${AUTH_URL:-http://auth-service:8000}

die() {
    echo "querylens: $*" >&2
    exit 1
}

log() {
    printf '%s scheduler | %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*"
}

# psql sin psqlrc, quieto (sin "UPDATE 1") y detenido en el primer error. El
# SQL llega por stdin: con -c psql no sustituye las variables :'x'.
q() {
    psql -X -q -v ON_ERROR_STOP=1 "$@"
}

usage() {
    cat <<'EOF'
Uso: querylens [comando] [opciones]

Sin comando abre el menu interactivo.

  add                       Registra una base (pide credenciales) y programa
                            su ventana de recoleccion
  test                      Prueba una conexion sin registrarla
  list                      Lista las bases registradas y su ventana
  schedule ID --until T [--from T]
                            Programa la ventana de recoleccion de ID. Al
                            terminar, el scheduler escribe el reporte en
                            reports/. --from por defecto: ahora
  unschedule ID             Quita la ventana (el monitoreo sigue como esta)
  pause ID | resume ID      Pausa o reanuda el monitoreo de ID
  report ID [--since 7d | --from T --until T] [--format md|csv|json]
            [--include-evidence]
                            Escribe el reporte de hallazgos en reports/. Sin
                            rango: la ventana programada o los ultimos 7 dias
  health ID                 Ultimo ciclo del pipeline y problemas abiertos
  scheduler                 Loop del servicio scheduler (no usar a mano)
  help                      Esta ayuda

Horas (T): "YYYY-MM-DD HH:MM" en la zona QL_TZ, "now", o relativas a
ahora: "+2h", "+30min", "-1 day".
EOF
}

# --- Validaciones -----------------------------------------------------------

# Instante T normalizado por Postgres (texto timestamptz en la zona de la
# sesion), o falla. Las relativas se resuelven con now() + interval.
to_ts() {
    local value=${1:-} out
    [[ -n $value ]] || return 1
    if [[ $value == [+-]* ]]; then
        out=$(q -tA -v d="${value#+}" 2>/dev/null <<'SQL'
SELECT (now() + :'d'::interval)::text
SQL
        ) || return 1
    else
        out=$(q -tA -v t="$value" 2>/dev/null <<'SQL'
SELECT :'t'::timestamptz::text
SQL
        ) || return 1
    fi
    printf '%s\n' "$out"
}

require_ts() {
    to_ts "$1" || die "hora no valida: '$1' (use \"YYYY-MM-DD HH:MM\", now, +2h o -7d)"
}

# El id existe en registered_databases (y tiene la forma de uno).
require_db() {
    local id=${1:-} n
    [[ $id =~ ^[A-Za-z0-9_]{1,32}$ ]] || die "identificador no valido: '${id}' (ver querylens list)"
    n=$(q -tA -v id="$id" <<'SQL'
SELECT count(*) FROM registered_databases WHERE database_identifier = :'id'
SQL
    )
    [[ $n == 1 ]] || die "no hay una base registrada con id $id (ver querylens list)"
}

# --- auth-service -----------------------------------------------------------

# POST del JSON que llega por stdin a $AUTH_URL$1. Imprime el cuerpo; falla
# con el detalle del error si la respuesta no es 2xx.
auth_post() {
    local path=$1 out code body detail
    out=$(curl -sS --max-time 60 -X POST -H 'Content-Type: application/json' \
        --data-binary @- -w '\n%{http_code}' "$AUTH_URL$path") \
        || die "no se pudo contactar al auth-service en $AUTH_URL"
    code=${out##*$'\n'}
    body=${out%$'\n'*}
    if [[ $code != 2?? ]]; then
        detail=$(jq -r '.detail | if type == "array"
            then map((.loc | last | tostring) + ": " + .msg) | join("; ")
            else tostring end' <<<"$body" 2>/dev/null || true)
        die "el auth-service respondio $code: ${detail:-$body}"
    fi
    printf '%s\n' "$body"
}

# Pide los datos de conexion. Deja conn_* como globales; la contrasena, en
# conn_password, solo se usa en connection_json.
ask_connection() {
    local default_port
    read -r -e -p "Motor [postgresql/mysql] (postgresql): " conn_engine
    conn_engine=${conn_engine:-postgresql}
    [[ $conn_engine == postgresql || $conn_engine == mysql ]] \
        || die "motor no soportado: $conn_engine"
    read -r -e -p "Host (host.docker.internal si la base corre en este servidor): " conn_host
    [[ -n $conn_host ]] || die "el host es obligatorio"
    default_port=5432
    [[ $conn_engine == mysql ]] && default_port=3306
    read -r -e -p "Puerto ($default_port): " conn_port
    conn_port=${conn_port:-$default_port}
    [[ $conn_port =~ ^[0-9]{1,5}$ ]] || die "puerto no valido: $conn_port"
    read -r -e -p "Usuario: " conn_user
    [[ -n $conn_user ]] || die "el usuario es obligatorio"
    read -r -s -p "Contrasena: " conn_password
    echo >&2
    [[ -n $conn_password ]] || die "la contrasena es obligatoria"
    read -r -e -p "Base de datos: " conn_database
    [[ -n $conn_database ]] || die "la base de datos es obligatoria"
}

# JSON de la conexion (con connection_name si se pasa uno). La contrasena va
# por el entorno de jq ($ENV), no como argumento.
connection_json() {
    QL_CONN_PASSWORD=$conn_password jq -nc \
        --arg engine "$conn_engine" --arg host "$conn_host" --arg port "$conn_port" \
        --arg user "$conn_user" --arg database "$conn_database" --arg name "${1:-}" \
        '{engine: $engine, host: $host, port: ($port | tonumber), username: $user,
          password: $ENV.QL_CONN_PASSWORD, database_name: $database}
         + (if $name == "" then {} else {connection_name: $name} end)'
}

# Prueba la conexion de conn_*; imprime el resultado y falla si no conecta.
check_connection() {
    local body
    body=$(connection_json | auth_post /databases/test-connection)
    jq -r '"\(if .success then "Conexion correcta" else "Sin conexion" end): \(.message)"
        + (if .latency_ms then " (\(.latency_ms | floor) ms)" else "" end)' <<<"$body"
    [[ $(jq -r .success <<<"$body") == true ]]
}

# --- Comandos ---------------------------------------------------------------

cmd_test() {
    ask_connection
    check_connection
}

cmd_add() {
    local name body id answer from until
    read -r -e -p "Nombre de la conexion: " name
    [[ -n $name ]] || die "el nombre es obligatorio"
    ask_connection

    if ! check_connection; then
        read -r -p "Registrar de todos modos? [s/N]: " answer
        [[ $answer == [sS] ]] || die "registro cancelado"
    fi

    body=$(connection_json "$name" | auth_post /databases)
    conn_password=
    id=$(jq -r .database_identifier <<<"$body")
    echo "Base registrada: $name (id $id)"

    echo
    echo "Ventana de recoleccion. Horas: \"YYYY-MM-DD HH:MM\", now o +2h."
    read -r -e -p "Inicio (vacio = ahora): " from
    read -r -e -p "Fin (vacio = monitoreo continuo, sin reporte automatico): " until
    if [[ -z $until ]]; then
        echo "Sin ventana: el monitoreo queda continuo. Reporte: querylens report $id"
        return
    fi
    cmd_schedule "$id" --from "${from:-now}" --until "$until"
}

cmd_list() {
    q -P footer=off <<'SQL'
SELECT database_identifier AS id,
       connection_name AS nombre,
       engine AS motor,
       database_name AS base,
       CASE WHEN is_active THEN 'si' ELSE 'no' END AS disponible,
       CASE WHEN monitoring_enabled THEN 'si' ELSE 'no' END AS monitoreo,
       coalesce(to_char(collect_from, 'YYYY-MM-DD HH24:MI'), '-') AS desde,
       coalesce(to_char(collect_until, 'YYYY-MM-DD HH24:MI'), '-') AS hasta,
       CASE WHEN collect_until IS NULL THEN 'continuo'
            WHEN report_written_at IS NOT NULL THEN 'reporte listo'
            WHEN collect_until <= now() THEN 'terminada'
            WHEN collect_started_at IS NOT NULL THEN 'en curso'
            ELSE 'programada' END AS ventana
FROM registered_databases
ORDER BY created_at
SQL
}

cmd_schedule() {
    local id=${1:-} from=now until='' valid
    shift || true
    while (($#)); do
        case $1 in
            --from) from=${2:-}; shift 2 || die "falta el valor de --from" ;;
            --until) until=${2:-}; shift 2 || die "falta el valor de --until" ;;
            *) die "opcion desconocida: $1" ;;
        esac
    done
    require_db "$id"
    [[ -n $until ]] || die "falta --until (fin de la ventana)"
    from=$(require_ts "$from")
    until=$(require_ts "$until")

    valid=$(q -tA -v from="$from" -v until="$until" <<'SQL'
SELECT CASE WHEN :'until'::timestamptz <= :'from'::timestamptz THEN 'el fin debe ser posterior al inicio'
            WHEN :'until'::timestamptz <= now() THEN 'el fin ya paso'
            ELSE 'ok' END
SQL
    )
    [[ $valid == ok ]] || die "$valid"

    # Si el inicio ya llego, la recoleccion arranca ya; si no, queda pausada y
    # el scheduler la enciende a su hora. report_written_at vuelve a NULL: una
    # ventana nueva tiene su propio reporte.
    q -tA -v id="$id" -v from="$from" -v until="$until" <<'SQL'
UPDATE registered_databases
SET collect_from = :'from'::timestamptz,
    collect_until = :'until'::timestamptz,
    collect_started_at = CASE WHEN :'from'::timestamptz <= now() THEN now() END,
    report_written_at = NULL,
    monitoring_enabled = :'from'::timestamptz <= now(),
    updated_at = now()
WHERE database_identifier = :'id'
RETURNING format('Ventana de %s: %s a %s (%s). Al terminar, el reporte queda en reports/.',
                 database_identifier,
                 to_char(collect_from, 'YYYY-MM-DD HH24:MI'),
                 to_char(collect_until, 'YYYY-MM-DD HH24:MI'),
                 CASE WHEN monitoring_enabled THEN 'recolectando' ELSE 'empieza a su hora' END)
SQL
}

cmd_unschedule() {
    local id=${1:-}
    require_db "$id"
    q -tA -v id="$id" <<'SQL'
UPDATE registered_databases
SET collect_from = NULL, collect_until = NULL,
    collect_started_at = NULL, report_written_at = NULL, updated_at = now()
WHERE database_identifier = :'id'
RETURNING format('Ventana quitada de %s. Monitoreo: %s.', database_identifier,
                 CASE WHEN monitoring_enabled THEN 'activo' ELSE 'pausado' END)
SQL
}

set_monitoring() {
    local id=${1:-} enabled=$2
    require_db "$id"
    q -tA -v id="$id" -v enabled="$enabled" <<'SQL'
UPDATE registered_databases
SET monitoring_enabled = :'enabled'::boolean, updated_at = now()
WHERE database_identifier = :'id'
RETURNING format('Monitoreo de %s: %s.', database_identifier,
                 CASE WHEN monitoring_enabled THEN 'activo' ELSE 'pausado' END)
SQL
}

cmd_health() {
    local id=${1:-}
    require_db "$id"
    echo "Ultimo ciclo del pipeline:"
    q -P footer=off -v id="$id" <<'SQL'
SELECT status AS estado,
       to_char(finished_at, 'YYYY-MM-DD HH24:MI:SS') AS terminado,
       array_to_string(sections_failed, ', ') AS secciones_fallidas,
       engine_version AS version
FROM pipeline_health.last_run
WHERE db_id = :'id'
SQL
    echo
    echo "Captura de abrazos mortales (ultima hora):"
    q -P footer=off -v id="$id" <<'SQL'
SELECT count(*) AS ciclos,
       sum(deadlocks_since_prev) AS abrazos_del_motor,
       count(*) FILTER (WHERE log_window_overrun) AS ciclos_con_desborde,
       round((max(log_bytes_since_prev / nullif(seconds_since_prev, 0)) / 1024)::numeric, 1)
           AS log_kb_s_max
FROM pipeline_health.cycle_samples
WHERE db_id = :'id' AND taken_at >= now() - interval '1 hour'
SQL
    echo
    echo "Problemas abiertos:"
    q -P footer=off -x -v id="$id" <<'SQL'
SELECT severity AS severidad, code AS codigo, nullif(section, '') AS seccion,
       message AS mensaje, remediation AS solucion,
       to_char(last_seen, 'YYYY-MM-DD HH24:MI') AS visto, occurrences AS veces
FROM pipeline_health.issues
WHERE db_id = :'id' AND resolved_at IS NULL
ORDER BY CASE severity WHEN 'blocking' THEN 0 WHEN 'degraded' THEN 1 ELSE 2 END,
         last_seen DESC
SQL
}

# Escribe el reporte de ID en [from, until) y imprime su ruta. grace: segundos
# despues de until que tambien cuentan (report.sql). Se escribe en un temporal
# del mismo directorio y se renombra: nadie lee uno a medias.
write_report() {
    local id=$1 from=$2 until=$3 grace=$4 format=$5 evidence=$6 json tmp path
    json=$(q -tA -v id="$id" -v from="$from" -v until="$until" -v grace="$grace" \
        -v evidence="$evidence" -f "$SHARE_DIR/report.sql") || return 1
    [[ -d $REPORTS_DIR && -w $REPORTS_DIR ]] || {
        echo "querylens: no se puede escribir en $REPORTS_DIR" >&2
        return 1
    }
    path="$REPORTS_DIR/$id-$(date +%Y%m%d-%H%M%S).$format"
    tmp=$(mktemp "$REPORTS_DIR/.$id.XXXXXX") || return 1
    case $format in
        md) jq -r -f "$SHARE_DIR/report_md.jq" <<<"$json" >"$tmp" ;;
        csv) jq -r -f "$SHARE_DIR/report_csv.jq" <<<"$json" >"$tmp" ;;
        json)
            if [[ $evidence == true ]]; then
                jq . <<<"$json" >"$tmp"
            else
                jq 'del(.hallazgos[].evidencia)' <<<"$json" >"$tmp"
            fi
            ;;
    esac || { rm -f "$tmp"; return 1; }
    mv "$tmp" "$path" || { rm -f "$tmp"; return 1; }
    printf '%s\n' "$path"
}

# Rango de la ventana programada de ID ("desde|hasta"), vacio si no tiene.
window_of() {
    q -tA -F '|' -v id="$1" <<'SQL'
SELECT collect_from::text, collect_until::text
FROM registered_databases
WHERE database_identifier = :'id' AND collect_until IS NOT NULL
SQL
}

cmd_report() {
    local id=${1:-} since='' from='' until='' format=md evidence=false window path grace=0
    shift || true
    while (($#)); do
        case $1 in
            --since) since=${2:-}; shift 2 || die "falta el valor de --since" ;;
            --from) from=${2:-}; shift 2 || die "falta el valor de --from" ;;
            --until) until=${2:-}; shift 2 || die "falta el valor de --until" ;;
            --format) format=${2:-}; shift 2 || die "falta el valor de --format" ;;
            --include-evidence) evidence=true; shift ;;
            *) die "opcion desconocida: $1" ;;
        esac
    done
    require_db "$id"
    [[ $format == md || $format == csv || $format == json ]] \
        || die "formato no soportado: $format (md, csv o json)"

    if [[ -n $since ]]; then
        [[ -z $from && -z $until ]] || die "use --since o --from/--until, no los dos"
        from=$(require_ts "-$since")
        until=$(require_ts now)
    elif [[ -n $from || -n $until ]]; then
        from=$(require_ts "${from:-"-7d"}")
        until=$(require_ts "${until:-now}")
    else
        window=$(window_of "$id")
        if [[ -n $window ]]; then
            # Mismo margen que el reporte del scheduler.
            from=${window%%|*}
            until=${window#*|}
            grace=${REPORT_GRACE_S:-300}
            [[ $grace =~ ^[0-9]+$ ]] || die "REPORT_GRACE_S no valido: $grace"
        else
            from=$(require_ts "-7d")
            until=$(require_ts now)
        fi
    fi

    path=$(write_report "$id" "$from" "$until" "$grace" "$format" "$evidence") \
        || die "no se pudo escribir el reporte"
    echo "Reporte: ${path#"$REPORTS_DIR"/} (en reports/ del servidor)"
}

# --- Scheduler --------------------------------------------------------------

# Una vuelta: encender las ventanas que empiezan, apagar las que terminan y
# escribir el reporte de las que terminaron hace mas de $1 segundos. Falla si
# un paso falla (la siguiente vuelta lo reintenta: nada se marca a medias).
# Se llama desde un if, donde set -e no aplica: cada paso lleva su || return.
scheduler_tick() {
    local grace=$1 ids id window path status=0

    # Solo una vez por ventana (collect_started_at): una pausa manual durante
    # la ventana se respeta.
    ids=$(q -tA <<'SQL'
UPDATE registered_databases
SET monitoring_enabled = true, collect_started_at = now(), updated_at = now()
WHERE collect_from <= now() AND collect_until > now() AND collect_started_at IS NULL
RETURNING database_identifier
SQL
    ) || return 1
    for id in $ids; do log "$id | empieza la recoleccion"; done

    # Hasta escribir el reporte: reanudar a mano despues del reporte no se
    # vuelve a apagar.
    ids=$(q -tA <<'SQL'
UPDATE registered_databases
SET monitoring_enabled = false, updated_at = now()
WHERE collect_until <= now() AND report_written_at IS NULL AND monitoring_enabled
RETURNING database_identifier
SQL
    ) || return 1
    for id in $ids; do log "$id | termina la recoleccion"; done

    ids=$(q -tA -v grace="$grace" <<'SQL'
SELECT database_identifier
FROM registered_databases
WHERE report_written_at IS NULL
  AND collect_until + make_interval(secs => :'grace'::int) <= now()
ORDER BY collect_until
SQL
    ) || return 1
    for id in $ids; do
        window=$(window_of "$id") || { status=1; continue; }
        [[ -n $window ]] || continue
        if ! path=$(write_report "$id" "${window%%|*}" "${window#*|}" "$grace" "$REPORT_FORMAT" false); then
            log "$id | no se pudo escribir el reporte, se reintenta en la siguiente vuelta"
            status=1
            continue
        fi
        q -v id="$id" <<'SQL' || { status=1; continue; }
UPDATE registered_databases SET report_written_at = now(), updated_at = now()
WHERE database_identifier = :'id'
SQL
        log "$id | reporte en ${path#"$REPORTS_DIR"/}"
    done
    return "$status"
}

cmd_scheduler() {
    local interval=${SCHEDULER_INTERVAL_S:-60} grace=${REPORT_GRACE_S:-300}
    local heartbeat=${HEARTBEAT_FILE:-/tmp/heartbeat}
    REPORT_FORMAT=${REPORT_FORMAT:-md}
    [[ $interval =~ ^[0-9]+$ && $interval -gt 0 ]] || die "SCHEDULER_INTERVAL_S no valido: $interval"
    [[ $grace =~ ^[0-9]+$ ]] || die "REPORT_GRACE_S no valido: $grace"
    [[ $REPORT_FORMAT == md || $REPORT_FORMAT == csv || $REPORT_FORMAT == json ]] \
        || die "REPORT_FORMAT no soportado: $REPORT_FORMAT (md, csv o json)"

    # bash como PID 1 no termina con SIGTERM por defecto; el sleep va en
    # segundo plano para que docker stop no espere a que acabe.
    trap 'log "detenido"; exit 0' TERM INT
    log "iniciado | cada ${interval}s | reporte ${grace}s despues del fin | formato $REPORT_FORMAT"
    while :; do
        if scheduler_tick "$grace"; then
            touch "$heartbeat"
        else
            log "vuelta con errores"
        fi
        sleep "$interval" &
        wait $! || true
    done
}

# --- Menu -------------------------------------------------------------------

ask_id() {
    read -r -e -p "Id de la base (ver la lista): " menu_id
}

menu() {
    local option menu_id from until format since
    while :; do
        cat <<'EOF'

QueryLens
  1) Registrar una base y programar su recoleccion
  2) Probar una conexion
  3) Listar bases
  4) Programar ventana de recoleccion
  5) Quitar ventana (monitoreo continuo)
  6) Pausar monitoreo
  7) Reanudar monitoreo
  8) Generar reporte de hallazgos
  9) Salud del pipeline
  0) Salir
EOF
        read -r -p "Opcion: " option || exit 0
        echo
        # Cada opcion en un subshell: un error (die) vuelve al menu.
        case $option in
            1) (cmd_add) || true ;;
            2) (cmd_test) || true ;;
            3) (cmd_list) || true ;;
            4)
                (cmd_list) || true
                ask_id
                read -r -e -p "Inicio (vacio = ahora): " from
                read -r -e -p "Fin: " until
                (cmd_schedule "$menu_id" --from "${from:-now}" --until "$until") || true
                ;;
            5) (cmd_list) || true; ask_id; (cmd_unschedule "$menu_id") || true ;;
            6) (cmd_list) || true; ask_id; (set_monitoring "$menu_id" false) || true ;;
            7) (cmd_list) || true; ask_id; (set_monitoring "$menu_id" true) || true ;;
            8)
                (cmd_list) || true
                ask_id
                read -r -e -p "Ultimos (p. ej. 7d; vacio = la ventana programada o 7d): " since
                read -r -e -p "Formato [md/csv/json] (md): " format
                (cmd_report "$menu_id" ${since:+--since "$since"} --format "${format:-md}") || true
                ;;
            9) (cmd_list) || true; ask_id; (cmd_health "$menu_id") || true ;;
            0 | q | salir) exit 0 ;;
            *) echo "Opcion no valida: $option" ;;
        esac
    done
}

main() {
    local command=${1:-}
    shift || true
    case $command in
        "") menu ;;
        add) cmd_add ;;
        test) cmd_test ;;
        list) cmd_list ;;
        schedule) cmd_schedule "$@" ;;
        unschedule) cmd_unschedule "$@" ;;
        pause) set_monitoring "${1:-}" false ;;
        resume) set_monitoring "${1:-}" true ;;
        report) cmd_report "$@" ;;
        health) cmd_health "$@" ;;
        scheduler) cmd_scheduler ;;
        help | -h | --help) usage ;;
        *) usage >&2; exit 2 ;;
    esac
}

main "$@"
