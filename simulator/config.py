from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SimulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    simulator_api_url: str = "http://127.0.0.1:8000"
    simulator_http_timeout_seconds: float = 30.0

    clickhouse_host: str = "127.0.0.1"
    clickhouse_http_port: int = 8123
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_db: str = "chime_dwh"

    @property
    def clickhouse_url(self) -> str:
        return f"http://{self.clickhouse_host}:{self.clickhouse_http_port}/"


@lru_cache
def get_simulator_settings() -> SimulatorSettings:
    return SimulatorSettings()
