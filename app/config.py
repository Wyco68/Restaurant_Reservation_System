"""Application configuration.

Every value comes from the environment or .env. No secret is ever written
into source.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- PostgreSQL ---
    postgres_db: str = "tableflow"
    postgres_user: str = "dev_user"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # --- MongoDB ---
    mongo_db: str = "tableflow"
    mongo_user: str = "dev_user"
    mongo_password: str = ""
    mongo_host: str = "localhost"
    mongo_port: int = 27017

    # --- Auth ---
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # --- Application ---
    app_env: str = "development"
    log_level: str = "INFO"

    # --- Read-switch feature flag ---
    # Flipping this is the SWITCH READ step of the Expand-Contract migration.
    # Rollback is a flag flip, not a redeploy.
    read_new_name_fields: bool = False

    @property
    def postgres_dsn(self) -> str:
        """Async DSN used by the application."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_sync_dsn(self) -> str:
        """Sync DSN used by Alembic and the seed script."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def mongo_uri(self) -> str:
        return (
            f"mongodb://{self.mongo_user}:{self.mongo_password}"
            f"@{self.mongo_host}:{self.mongo_port}/?authSource=admin"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
