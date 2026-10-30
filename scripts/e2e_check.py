"""End-to-end check of the catalog schema + RustFS media flow.

Run from the project root:

    uv run python scripts/e2e_check.py

The script creates sample catalog rows, uploads five media objects
(audio original/rendition, lyrics, album/track covers), verifies keys,
checksums, downloads, presigned URLs, DB constraints, error compensation
and cleanup. It removes all created rows and objects afterwards, leaving
both PostgreSQL and RustFS empty.
"""

import asyncio
import hashlib
import logging
import sys
import uuid
from pathlib import Path

# Make the project package importable regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import async_session_factory, engine
from app.models import (
    Album,
    AlbumType,
    Artist,
    ArtistRole,
    MediaAsset,
    MediaAssetType,
    StorageObject,
    StorageObjectStatus,
    Track,
    TrackArtist,
    TrackStatus,
)
from app.services.media_assets import MediaService
from app.storage.client import storage_client
from app.storage.service import StorageService

logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    storage = StorageService(storage_client, settings.storage_bucket)
    await storage.ensure_bucket()

    async with async_session_factory() as session:
        artist = Artist(name="E2E Artist", description="check")
        album = Album(title="E2E Album", album_type=AlbumType.EP)
        track = Track(
            title="E2E Track",
            duration_seconds=195,
            track_number=1,
            explicit_content=True,
            status=TrackStatus.DRAFT,
            album=album,
        )
        session.add_all(
            [
                artist,
                album,
                track,
                TrackArtist(
                    track=track, artist=artist, role=ArtistRole.PRIMARY, display_order=1
                ),
                TrackArtist(track=track, artist=artist, role=ArtistRole.PRODUCER),
            ]
        )
        await session.commit()
        # Snapshot ids: attribute access on ORM instances is unsafe after
        # the rollbacks below (rollback expires all instances).
        track_id, album_id, artist_id = track.track_id, album.album_id, artist.artist_id
        print("catalog rows + track_artists (2 roles) ok")

        # duplicate (track, artist, role) must be rejected by unique constraint
        session.add(
            TrackArtist(
                track_id=track_id,
                artist_id=artist_id,
                role=ArtistRole.PRIMARY,
            )
        )
        try:
            await session.commit()
            raise AssertionError("duplicate track_artist accepted")
        except IntegrityError:
            await session.rollback()
            print("uq_track_artist_role enforced")

        svc = MediaService(session, storage)

        original_bytes = b"ID3" + b"\x01" * 2048
        audio = await svc.upload_track_audio(
            track_id,
            original_bytes,
            mime_type="audio/flac",
            original_filename="master.FLAC",
            is_primary=True,
        )
        assert (
            audio.object.object_key
            == f"music/audio/{track_id}/original/source_audio.flac"
        ), audio.object.object_key
        print("audio original:", audio.object.object_key)

        rendition_bytes = b"\xff\xfb" + b"\x02" * 1024
        rendition = await svc.upload_track_audio(
            track_id,
            rendition_bytes,
            mime_type="audio/mpeg",
            audio_format="mp3",
            audio_bitrate=320,
        )
        assert (
            rendition.object.object_key
            == f"music/audio/{track_id}/renditions/mp3_320.mp3"
        )
        print("audio rendition:", rendition.object.object_key)

        lyrics = await svc.upload_lyrics(
            track_id, "la la la\nпроверка юникода", language="ru"
        )
        assert lyrics.object.object_key == f"music/lyrics/{track_id}/original.txt"
        print("lyrics:", lyrics.object.object_key)

        cover_bytes = b"\x89PNG\r\n\x1a\n" + b"cover" * 64
        album_cover = await svc.upload_cover(
            content=cover_bytes,
            mime_type="image/png",
            album_id=album_id,
            original_filename="front.PNG",
        )
        assert (
            album_cover.object.object_key
            == f"music/covers/albums/{album_id}/cover.png"
        )
        track_cover = await svc.upload_cover(
            content=cover_bytes,
            mime_type="image/jpeg",
            track_id=track_id,
        )
        assert (
            track_cover.object.object_key
            == f"music/covers/tracks/{track_id}/cover"
        )
        print("covers:", album_cover.object.object_key, "|", track_cover.object.object_key)

        # Snapshot keys/ids before any rollback below (rollbacks expire
        # all loaded instances and make attribute access unsafe).
        assets = (audio, rendition, lyrics, album_cover, track_cover)
        keys = [a.object.object_key for a in assets]
        asset_ids = [a.asset_id for a in assets]
        primary_object_id = audio.object.object_id

        # storage object metadata
        so = audio.object
        assert so.bucket_name == "music-media"
        assert so.checksum_algorithm == "sha256"
        assert so.checksum == hashlib.sha256(original_bytes).hexdigest()
        assert so.size_bytes == len(original_bytes)
        assert so.status == StorageObjectStatus.AVAILABLE
        print("storage_objects metadata ok (sha256, size, available)")

        # objects really exist in RustFS
        for asset in (audio, rendition, lyrics, album_cover, track_cover):
            assert await storage.exists(asset.object.object_key)
        print("all 5 objects exist in RustFS")

        # download round-trip + presigned url
        assert await svc.download(audio.asset_id) == original_bytes
        assert (await svc.download(lyrics.asset_id)).decode("utf-8").startswith("la la")
        url = await svc.get_presigned_url(audio.asset_id)
        assert "X-Amz-Signature" in url
        print("download + presigned url ok")

        # invalid combinations rejected before upload
        try:
            await svc.upload_lyrics(album_id, "x")  # type: ignore[arg-type]
            raise AssertionError("lyrics-to-album accepted")
        except ValueError as exc:
            print("lyrics-to-album rejected:", exc)
        try:
            await svc.upload_cover(content=b"x", mime_type="image/png")
            raise AssertionError("cover without target accepted")
        except ValueError as exc:
            print("cover without target rejected:", exc)

        # fixed slot cannot be overwritten silently
        try:
            await svc.upload_track_audio(
                track_id,
                b"other",
                mime_type="audio/flac",
                original_filename="master.FLAC",
            )
            raise AssertionError("duplicate slot accepted")
        except ValueError as exc:
            print("duplicate key slot rejected:", exc)

        # DB-level check: asset with both targets is impossible
        session.add(
            MediaAsset(
                track_id=track_id,
                album_id=album_id,
                object_id=primary_object_id,
                asset_type=MediaAssetType.COVER,
            )
        )
        try:
            await session.commit()
            raise AssertionError("both-targets asset accepted")
        except IntegrityError:
            await session.rollback()
            print("ck_media_assets_single_catalog_target enforced")

        # RESTRICT: track deletion blocked while media assets reference it
        t = await session.get(Track, track_id)
        await session.delete(t)
        try:
            await session.commit()
            raise AssertionError("RESTRICT not enforced")
        except IntegrityError:
            await session.rollback()
            print("media FK RESTRICT enforced")

        # delete all five assets: object gone in RustFS, rows gone in PG
        for asset_id in asset_ids:
            await svc.delete_asset(asset_id)
        for key in keys:
            assert not await storage.exists(key)
        left = (
            await session.execute(
                select(StorageObject).where(StorageObject.object_key.in_(keys))
            )
        ).scalars().all()
        assert left == []
        print("delete_asset removed objects and both tables' rows")

        # cleanup catalog rows
        await session.execute(delete(TrackArtist))
        await session.execute(delete(Track))
        await session.execute(delete(Album))
        await session.execute(delete(Artist))
        await session.commit()

        counts = {}
        for model in (TrackArtist, Track, Album, Artist, MediaAsset, StorageObject):
            counts[model.__tablename__] = len(
                (await session.execute(select(model))).scalars().all()
            )
        print("cleanup done, remaining:", counts)
        assert all(v == 0 for v in counts.values())

    await engine.dispose()
    print("E2E V2 OK")


asyncio.run(main())

