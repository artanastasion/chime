"""Shared fixtures for API integration tests.

Each test boots an isolated HTTP client bound to a fresh NullPool
engine and overrides the session dependency, so the app's default
engine pool is never shared between asyncio.run() loops.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_session
from app.core.config import settings
from app.main import app
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


def make_storage() -> StorageService:
    return StorageService(storage_client, settings.storage_bucket)


async def wipe_all() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = make_storage()
    async with factory() as session:
        for obj in (await session.execute(select(StorageObject))).scalars():
            try:
                await storage.delete(obj.object_key)
            except Exception:
                pass  # object may already be gone
        for model in (MediaAsset, StorageObject, TrackArtist, Track, Album, Artist, User):
            await session.execute(delete(model))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_state():
    asyncio.run(wipe_all())
    yield
    asyncio.run(wipe_all())


class Api:
    """Isolated test client: fresh engine + overridden session dep."""

    def __init__(self) -> None:
        self.engine = create_async_engine(
            settings.database_url, poolclass=NullPool
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    def run(self, scenario: Callable[[AsyncClient], Awaitable[object]]) -> object:
        async def wrapper() -> object:
            async def override_session():
                async with self.factory() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    return await scenario(client)
            finally:
                app.dependency_overrides.pop(get_session, None)
                await self.engine.dispose()

        return asyncio.run(wrapper())

    async def seed_track(self) -> dict[str, uuid.UUID]:
        """Create artist + album + track directly through the session."""
        async with self.factory() as session:
            artist = Artist(name=f"seed-{uuid.uuid4().hex[:8]}")
            album = Album(title="Seed Album")
            track = Track(
                title="Seed Track", duration_seconds=100, album=album
            )
            session.add_all([artist, album, track])
            await session.commit()
            return {
                "artist_id": artist.artist_id,
                "album_id": album.album_id,
                "track_id": track.track_id,
            }
