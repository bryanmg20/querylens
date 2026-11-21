// Contenido de la guía "Preparar la base". La fuente de verdad es PREPARAR_BASE.md (raíz del
// repositorio): si cambia allí, cambia aquí. Los scripts se descargan de public/preparar-base/
// (copias de querylens_database/; ver scripts/setup-scripts.mjs).
//
// En los bloques de código, `<base>` se reemplaza por el nombre que el usuario escribió.

export type GuideEngine = "postgresql" | "mysql";

export type Block =
  | { t: "p"; text: string }
  | { t: "code"; code: string }
  | { t: "list"; items: string[]; ordered?: boolean }
  | { t: "table"; head: string[]; rows: string[][] }
  | { t: "note"; tone: "warn" | "info"; text: string }
  | { t: "details"; summary: string; blocks: Block[] }
  | { t: "download"; files: { file: string; label: string }[] };

export interface Section {
  id: string;
  title: string;
  blocks: Block[];
}

export const GUIDE_ENGINES: { id: GuideEngine; label: string }[] = [
  { id: "postgresql", label: "PostgreSQL" },
  { id: "mysql", label: "MySQL" },
];

export const SCRIPTS_PATH = "/preparar-base";

/** Código del problema de salud → sección de la guía que lo resuelve. */
export const HEALTH_CODE_SECTION: Record<string, string> = {
  AUTO_EXPLAIN_LOG_UNAVAILABLE: "instalar",
  EXPLAIN_PERMISSION_DENIED: "planes-explain",
};

export const isGuideEngine = (v: unknown): v is GuideEngine => v === "postgresql" || v === "mysql";

const MANAGED_WARNING =
  "No sirve en servicios administrados (RDS, Aurora, Cloud SQL, Azure): los scripts cambian la configuración del servidor y, en Postgres, leen archivos de log. En Postgres, QueryLens usa el plan genérico como respaldo.";

const BEFORE: Record<GuideEngine, Section> = {
  postgresql: {
    id: "antes-de-empezar",
    title: "Antes de empezar",
    blocks: [
      {
        t: "p",
        text: "Esto lo hace una sola vez el administrador de la base que se va a monitorear. Es un script que activa lo que QueryLens necesita y crea el usuario `querylens_monitor`. Lo que ya está activo no se toca, así que correrlo de nuevo no rompe nada.",
      },
      {
        t: "table",
        head: ["", "PostgreSQL"],
        rows: [
          ["Versión mínima", "15 (17 o superior para todas las detecciones)"],
          ["Quién lo corre", "Un superusuario (`postgres`)"],
          ["Reinicio", "Una vez, si el script lo pide"],
        ],
      },
      { t: "note", tone: "warn", text: MANAGED_WARNING },
    ],
  },
  mysql: {
    id: "antes-de-empezar",
    title: "Antes de empezar",
    blocks: [
      {
        t: "p",
        text: "Esto lo hace una sola vez el administrador de la base que se va a monitorear. Es un script que activa lo que QueryLens necesita y crea el usuario `querylens_monitor`. Lo que ya está activo no se toca, así que correrlo de nuevo no rompe nada.",
      },
      {
        t: "table",
        head: ["", "MySQL"],
        rows: [
          ["Versión mínima", "8.0.18 (8.0.22 o superior para los informes completos de abrazo mortal); 8.4 LTS también"],
          ["Quién lo corre", "Un administrador (`root`)"],
          ["Reinicio", "Una vez, si el script lo pide"],
        ],
      },
      { t: "note", tone: "warn", text: MANAGED_WARNING },
    ],
  },
};

const INSTALL: Record<GuideEngine, Section> = {
  postgresql: {
    id: "instalar",
    title: "Instalar auto_explain y los permisos",
    blocks: [
      {
        t: "list",
        ordered: true,
        items: [
          "Descargar el script y copiarlo al servidor.",
          "Correrlo en la base a monitorear (abajo).",
          "Responder si `querylens_monitor` puede pedir planes de ejecución (`Dar SELECT a querylens_monitor? [s/N]`). Por defecto no; antes de decidir, ver Planes de ejecución. Para no tener la pregunta, agregar `-v ql_grant_select=1` (o `=0`) al comando.",
          "Escribir la contraseña de `querylens_monitor` cuando la pida. Si el usuario ya existía, el script pregunta antes de cambiarla.",
          "Leer las últimas líneas (abajo).",
          "Con varias bases en el mismo servidor, correr el script en cada una. Desde la segunda ya no pide reinicio.",
        ],
      },
      { t: "download", files: [{ file: "setup_target_postgres.sql", label: "Descargar setup_target_postgres.sql" }] },
      { t: "code", code: "psql -U postgres -d <base> -f setup_target_postgres.sql" },
      { t: "p", text: "Con la pregunta de los planes ya respondida:" },
      { t: "code", code: "psql -U postgres -d <base> -v ql_grant_select=0 -f setup_target_postgres.sql" },
      {
        t: "p",
        text: "La última línea dice qué falta:",
      },
      {
        t: "table",
        head: ["Línea", "Qué hacer"],
        rows: [
          ["`Listo: ...`", "No hay más pasos."],
          [
            "`Falta un paso: reinicie Postgres ...`",
            "Reiniciar una vez (`sudo systemctl restart postgresql`). No hace falta volver a correr el script.",
          ],
          [
            "`Falta un paso: estos parametros vienen de la linea de comandos ...`",
            "Postgres arranca con flags `-c` (systemd, Docker) que ganan sobre el script. Cambiar esos flags con los valores de la tabla «Qué cambia el script» y reiniciar.",
          ],
        ],
      },
      { t: "code", code: "sudo systemctl restart postgresql" },
      {
        t: "details",
        summary: "QueryLens está en el mismo servidor y Postgres está fuera de Docker",
        blocks: [
          {
            t: "p",
            text: "El host a registrar es `host.docker.internal`. Hay que permitir esa conexión una vez: en `postgresql.conf`, `listen_addresses` debe incluir la IP del bridge de Docker (normalmente `172.17.0.1`), y en `pg_hba.conf` hay que agregar:",
          },
          { t: "code", code: "host  all  querylens_monitor  172.16.0.0/12  scram-sha-256" },
          { t: "p", text: "Después, `sudo systemctl reload postgresql`, o reiniciar si cambió `listen_addresses`." },
        ],
      },
      {
        t: "details",
        summary: "Qué cambia el script",
        blocks: [
          {
            t: "table",
            head: ["Ajuste", "Valor", "Para qué"],
            rows: [
              ["`shared_preload_libraries`", "agrega `pg_stat_statements`", "Estadísticas de consultas (requiere reinicio)"],
              ["`session_preload_libraries`", "agrega `auto_explain`", "Planes reales de las consultas lentas"],
              ["`pg_stat_statements.max`", "10000 como mínimo", "Que no se descarten consultas (requiere reinicio)"],
              ["`pg_stat_statements.track`", "`all`", "Incluye sentencias dentro de funciones"],
              ["`track_activity_query_size`", "4096 como mínimo", "Texto completo de las consultas en curso (requiere reinicio)"],
              ["`track_counts`", "`on`", "Uso de tablas e índices"],
              [
                "`auto_explain.log_min_duration` / `log_format`",
                "`50ms` / `json`",
                "Registra el plan de lo que tarda 50 ms o más, sin volver a ejecutar nada",
              ],
              [
                "`auto_explain.log_analyze` / `log_timing`",
                "`on` / `off`",
                "El plan registrado trae las filas que devolvió esa ejecución, sin medir tiempos por paso (que es lo caro). QueryLens no ejecuta nada",
              ],
              ["`logging_collector`", "`on`", "Log en archivos (requiere reinicio si estaba apagado)"],
              ["`log_destination`", "agrega `jsonlog`", "Log JSON que QueryLens lee"],
              [
                "`log_filename` / `log_truncate_on_rotation` / `log_rotation_age`",
                "`postgresql-%a.log` / `on` / `1d`",
                "Solo si el servidor no escribía archivos de log: un archivo por día de la semana, que se reutiliza",
              ],
              [
                "`log_error_verbosity` / `log_min_messages`",
                "`default` / `warning`",
                "Solo si estaban en `terse` o más alto que `error`: los abrazos mortales necesitan el detalle",
              ],
              [
                "`log_lock_waits`",
                "`on`",
                "La fila que espera cada sesión de un abrazo mortal cuando la aplicación manda los valores aparte (`$1`)",
              ],
            ],
          },
          {
            t: "p",
            text: "Las listas (`*_preload_libraries`, `log_destination`) se completan; lo que ya tenían se conserva. Por base, el script crea la extensión `pg_stat_statements` y el esquema `querylens` con las funciones `log_files()` y `read_log()` (solo leen archivos `.json` del directorio de logs). En la tabla `querylens.setup_changes` queda anotado cada cambio con el valor de antes, para poder deshacerlo.",
          },
        ],
      },
    ],
  },
  mysql: {
    id: "instalar",
    title: "Preparar MySQL y los permisos",
    blocks: [
      {
        t: "list",
        ordered: true,
        items: [
          "Descargar el script y copiarlo al servidor.",
          "Correrlo con el nombre de la base de la aplicación (abajo). Así `querylens_monitor` no puede pedir planes de ejecución: el script no puede preguntarlo porque `mysql` lo lee por la entrada.",
          "La primera vez, la salida muestra la contraseña generada para `querylens_monitor` (columna `generated password`). Anotarla: es la que se usa al registrar la base. Las siguientes corridas no la cambian.",
          "Leer las últimas líneas (abajo).",
          "Con varias bases en el mismo servidor, correr el script con cada una. Desde la segunda ya no pide reinicio. Los planes se autorizan por base.",
        ],
      },
      { t: "download", files: [{ file: "setup_target_mysql.sql", label: "Descargar setup_target_mysql.sql" }] },
      { t: "code", code: "mysql -u root -p <base> < setup_target_mysql.sql" },
      {
        t: "p",
        text: "Para dar planes de ejecución (ver Planes de ejecución antes de decidir), correrlo así, desde la carpeta del script:",
      },
      { t: "code", code: 'mysql -u root -p <base> -e "SET @ql_grant_select = 1; source setup_target_mysql.sql"' },
      { t: "p", text: "Si hace falta otra contraseña para `querylens_monitor`:" },
      { t: "code", code: "ALTER USER 'querylens_monitor'@'%' IDENTIFIED BY RANDOM PASSWORD;" },
      { t: "p", text: "La salida trae `planes_de_ejecucion` (si están autorizados) y una última línea:" },
      {
        t: "table",
        head: ["Línea", "Qué hacer"],
        rows: [
          ["`Listo: ...`", "No hay más pasos."],
          [
            "`Falta un paso: reinicie MySQL ...`",
            "Reiniciar una vez (`sudo systemctl restart mysql`). No hace falta volver a correr el script.",
          ],
        ],
      },
      { t: "code", code: "sudo systemctl restart mysql" },
      {
        t: "details",
        summary: "QueryLens está en el mismo servidor y MySQL está fuera de Docker",
        blocks: [
          {
            t: "p",
            text: "El host a registrar es `host.docker.internal`, y `bind-address` (en `my.cnf`) debe aceptar conexiones desde la red de Docker.",
          },
        ],
      },
      {
        t: "details",
        summary: "Qué cambia el script",
        blocks: [
          {
            t: "table",
            head: ["Ajuste", "Valor", "Para qué"],
            rows: [
              [
                "`performance_schema`",
                "`ON`",
                "Estadísticas de consultas (viene activo por defecto; requiere reinicio si estaba apagado)",
              ],
              [
                "`performance_schema_max_sql_text_length`",
                "4096 como mínimo",
                "Texto completo de las consultas, para obtener su plan (requiere reinicio)",
              ],
              [
                "`performance_schema_events_statements_history_size`",
                "100 como mínimo",
                "Últimas sentencias de cada conexión: QueryLens explica una ejecución que siga ahí y compara su estimación con las filas que devolvió (con el valor por defecto, 10, las consultas lentas casi nunca siguen ahí). Requiere reinicio",
              ],
              [
                "`innodb_print_all_deadlocks` / `log_error_verbosity`",
                "`ON` / `3`",
                "Cada abrazo mortal llega al log de errores",
              ],
              [
                "Consumers `statements_digest`, `events_statements_current`, `events_statements_history`, `global_instrumentation`, `thread_instrumentation` e instrumentos `statement/%`",
                "activos",
                "Vienen activos por defecto; el script los reactiva si alguien los apagó",
              ],
            ],
          },
          {
            t: "p",
            text: "La configuración queda con `SET PERSIST` en `mysqld-auto.cnf`, que gana sobre `my.cnf`. El valor de antes de cada variable queda anotado en la tabla `querylens.setup_changes` (una base `querylens` que solo usa el script), para poder deshacerlo. Los consumers no se persisten: si `my.cnf` los desactiva, hay que quitar esa línea.",
          },
        ],
      },
    ],
  },
};

const PERMISSIONS: Record<GuideEngine, Section> = {
  postgresql: {
    id: "permisos",
    title: "Qué permisos recibe querylens_monitor",
    blocks: [
      {
        t: "table",
        head: ["Permiso", "Para qué"],
        rows: [
          ["`pg_monitor`", "Leer estadísticas del servidor. No da acceso a los datos de las tablas."],
          ["`CONNECT` a la base", "Poder conectarse a la base a monitorear."],
          [
            "`EXECUTE` sobre `querylens.log_files()` y `querylens.read_log()`",
            "Leer el log JSON donde `auto_explain` deja los planes y donde caen los abrazos mortales. Solo leen archivos `.json` del directorio de logs.",
          ],
        ],
      },
      {
        t: "p",
        text: "QueryLens no lee los datos de tus tablas, salvo que autorices el `SELECT` opcional de Planes de ejecución.",
      },
    ],
  },
  mysql: {
    id: "permisos",
    title: "Qué permisos recibe querylens_monitor",
    blocks: [
      {
        t: "table",
        head: ["Permiso", "Para qué"],
        rows: [
          ["`SELECT` sobre `performance_schema`", "Estadísticas de consultas y de bloqueos."],
          ["`PROCESS`", "Ver las consultas en curso, los bloqueos y los abrazos mortales."],
          ["`SELECT` sobre `mysql.innodb_index_stats`", "El tamaño de cada índice."],
          [
            "`SHOW VIEW` sobre la base",
            "Conectarse a la base y ver sus tablas e índices, sin leer datos.",
          ],
        ],
      },
      {
        t: "p",
        text: "QueryLens no lee los datos de tus tablas, salvo que autorices el `SELECT` opcional de Planes de ejecución.",
      },
    ],
  },
};

const EXPLAIN: Record<GuideEngine, Section> = {
  postgresql: {
    id: "planes-explain",
    title: "Opcional: planes con EXPLAIN",
    blocks: [
      {
        t: "p",
        text: "Para pedir el plan de una consulta (`EXPLAIN`), `querylens_monitor` necesita `SELECT` sobre las tablas, y con `SELECT` podría leer los datos. Es decisión del dueño de la base: el script la pide y por defecto no da el permiso.",
      },
      {
        t: "note",
        tone: "info",
        text: "Sin `SELECT`, QueryLens ve las consultas, sus tiempos, los bloqueos y los índices, pero los antipatrones que leen el plan (AP-02, AP-03, AP-04 y AP-05) solo ven las consultas que `auto_explain` registró en el log, las de 50 ms o más. Es normal ver `EXPLAIN_PERMISSION_DENIED` en la salud de la extracción. El resto (N+1, escritura a disco, cardinalidad mal estimada, degradación y abrazos mortales) funciona igual.",
      },
      {
        t: "p",
        text: "Si decides darlo, el script lo hace por ti (`USAGE` y `SELECT` sobre las tablas de cada schema de la base, menos los del sistema y `querylens`). Equivale a, por cada schema:",
      },
      {
        t: "code",
        code: "GRANT USAGE ON SCHEMA <schema> TO querylens_monitor;\nGRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO querylens_monitor;\nALTER DEFAULT PRIVILEGES FOR ROLE <dueño> IN SCHEMA <schema> GRANT SELECT ON TABLES TO querylens_monitor;",
      },
      {
        t: "p",
        text: "`ALTER DEFAULT PRIVILEGES` cubre las tablas que creen el dueño del schema y los dueños de las tablas que ya existían. Si después otro rol crea tablas, hay que correr el script de nuevo. El script de desinstalación quita este permiso con el resto.",
      },
    ],
  },
  mysql: {
    id: "planes-explain",
    title: "Opcional: planes con EXPLAIN",
    blocks: [
      {
        t: "p",
        text: "Para pedir el plan de una consulta (`EXPLAIN`), `querylens_monitor` necesita `SELECT` sobre las tablas, y con `SELECT` podría leer los datos. Es decisión del dueño de la base: por defecto no se da el permiso.",
      },
      {
        t: "note",
        tone: "info",
        text: "Sin `SELECT`, QueryLens ve las consultas, sus tiempos, los bloqueos y los índices, pero los antipatrones que leen el plan (AP-02, AP-03, AP-04 y AP-05) no ven todas las consultas: MySQL no tiene `auto_explain`, así que no aparecen. Es normal ver `EXPLAIN_PERMISSION_DENIED` en la salud de la extracción. El resto (N+1, escritura a disco, degradación y abrazos mortales) funciona igual.",
      },
      {
        t: "p",
        text: "Para darlo, correr el script con `@ql_grant_select = 1` (ver Preparar MySQL), o a mano:",
      },
      { t: "code", code: "GRANT SELECT ON `<base>`.* TO 'querylens_monitor'@'%';" },
      { t: "p", text: "Lo ya dado se conserva si se corre de nuevo. El script de desinstalación lo quita con el resto." },
    ],
  },
};

const VERIFY: Section = {
  id: "comprobar",
  title: "Comprobar",
  blocks: [
    {
      t: "p",
      text: "Después de registrar la base, el panel de salud de Diagnósticos muestra lo que le falta a la base, con la solución. Si no autorizaste los planes, es normal ver `EXPLAIN_PERMISSION_DENIED`. Cualquier otro problema de configuración indica que falta un paso de esta guía.",
    },
  ],
};

const TEARDOWN: Record<GuideEngine, Section> = {
  postgresql: {
    id: "desinstalar",
    title: "Quitar QueryLens de la base",
    blocks: [
      {
        t: "p",
        text: "El script de desinstalación borra el usuario `querylens_monitor` con todos sus permisos (también los `SELECT` opcionales), quita `auto_explain` y el log JSON, y devuelve cada ajuste al valor que tenía antes de QueryLens. Un ajuste que alguien cambió después de instalar no se toca. Se puede correr más de una vez.",
      },
      {
        t: "download",
        files: [{ file: "teardown_target_postgres.sql", label: "Descargar teardown_target_postgres.sql" }],
      },
      { t: "code", code: "psql -U postgres -d <base> -f teardown_target_postgres.sql" },
      {
        t: "list",
        items: [
          "`Falta un paso: corra este script tambien en estas bases ...`: QueryLens sigue instalado en esas bases, que siguen funcionando. El rol se borra al desinstalar la última.",
          "`Reinicie Postgres para terminar ...`: el reinicio quita `pg_stat_statements` de la memoria y, si el script de instalación lo había encendido, apaga `logging_collector`. Hasta entonces nada se rompe.",
          "`Listo: ...`: no hay más pasos.",
        ],
      },
      {
        t: "p",
        text: "Correrlo en cada base donde se instaló. La configuración del servidor se revierte al correrlo en la primera base donde se instaló, que es la que tiene el registro. Los archivos de log que ya se escribieron quedan en el directorio `log/` de Postgres; se pueden borrar a mano.",
      },
    ],
  },
  mysql: {
    id: "desinstalar",
    title: "Quitar QueryLens de la base",
    blocks: [
      {
        t: "p",
        text: "El script de desinstalación borra el usuario `querylens_monitor` con todos sus permisos (también los `SELECT` opcionales) y devuelve cada ajuste al valor que tenía antes de QueryLens. Un ajuste que alguien cambió después de instalar no se toca. Se puede correr más de una vez.",
      },
      {
        t: "download",
        files: [{ file: "teardown_target_mysql.sql", label: "Descargar teardown_target_mysql.sql" }],
      },
      { t: "p", text: "Correrlo una vez por servidor (no hace falta la base):" },
      { t: "code", code: "mysql -u root -p < teardown_target_mysql.sql" },
      {
        t: "p",
        text: "El final dice `Listo: ...`. Si además dice que algunos valores vuelven a como estaban en el próximo reinicio, no es urgente: mientras tanto el servidor sigue con el texto de consultas ampliado.",
      },
    ],
  },
};

/** Las secciones de la guía para un motor, en orden. */
export function guideSections(engine: GuideEngine): Section[] {
  return [BEFORE[engine], INSTALL[engine], PERMISSIONS[engine], EXPLAIN[engine], VERIFY, TEARDOWN[engine]];
}

/** La sección que explica un problema de salud, si hay una. */
export function sectionForHealthCode(code: string): string | null {
  return HEALTH_CODE_SECTION[code] ?? null;
}

/** El enlace a la guía: motor, sección y, si ya se conoce, el nombre de la base. */
export function guideHref(opts: { engine?: string | null; base?: string | null; section?: string | null }): string {
  const params = new URLSearchParams();
  if (isGuideEngine(opts.engine)) params.set("motor", opts.engine);
  if (opts.base?.trim()) params.set("base", opts.base.trim());
  const query = params.toString();
  return `/preparar-base${query ? `?${query}` : ""}${opts.section ? `#${opts.section}` : ""}`;
}
