from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.session import async_session_factory
from app.models import StorageObject, StorageObjectStatus
from app.storage.client import storage_client
from simulator.clickhouse import ClickHouseClient

CATALOG_TABLES = (
    "users",
    "artists",
    "albums",
    "tracks",
    "track_artists",
    "storage_objects",
    "media_assets",
)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    postgres: int
    clickhouse: int | None
    storage: int | None = None

    @property
    def ok(self) -> bool:
        values = [v for v in (self.clickhouse, self.storage) if v is not None]
        return all(v == self.postgres for v in values)


async def _postgres_counts() -> dict[str, int]:
    async with async_session_factory() as session:
        counts = {
            table: (
                await session.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
            for table in CATALOG_TABLES
        }
        objects, size = (
            await session.execute(
                select(
                    func.count(), func.coalesce(func.sum(StorageObject.size_bytes), 0)
                ).where(StorageObject.status == StorageObjectStatus.AVAILABLE)
            )
        ).one()
    counts["available_objects"] = objects
    counts["available_bytes"] = int(size)
    return counts


async def _clickhouse_counts(ch: ClickHouseClient) -> dict[str, int]:
    union = " UNION ALL ".join(
        f"SELECT '{table}' AS name, count() AS value FROM v_{table}"
        for table in CATALOG_TABLES
    )
    rows = await ch.query(union)
    return {row["name"]: int(row["value"]) for row in rows}


def _storage_totals() -> tuple[int, int]:
    count = size = 0
    for item in storage_client.list_objects(settings.storage_bucket, recursive=True):
        count += 1
        size += item.size or 0
    return count, size


async def collect_checks(ch: ClickHouseClient) -> list[Check]:
    postgres, clickhouse, (objects, size) = await asyncio.gather(
        _postgres_counts(), _clickhouse_counts(ch), asyncio.to_thread(_storage_totals)
    )
    checks = [
        Check(f"{table} rows", postgres[table], clickhouse.get(table, 0))
        for table in CATALOG_TABLES
    ]
    checks.append(Check("RustFS objects", postgres["available_objects"], None, objects))
    checks.append(Check("RustFS bytes", postgres["available_bytes"], None, size))
    return checks


async def verify(
    ch: ClickHouseClient, wait_seconds: float = 20.0
) -> tuple[list[Check], dict[str, object]]:
    deadline = time.monotonic() + wait_seconds
    checks = await collect_checks(ch)
    while not all(c.ok for c in checks) and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        checks = await collect_checks(ch)
    pipeline = (
        await ch.query(
            """
            SELECT
                (SELECT count() FROM cdc_changelog) AS cdc_changes,
                (SELECT quantileExact(0.95)(dateDiff('millisecond', source_ts, ingested_at))
                   FROM cdc_changelog WHERE op != 'r' AND ingested_at > now() - INTERVAL 1 HOUR) AS cdc_lag_p95_ms,
                (SELECT count() FROM fact_api_requests) AS api_requests,
                (SELECT count() FROM fact_app_events) AS app_events,
                (SELECT count() FROM ingest_errors) AS ingest_errors
            """
        )
    )[0]
    return checks, pipeline
