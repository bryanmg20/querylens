from sqlalchemy import create_engine  # Import the engine creator for DB connections
from sqlalchemy.engine import Engine  # Import the Engine type for type hinting
from sqlalchemy.exc import SQLAlchemyError  # Import base exception for SQLAlchemy errors


def get_connection_postgres() -> Engine:
    """Create a SQLAlchemy engine for the PostgreSQL database.

    Returns:
        sqlalchemy.engine.Engine: SQLAlchemy engine instance.
    """
    try:
        db_host = "localhost"  # PostgreSQL server hostname
        db_port = 5432  # Default PostgreSQL port
        db_name = "ql_demo"  # Target database name
        db_user = "querylens_monitor"  # Database username
        db_password = "monitor_pass"  # Database password

        # Build connection URL using PostgreSQL with psycopg2 driver
        url = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

        # Create the SQLAlchemy engine instance
        engine = create_engine(url)

        return engine  # Return the configured engine

    except SQLAlchemyError as e:
        raise  # Re-raise any SQLAlchemy-specific errors


def get_connection_mysql() -> Engine:
    """Create a SQLAlchemy engine for the MySQL database.

    Returns:
        sqlalchemy.engine.Engine: SQLAlchemy engine instance.
    """
    try:
        db_host = "localhost"  # MySQL server hostname
        db_port = 3307  # MySQL server port (non-default)
        db_name = "ql_demo"  # Target database name
        db_user = "querylens_monitor"
        db_password = "monitor_pass"

        # Build connection URL using MySQL with PyMySQL driver
        url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}"

        engine = create_engine(
            url,
            pool_size=1,  # Number of connections to keep in the pool
            max_overflow=10,  # Extra connections allowed when pool is full
            pool_pre_ping=True,  # Verify connection alive before using
            echo=False  # Disable SQL query logging to console
        )

        return engine  # Return the configured engine

    except SQLAlchemyError as e:
        # Raise a user-friendly error with context
        raise RuntimeError(f"Error connecting to MySQL: {e}")