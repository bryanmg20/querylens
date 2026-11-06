from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion del Auth Service, cargada desde variables de entorno."""

    db_host: str = "localhost"
    db_port: int = 5432
    querylens_db: str = "ql_demo"
    querylens_user: str = "ql_user"
    querylens_password: str = "ql_pass"

    auth_encryption_key: str
    # Clave compartida con la API REST: el Auth Service firma el access token
    # (HS256) con ella y la API REST la usa para verificarlo. Debe ser la misma
    # en ambos servicios.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:3000"
    connection_test_timeout_seconds: int = 5
    # Cada cuántos segundos se revisa si las bases registradas están disponibles.
    health_check_interval_seconds: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def querylens_database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.querylens_user}:{self.querylens_password}"
            f"@{self.db_host}:{self.db_port}/{self.querylens_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
