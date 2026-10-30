"""Reliability tests for MediaService.

Integration tests against the local PostgreSQL and RustFS started via
``docker compose up -d``. Every test wipes leftover media data before
and after execution, so the suite is self-contained.

Covered areas (per the audit):
* compensation when the DB fails after a successful RustFS upload;
* compensation when the compensating S3 delete itself fails;
* concurrent uploads into the same object key slot;
* one-storage-object-per-asset DB constraint and repeated deletion;
* validation of formats, bitrates, MIME types, metadata and empty content;
* rollback behaviour on commit failures.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models import (
    Album,
    Artist,
    ArtistRole,
    MediaAsset,
    MediaAssetType,
    StorageObject,
    StorageObjectStatus,
    Track,
    TrackArtist,
)
from app.services.media_assets import MediaService
from app.storage.client import storage_client
from app.storage.service import StorageError, StorageService

PAYLOAD_A = b"AUDIO-DATA-A" * 20  # 220 bytes
PAYLOAD_B = b"B!" * 200  # 400 bytes, different length to tell winners apart


def make_storage() -> StorageService:
    return StorageService(storage_client, settings.storage_bucket)


def make_probe() -> StorageService:
    """An unsabotaged StorageService used to verify real S3 state."""
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
        for model in (MediaAsset, StorageObject, TrackArtist, Track, Album, Artist):
            await session.execute(delete(model))
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_media_state():
    asyncio.run(wipe_all())
    yield
    asyncio.run(wipe_all())


class Env:
    """Fresh engine, session and MediaService for one scenario."""

    def __init__(self) -> None:
        self.engine = create_async_engine(settings.database_url, poolclass=NullPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def __aenter__(self) -> "Env":
        self.session = self.factory()
        self.service = MediaService(self.session, make_storage())
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.session.close()
        await self.engine.dispose()

    async def seed_track(self) -> tuple[uuid.UUID, uuid.UUID]:
        artist = Artist(name=f"test-{uuid.uuid4().hex[:8]}")
        album = Album(title="Test Album")
        track = Track(title="Test Track", duration_seconds=100, album=album)
        self.session.add_all(
            [
                artist,
                album,
                track,
                TrackArtist(
                    track=track, artist=artist, role=ArtistRole.PRIMARY
                ),
            ]
        )
        await self.session.commit()
        return track.track_id, album.album_id

    async def counts(self) -> dict[str, int]:
        return {
            model.__tablename__: len(
                (await self.session.execute(select(model))).scalars().all()
            )
            for model in (MediaAsset, StorageObject)
        }


def flaky_commit(session, fail_on: int) -> dict:
    """Make session.commit() raise on the Nth call (counted from now)."""
    original = session.commit
    state = {"n": 0}

    async def _flaky():
        state["n"] += 1
        if state["n"] == fail_on:
            raise RuntimeError("simulated commit failure")
        return await original()

    session.commit = _flaky
    return state


def break_storage_method(service: MediaService, name: str) -> dict:
    """Replace a StorageService method on the service instance so it fails.

    Returns a call counter so tests can verify how often the method was
    actually invoked."""

    async def _broken(*args, **kwargs):
        state["n"] += 1
        raise StorageError(f"simulated {name} failure")

    state = {"n": 0}
    setattr(service._storage, name, _broken)
    return state


def count_storage_calls(service: MediaService, name: str) -> dict:
    """Wrap a StorageService method with a call counter."""
    original = getattr(service._storage, name)
    state = {"n": 0}

    async def _counting(*args, **kwargs):
        state["n"] += 1
        return await original(*args, **kwargs)

    setattr(service._storage, name, _counting)
    return state


# -- 1. compensation for DB failures after a successful upload -------------


def test_register_finalise_failure_compensates():
    """Upload ok, reservation ok, finalisation commit fails ->
    binary removed from RustFS, no storage_objects row left."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            key = f"music/audio/{track_id}/original/source_audio.bin"
            flaky_commit(env.session, fail_on=2)  # 1=reserve, 2=finalise
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await env.service.register_object(
                    content=PAYLOAD_A, object_key=key, mime_type="audio/mpeg"
                )
            assert not await make_probe().exists(key), "binary must be compensated"
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_register_upload_failure_releases_reservation():
    """Reservation ok, RustFS upload fails -> reservation row released."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            key = f"music/audio/{track_id}/original/source_audio.bin"
            uploads = break_storage_method(env.service, "upload")
            with pytest.raises(StorageError):
                await env.service.register_object(
                    content=PAYLOAD_A, object_key=key, mime_type="audio/mpeg"
                )
            assert uploads["n"] == 1, "upload must have been attempted once"
            assert not await make_probe().exists(key)
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_compensation_delete_failure_marks_object_failed():
    """Finalisation fails AND the compensating S3 delete fails ->
    row stays marked 'failed' (discoverable), binary stays in RustFS."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            key = f"music/audio/{track_id}/original/source_audio.bin"
            flaky_commit(env.session, fail_on=2)
            break_storage_method(env.service, "delete")
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await env.service.register_object(
                    content=PAYLOAD_A, object_key=key, mime_type="audio/mpeg"
                )
            row = (
                await env.session.execute(select(StorageObject))
            ).scalars().one()
            assert row.status == StorageObjectStatus.FAILED
            assert await make_probe().exists(key), "binary remains until cleanup"

    asyncio.run(scenario())


def test_attach_failure_compensates():
    """Object registered (available) but the media_assets insert fails ->
    composite upload discards both the binary and the row."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            flaky_commit(env.session, fail_on=3)  # 3=asset commit
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await env.service.upload_track_audio(
                    track_id, PAYLOAD_A, mime_type="audio/mpeg"
                )
            key = f"music/audio/{track_id}/original/source_audio"
            assert not await make_probe().exists(key)
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_attach_failure_compensation_delete_fails():
    """Asset creation fails AND compensation delete fails ->
    storage row marked 'failed', binary remains, no asset."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            flaky_commit(env.session, fail_on=3)
            break_storage_method(env.service, "delete")
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await env.service.upload_track_audio(
                    track_id, PAYLOAD_A, mime_type="audio/mpeg"
                )
            row = (
                await env.session.execute(select(StorageObject))
            ).scalars().one()
            assert row.status == StorageObjectStatus.FAILED
            assert await env.counts() == {"media_assets": 0, "storage_objects": 1}

    asyncio.run(scenario())


# -- 2. concurrency ---------------------------------------------------------


def test_concurrent_upload_same_slot_single_winner():
    """Two parallel uploads into the same key: exactly one wins, the
    loser is rejected before any put_object, the winner's binary stays
    intact, exactly one row/asset pair remains."""

    async def scenario():
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session_a = factory()
        session_b = factory()
        try:
            service_a = MediaService(session_a, make_storage())
            service_b = MediaService(session_b, make_storage())
            track = Track(title="Race", duration_seconds=10)
            session_a.add(track)
            await session_a.commit()

            results = await asyncio.gather(
                service_a.upload_track_audio(
                    track.track_id, PAYLOAD_A, mime_type="audio/mpeg"
                ),
                service_b.upload_track_audio(
                    track.track_id, PAYLOAD_B, mime_type="audio/mpeg"
                ),
                return_exceptions=True,
            )
            winners = [r for r in results if isinstance(r, MediaAsset)]
            losers = [r for r in results if isinstance(r, ValueError)]
            assert len(winners) == 1, f"exactly one winner expected: {results}"
            assert len(losers) == 1
            assert "reserved" in str(losers[0]) or "registered" in str(losers[0])

            key = f"music/audio/{track.track_id}/original/source_audio"
            downloaded = await make_probe().download(key)
            expected = (
                PAYLOAD_A
                if winners[0].object.size_bytes == len(PAYLOAD_A)
                else PAYLOAD_B
            )
            assert downloaded == expected, "winner's binary must be intact"

            assets = len(
                (await session_a.execute(select(MediaAsset))).scalars().all()
            )
            objects = len(
                (await session_a.execute(select(StorageObject))).scalars().all()
            )
            assert (assets, objects) == (1, 1)
        finally:
            await session_a.close()
            await session_b.close()
            await engine.dispose()

    asyncio.run(scenario())


# -- 3. one object per asset / deletion --------------------------------------


def test_object_owned_by_single_asset_db_constraint():
    """A second media asset referencing the same storage object is
    rejected by UNIQUE media_assets.object_id."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            asset = await env.service.upload_track_audio(
                track_id, PAYLOAD_A, mime_type="audio/mpeg"
            )
            env.session.add(
                MediaAsset(
                    track_id=track_id,
                    object_id=asset.object_id,
                    asset_type=MediaAssetType.AUDIO,
                )
            )
            with pytest.raises(IntegrityError):
                await env.session.commit()
            await env.session.rollback()

    asyncio.run(scenario())


def test_delete_asset_twice_raises_lookup_error():
    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            asset = await env.service.upload_track_audio(
                track_id, PAYLOAD_A, mime_type="audio/mpeg"
            )
            key = asset.object.object_key
            await env.service.delete_asset(asset.asset_id)
            assert not await make_probe().exists(key)
            with pytest.raises(LookupError):
                await env.service.delete_asset(asset.asset_id)

    asyncio.run(scenario())


def test_delete_asset_happy_path():
    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            asset = await env.service.upload_track_audio(
                track_id, PAYLOAD_A, mime_type="audio/mpeg"
            )
            key = asset.object.object_key
            await env.service.delete_asset(asset.asset_id)
            assert not await make_probe().exists(key)
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


# -- 4. validation ------------------------------------------------------------


def test_invalid_audio_params_rejected_before_any_upload():
    cases = [
        {"mime_type": "audio/mpeg", "audio_format": "../evil", "audio_bitrate": 128},
        {"mime_type": "audio/mpeg", "audio_format": "mp4", "audio_bitrate": 128},
        {"mime_type": "audio/mpeg", "audio_bitrate": 128},
        {"mime_type": "audio/mpeg", "audio_format": "mp3"},
        {"mime_type": "audio/mpeg", "audio_format": "mp3", "audio_bitrate": 0},
        {"mime_type": "audio/mpeg", "audio_format": "mp3", "audio_bitrate": -5},
        {"mime_type": "audio/mpeg", "audio_format": "mp3", "audio_bitrate": "320"},
        {"mime_type": "image/png"},
    ]

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            uploads = count_storage_calls(env.service, "upload")
            for kwargs in cases:
                with pytest.raises(ValueError):
                    await env.service.upload_track_audio(
                        track_id, PAYLOAD_A, **kwargs
                    )
            assert uploads["n"] == 0, "no upload may happen for invalid input"
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_empty_content_rejected():
    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            with pytest.raises(ValueError, match="empty"):
                await env.service.upload_track_audio(
                    track_id, b"", mime_type="audio/mpeg"
                )
            with pytest.raises(ValueError, match="empty"):
                await env.service.upload_lyrics(track_id, "")
            with pytest.raises(ValueError, match="empty"):
                await env.service.register_object(
                    content=b"",
                    object_key=f"music/audio/{track_id}/original/source_audio",
                    mime_type="audio/mpeg",
                )
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_nonexistent_catalog_entity_rejected():
    async def scenario():
        async with Env() as env:
            with pytest.raises(ValueError, match="does not exist"):
                await env.service.upload_track_audio(
                    uuid.uuid4(), PAYLOAD_A, mime_type="audio/mpeg"
                )
            assert await env.counts() == {"media_assets": 0, "storage_objects": 0}

    asyncio.run(scenario())


def test_asset_metadata_must_match_asset_type():
    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()

            # cover requires an image/* MIME type
            with pytest.raises(ValueError, match="image/"):
                await env.service.upload_cover(
                    content=b"not an image",
                    mime_type="text/plain",
                    track_id=track_id,
                )

            # language is only allowed for lyrics assets
            storage_object = await env.service.register_object(
                content=PAYLOAD_A,
                object_key=f"music/audio/{track_id}/original/source_audio",
                mime_type="audio/mpeg",
            )
            with pytest.raises(ValueError, match="language"):
                await env.service.create_asset(
                    storage_object,
                    MediaAssetType.AUDIO,
                    track_id=track_id,
                    language="ru",
                )

            # audio fields are not allowed for lyrics assets
            text_object = await env.service.register_object(
                content=b"la la",
                object_key=f"music/lyrics/{track_id}/original.txt",
                mime_type="text/plain; charset=utf-8",
            )
            with pytest.raises(ValueError, match="audio"):
                await env.service.create_asset(
                    text_object,
                    MediaAssetType.LYRICS,
                    track_id=track_id,
                    audio_bitrate=128,
                )

            # asset type must match the registered object's MIME type
            with pytest.raises(ValueError, match="audio/"):
                await env.service.create_asset(
                    text_object,
                    MediaAssetType.AUDIO,
                    track_id=track_id,
                )
            await env.session.rollback()

    asyncio.run(scenario())


def test_create_asset_requires_available_object():
    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            asset = await env.service.upload_track_audio(
                track_id, PAYLOAD_A, mime_type="audio/mpeg"
            )
            await env.service.delete_asset(asset.asset_id)  # free the slot
            storage_object = await env.service.register_object(
                content=PAYLOAD_B,
                object_key=f"music/audio/{track_id}/original/source_audio",
                mime_type="audio/mpeg",
            )
            await env.session.execute(
                update(StorageObject)
                .where(StorageObject.object_id == storage_object.object_id)
                .values(status=StorageObjectStatus.PENDING)
            )
            await env.session.commit()
            object_pk = storage_object.object_id  # capture before expiring
            env.session.expire_all()
            stale = await env.session.get(StorageObject, object_pk)
            with pytest.raises(ValueError, match="not available"):
                await env.service.create_asset(
                    stale,
                    MediaAssetType.AUDIO,
                    track_id=track_id,
                )

    asyncio.run(scenario())


# -- 5. transactions -----------------------------------------------------------


def test_commit_failure_rolls_back_and_keeps_session_usable():
    """A failing commit in create_asset rolls the transaction back so
    the same session stays usable afterwards (direct primitive: the
    storage object is intentionally kept, no compensation here)."""

    async def scenario():
        async with Env() as env:
            track_id, _ = await env.seed_track()
            storage_object = await env.service.register_object(
                content=PAYLOAD_A,
                object_key=f"music/audio/{track_id}/original/source_audio",
                mime_type="audio/mpeg",
            )
            flaky_commit(env.session, fail_on=1)  # next commit fails
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                await env.service.create_asset(
                    storage_object,
                    MediaAssetType.AUDIO,
                    track_id=track_id,
                )
            # session must be usable: no aborted transaction left behind
            titles = (await env.session.execute(select(Track.title))).scalars().all()
            assert titles, "session must still be able to read"
            assert await env.counts() == {"media_assets": 0, "storage_objects": 1}

    asyncio.run(scenario())





