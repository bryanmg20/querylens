#!/usr/bin/env bash
# ============================================================
# QUERYLENS — trafico para las variantes de plan con pg_stats
#
# Crea una tabla con una columna sesgada (estado: 95 % entregado, 4 %
# pendiente, 1 % fallido) y corre queries con literales cuyos $n se mapean a
# una columna (=, >, >=; sin LIMIT ni $n en el SELECT), para que el pipeline
# arme plan_variants y plan_stability (stages/plan_variants.py).
#
# Casos:
#   estado = '...'                 -> sensitive (Seq Scan vs Index Scan)
#   estado = '...' AND monto >= N  -> dos $n mapeados
#   cliente_id = N                 -> stable (columna sin valores frecuentes)
#   monto > N                      -> rango (extremos del histograma)
#   tabla sin ANALYZE              -> solo generico + PG_STATS_MISSING
#
# USO (desde el host):
#   docker exec ql_sysbench bash /scripts/postgres/simulate_plan_variants_postgres.sh [REPS]
#   docker exec ql_sysbench bash /scripts/postgres/simulate_plan_variants_postgres.sh cleanup
# REPS = repeticiones de cada query (default 30).
#
# Resetea pg_stat_statements al empezar, igual que battery.sh, para que el
# top-impact del siguiente ciclo sean estas queries.
# ============================================================

set -euo pipefail

export PGPASSWORD=ql_pass

PSQL=(psql --host=postgres --port=5432 --username=ql_user --dbname=ql_demo
      --no-psqlrc --quiet --tuples-only --set=ON_ERROR_STOP=1 --command)

if [[ "${1:-}" == "cleanup" ]]; then
  "${PSQL[@]}" "DROP TABLE IF EXISTS ql_pedidos, ql_pedidos_sin_stats;"
  echo ">>> Tablas de variantes eliminadas."
  exit 0
fi

REPS="${1:-30}"

echo ">>> Creando ql_pedidos (200k filas, estado sesgado) y ql_pedidos_sin_stats..."
"${PSQL[@]}" "
  DROP TABLE IF EXISTS ql_pedidos, ql_pedidos_sin_stats;

  CREATE TABLE ql_pedidos (
    id         serial PRIMARY KEY,
    estado     text   NOT NULL,
    cliente_id int    NOT NULL,
    monto      int    NOT NULL
  );
  INSERT INTO ql_pedidos (estado, cliente_id, monto)
  SELECT CASE WHEN g % 100 < 95 THEN 'entregado'
              WHEN g % 100 < 99 THEN 'pendiente'
              ELSE 'fallido' END,
         g,
         g % 1000
  FROM generate_series(1, 200000) AS g;
  CREATE INDEX ql_pedidos_estado_idx ON ql_pedidos (estado);
  CREATE INDEX ql_pedidos_cliente_idx ON ql_pedidos (cliente_id);
  CREATE INDEX ql_pedidos_monto_idx ON ql_pedidos (monto);
  ANALYZE ql_pedidos;

  -- Sin autovacuum no hay ANALYZE automatico: pg_stats queda vacio.
  CREATE TABLE ql_pedidos_sin_stats (id int, estado text)
    WITH (autovacuum_enabled = off);
  INSERT INTO ql_pedidos_sin_stats SELECT g, 'x' FROM generate_series(1, 5000) AS g;
" >/dev/null

echo ">>> Reseteando pg_stat_statements..."
"${PSQL[@]}" "SELECT pg_stat_statements_reset();" >/dev/null

QUERIES=(
  "SELECT id, monto FROM ql_pedidos WHERE estado = 'fallido';"
  "SELECT count(*) FROM ql_pedidos WHERE estado = 'entregado';"
  "SELECT id FROM ql_pedidos WHERE estado = 'pendiente' AND monto >= 500;"
  "SELECT id, estado FROM ql_pedidos WHERE cliente_id = 4242;"
  "SELECT id FROM ql_pedidos WHERE monto > 900;"
  "SELECT id FROM ql_pedidos_sin_stats WHERE estado = 'x';"
)

echo ">>> Corriendo ${#QUERIES[@]} queries x $REPS..."
for i in $(seq 1 "$REPS"); do
  for q in "${QUERIES[@]}"; do
    "${PSQL[@]}" "$q" >/dev/null
  done
  if (( i % 10 == 0 )); then echo "  rep $i/$REPS"; fi
done

echo ">>> Listo. Corre un ciclo del pipeline (python main_sandbox.py) para ver las variantes."
