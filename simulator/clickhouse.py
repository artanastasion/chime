from __future__ import annotations

from typing import Any

import httpx

from simulator.config import SimulatorSettings


class ClickHouseClient:
    def __init__(self, settings: SimulatorSettings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.clickhouse_url,
            auth=(settings.clickhouse_user, settings.clickhouse_password),
            params={"database": settings.clickhouse_db},
            timeout=30.0,
        )

    async def __aenter__(self) -> ClickHouseClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def query(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        response = await self._client.post(
            "",
            params={f"param_{name}": value for name, value in params.items()},
            content=f"{sql} FORMAT JSON",
        )
        if response.status_code != 200:
            raise RuntimeError(f"ClickHouse error: {response.text.strip()}")
        return response.json()["data"]

    async def scalar(self, sql: str, **params: Any) -> Any:
        rows = await self.query(sql, **params)
        return next(iter(rows[0].values())) if rows else None
