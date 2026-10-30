from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from minio.deleteobjects import DeleteObject
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import async_session_factory
from app.models import (
    Album,
    Artist,
    MediaAsset,
    StorageObject,
    Track,
    TrackArtist,
    User,
)
from app.storage.client import storage_client
from simulator.clickhouse import ClickHouseClient
from simulator.verify import CATALOG_TABLES

POSTGRES_WIPE_ORDER = (
    MediaAsset,
    StorageObject,
    TrackArtist,
    Track,
    Album,
    Artist,
    User,
)
CLICKHOUSE_TABLES = (
    "cdc_changelog",
    "ingest_errors",
    *(f"dim_{table}" for table in CATALOG_TABLES),
    "fact_api_requests",
    "fact_app_events",
    "agg_api_minute",
    "agg_track_daily",
)


@dataclass(frozen=True, slots=True)
class WipeReport:
    storage_objects: int
    postgres_rows: int
    clickhouse_tables: int
    cdc_drained: bool


def _purge_bucket() -> int:
    names = [
        DeleteObject(item.object_name)
        for item in storage_client.list_objects(settings.storage_bucket, recursive=True)
    ]
    errors = list(storage_client.remove_objects(settings.storage_bucket, names))
    if errors:
        raise RuntimeError(f"failed to delete {len(errors)} objects: {errors[0]}")
    return len(names)


async def _wipe_postgres() -> int:
    removed = 0
    async with async_session_factory() as session:
        for model in POSTGRES_WIPE_ORDER:
            removed += (await session.execute(delete(model))).rowcount or 0
        await session.commit()
    return removed


async def _wait_for_cdc(ch: ClickHouseClient, timeout: float) -> bool:
    union = " UNION ALL ".join(
        f"SELECT count() AS c FROM v_{table}" for table in CATALOG_TABLES
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await ch.query(f"SELECT sum(c) AS remaining FROM ({union})")
        if int(rows[0]["remaining"]) == 0:
            return True
        await asyncio.sleep(1.0)
    return False


async def wipe_all(ch: ClickHouseClient, cdc_timeout: float = 30.0) -> WipeReport:
    objects = await asyncio.to_thread(_purge_bucket)
    rows = await _wipe_postgres()
    drained = await _wait_for_cdc(ch, cdc_timeout)
    for table in CLICKHOUSE_TABLES:
        await ch.query(f"TRUNCATE TABLE IF EXISTS {table}")
    return WipeReport(objects, rows, len(CLICKHOUSE_TABLES), drained)
