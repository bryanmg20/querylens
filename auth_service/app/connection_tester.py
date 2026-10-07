import time

import psycopg2
import pymysql

from app.config import settings
from app.schemas import ConnectionParams, TestConnectionResponse


def test_connection(params: ConnectionParams) -> TestConnectionResponse:
    start = time.perf_counter()
    try:
        if params.engine == "postgresql":
            conn = psycopg2.connect(
                host=params.host,
                port=params.port,
                dbname=params.database_name,
                user=params.username,
                password=params.password,
                connect_timeout=settings.connection_test_timeout_seconds,
            )
        else:
            conn = pymysql.connect(
                host=params.host,
                port=params.port,
                database=params.database_name,
                user=params.username,
                password=params.password,
                connect_timeout=settings.connection_test_timeout_seconds,
            )
        conn.close()
    except (psycopg2.OperationalError, pymysql.MySQLError) as exc:
        return TestConnectionResponse(success=False, message=str(exc).strip())

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return TestConnectionResponse(
        success=True,
        message="Conexión exitosa",
        latency_ms=latency_ms,
    )
