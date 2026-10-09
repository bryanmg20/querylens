from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuracion de la API REST, cargada desde variables de entorno."""

    db_host: str = "localhost"
    db_port: int = 5432
    querylens_db: str = "ql_demo"
    querylens_user: str = "ql_user"
    querylens_password: str = "ql_pass"

    # Debe ser EXACTAMENTE la misma clave con la que el Auth Service firma el
    # access token (JWT_SECRET_KEY en auth_service).
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    cors_origins: str = "http://localhost:3000"

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
