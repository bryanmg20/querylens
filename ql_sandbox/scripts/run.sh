#!/bin/bash
# ============================================================
# QUERYLENS — sysbench load generator
#
# PURPOSE
#   Populates and stress-tests both PostgreSQL and MySQL with
#   a realistic OLTP workload so that all monitoring views
#   (pg_stat_statements, performance_schema, pg_locks, etc.)
#   have meaningful data to query.
#
# HOW IT WORKS
#   sysbench runs in two phases for each database:
#     1. prepare — creates tables and inserts initial rows.
#     2. run     — fires concurrent transactions for DURATION
#                  seconds using THREADS parallel connections.
#
#   The oltp_read_write profile mixes the following operations
#   in realistic proportions per transaction:
#     - 10 point SELECTs   (lookup by primary key)
#     -  1 range SELECT    (scan a small range of rows)
#     -  1 ORDER BY SELECT
#     -  1 DISTINCT SELECT
#     -  1 INSERT
#     -  1 UPDATE on indexed column
#     -  1 UPDATE on non-indexed column
#     -  1 DELETE
#
# USAGE
#   This script is executed automatically by the sysbench
#   container when you run: docker compose up
#
#   To inject load again without recreating the containers:
#     docker compose start sysbench
#
# TUNING
#   Adjust the variables below to control data volume and load
#   intensity. Raise TABLE_SIZE for more rows, raise THREADS
#   to simulate more concurrent users.
# ============================================================

TABLES=10        # number of tables sysbench creates per database
TABLE_SIZE=10000 # number of rows per table
THREADS=4        # number of parallel connections during the run phase
DURATION=120     # how many seconds the run phase lasts (2 minutes)

echo ""
echo "====================================="
echo " QUERYLENS sandbox — loading data    "
echo "====================================="

# -----------------------------------------------
# POSTGRESQL
# -----------------------------------------------
echo ""
echo ">>> [PostgreSQL] Cleaning up previous data..."
sysbench oltp_read_write \
  --db-driver=pgsql \
  --pgsql-host=postgres \
  --pgsql-port=5432 \
  --pgsql-user=ql_user \
  --pgsql-password=ql_pass \
  --pgsql-db=ql_demo \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  cleanup 2>/dev/null || true

echo ""
echo ">>> [PostgreSQL] Creating tables and data..."
sysbench oltp_read_write \
  --db-driver=pgsql \
  --pgsql-host=postgres \
  --pgsql-port=5432 \
  --pgsql-user=ql_user \
  --pgsql-password=ql_pass \
  --pgsql-db=ql_demo \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  prepare

echo ""
echo ">>> [PostgreSQL] Injecting load ($DURATION seconds)..."
sysbench oltp_read_write \
  --db-driver=pgsql \
  --pgsql-host=postgres \
  --pgsql-port=5432 \
  --pgsql-user=ql_user \
  --pgsql-password=ql_pass \
  --pgsql-db=ql_demo \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  --threads=$THREADS \
  --time=$DURATION \
  --report-interval=10 \
  run

echo ""
echo ">>> [PostgreSQL] Done."

# -----------------------------------------------
# MYSQL
# -----------------------------------------------
echo ""
echo ">>> [MySQL] Cleaning up previous data..."
sysbench oltp_read_write \
  --db-driver=mysql \
  --mysql-host=mysql \
  --mysql-port=3306 \
  --mysql-user=app_user \
  --mysql-password=app_pass \
  --mysql-db=ql_demo \
  --db-ps-mode=disable \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  cleanup 2>/dev/null || true

echo ""
echo ">>> [MySQL] Creating tables and data..."
sysbench oltp_read_write \
  --db-driver=mysql \
  --mysql-host=mysql \
  --mysql-port=3306 \
  --mysql-user=app_user \
  --mysql-password=app_pass \
  --mysql-db=ql_demo \
  --db-ps-mode=disable \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  prepare

echo ""
echo ">>> [MySQL] Injecting load ($DURATION seconds)..."
sysbench oltp_read_write \
  --db-driver=mysql \
  --mysql-host=mysql \
  --mysql-port=3306 \
  --mysql-user=app_user \
  --mysql-password=app_pass \
  --mysql-db=ql_demo \
  --db-ps-mode=disable \
  --tables=$TABLES \
  --table-size=$TABLE_SIZE \
  --threads=$THREADS \
  --time=$DURATION \
  --report-interval=10 \
  run

echo ""
echo ">>> [MySQL] Done."
echo ""
echo "====================================="
echo " Load complete. Both databases ready "
echo " for you to explore with your queries."
echo "====================================="