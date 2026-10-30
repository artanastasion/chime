from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    rustfs_access_key: str
    rustfs_secret_key: str
    # Endpoint of the S3 API as seen by the application (host:port).
    # Defaults to the host-side Docker mapping; override with RUSTFS_ENDPOINT
    # when the app itself runs inside Docker Compose.
    rustfs_endpoint: str | None = None
    # Kept for the Docker Compose port mapping fallback below.
    rustfs_api_port: int = 9000
    rustfs_secure: bool = False
    rustfs_bucket_media: str = "music-media"

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @property
    def storage_endpoint(self) -> str:
        return self.rustfs_endpoint or f"127.0.0.1:{self.rustfs_api_port}"

    @property
    def storage_bucket(self) -> str:
        return self.rustfs_bucket_media


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()