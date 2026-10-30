"""Remove leftover test data from PostgreSQL and RustFS.

Run from the project root:

    uv run python scripts/cleanup_test_data.py

Deletes every S3 object registered in ``storage_objects`` and empties all
catalog tables. Use it after aborted runs of ``e2e_check.py`` or to reset
local test data.
"""

import asyncio
import sys
from pathlib import Path

# Make the project package importable regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import async_session_factory, engine
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
from app.storage.service import StorageService


async def main() -> None:
    storage = StorageService(storage_client, settings.storage_bucket)

    async with async_session_factory() as session:
        objects = (await session.execute(select(StorageObject))).scalars().all()
        for obj in objects:
            await storage.delete(obj.object_key)
            print("removed object:", obj.object_key)

        for model in (
            MediaAsset,
            StorageObject,
            TrackArtist,
            Track,
            Album,
            Artist,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()
        print("db rows removed")

    await engine.dispose()


asyncio.run(main())
