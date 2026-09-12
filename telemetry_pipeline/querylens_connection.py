import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def get_connection_querylens_db() -> Engine:
    """Create a SQLAlchemy engine for the PostgreSQL database using environment variables."""
    try:
        db_host = os.getenv("DB_HOST") or "localhost"
        db_port = int(os.getenv("DB_PORT") or 5432)
        db_name = os.getenv("QUERYLENS_DB") or "ql_demo"
        db_user = os.getenv("QUERYLENS_USER") or "ql_user"
        db_password = os.getenv("QUERYLENS_PASSWORD") or "ql_pass"

        url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

        engine = create_engine(url)
        return engine

    except SQLAlchemyError as e:
        raise