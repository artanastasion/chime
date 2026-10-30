"""API integration tests: media endpoints (real PostgreSQL + RustFS)."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import update as db_update

import app.api.v1.media as media_api
from app.models import MediaAssetType, StorageObject, StorageObjectStatus
from tests.conftest import Api, make_storage

AUDIO = b"ID3" + b"\x01" * 500
COVER_PNG = b"\x89PNG\r\n\x1a\n" + b"cover" * 40


def _audio_file(data=AUDIO, name="song.mp3", mime="audio/mpeg"):
    return {"file": (name, data, mime)}


def _cover_file(data=COVER_PNG, name="cover.png", mime="image/png"):
    return {"file": (name, data, mime)}


def test_upload_audio_original_and_rendition():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/audio",
            files=_audio_file(name="master.FLAC", mime="audio/flac"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["asset_type"] == "audio"
        assert body["status"] == "available"
        assert body["size_bytes"] == len(AUDIO)
        assert body["checksum"] == hashlib.sha256(AUDIO).hexdigest()
        assert body["mime_type"] == "audio/flac"
        assert body["is_primary"] is False
        assert "object_key" not in body and "bucket" not in body
        assert body["download_url"] and "X-Amz-Signature" in body["download_url"]

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/audio",
            files=_audio_file(data=b"\xff\xfb" + b"\x02" * 100, name="r.mp3"),
            data={"audio_format": "mp3", "audio_bitrate": "320", "is_primary": "true"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["audio_format"] == "mp3"
        assert body["audio_bitrate"] == 320
        assert body["is_primary"] is True

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/audio",
            files=_audio_file(name="master.FLAC", mime="audio/flac"),
        )
        assert resp.status_code == 409

        resp = await client.get(f"/api/v1/tracks/{seed['track_id']}/media")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    api.run(scenario)


def test_upload_audio_validation():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        base = f"/api/v1/tracks/{seed['track_id']}/audio"

        resp = await client.post(base, files=_cover_file())
        assert resp.status_code == 422

        resp = await client.post(base, files={"file": ("e.mp3", b"", "audio/mpeg")})
        assert resp.status_code == 422

        resp = await client.post(base, files=_audio_file(), data={"audio_bitrate": "128"})
        assert resp.status_code == 422

        resp = await client.post(
            base, files=_audio_file(), data={"audio_format": "mp4", "audio_bitrate": "128"}
        )
        assert resp.status_code == 422

        resp = await client.post(
            base, files=_audio_file(), data={"audio_format": "mp3", "audio_bitrate": "0"}
        )
        assert resp.status_code == 422

        resp = await client.post(f"/api/v1/tracks/{uuid.uuid4()}/audio", files=_audio_file())
        assert resp.status_code == 404

        resp = await client.get(f"/api/v1/tracks/{seed['track_id']}/media")
        assert resp.json() == []

    api.run(scenario)


def test_upload_size_limits_enforced(monkeypatch):
    monkeypatch.setattr(media_api, "MAX_AUDIO_UPLOAD_BYTES", 16)
    monkeypatch.setattr(media_api, "MAX_LYRICS_BYTES", 16)
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/audio", files=_audio_file()
        )
        assert resp.status_code == 422
        assert "maximum size" in resp.json()["detail"]

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/lyrics",
            json={"content": "x" * 17},
        )
        assert resp.status_code == 422

    api.run(scenario)


def test_upload_lyrics():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/lyrics",
            json={"content": "ла-ла-ла\nsecond line", "language": "ru"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["asset_type"] == "lyrics"
        assert body["language"] == "ru"
        assert body["mime_type"].startswith("text/")

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/lyrics",
            json={"content": "another"},
        )
        assert resp.status_code == 409

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/lyrics", json={"content": ""}
        )
        assert resp.status_code == 422
        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/lyrics",
            json={"content": "x", "language": "x" * 21},
        )
        assert resp.status_code == 422

        resp = await client.get(
            f"/api/v1/tracks/{seed['track_id']}/media",
            params={"asset_type": "lyrics"},
        )
        asset_id = resp.json()[0]["asset_id"]
        redirect = await client.get(
            f"/api/v1/media/{asset_id}/download", follow_redirects=False
        )
        assert redirect.status_code == 307
        assert "X-Amz-Signature" in redirect.headers["location"]

    api.run(scenario)


def test_upload_covers_track_and_album():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()

        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/cover", files=_cover_file()
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["asset_type"] == "cover"
        assert resp.json()["track_id"] == str(seed["track_id"])

        # different extension = a different slot, allowed
        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/cover",
            files=_cover_file(name="art.jpg", mime="image/jpeg"),
        )
        assert resp.status_code == 201

        # the same slot again -> 409
        resp = await client.post(
            f"/api/v1/tracks/{seed['track_id']}/cover", files=_cover_file()
        )
        assert resp.status_code == 409

        resp = await client.post(
            f"/api/v1/albums/{seed['album_id']}/cover",
            files=_cover_file(name="front.png"),
        )
        assert resp.status_code == 201
        assert resp.json()["album_id"] == str(seed["album_id"])
        assert resp.json()["track_id"] is None

        # non-image MIME -> 422
        resp = await client.post(
            f"/api/v1/albums/{seed['album_id']}/cover", files=_audio_file()
        )
        assert resp.status_code == 422

        # missing album -> 404
        resp = await client.post(f"/api/v1/albums/{uuid.uuid4()}/cover", files=_cover_file())
        assert resp.status_code == 404

        resp = await client.get(f"/api/v1/tracks/{seed['track_id']}/media")
        types = sorted(a["asset_type"] for a in resp.json())
        assert types == ["cover", "cover"]

    api.run(scenario)


def test_get_asset_and_download_redirect():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        created = (
            await client.post(
                f"/api/v1/tracks/{seed['track_id']}/audio", files=_audio_file()
            )
        ).json()
        asset_id = created["asset_id"]

        resp = await client.get(f"/api/v1/media/{asset_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["checksum"] == created["checksum"]
        assert body["download_url"].startswith("http")

        # 307 to a signed URL on the client-reachable endpoint
        redirect = await client.get(
            f"/api/v1/media/{asset_id}/download", follow_redirects=False
        )
        assert redirect.status_code == 307
        location = redirect.headers["location"]
        assert location.startswith("http://127.0.0.1:9000/")
        assert "X-Amz-Signature" in location

        resp = await client.get(f"/api/v1/media/{uuid.uuid4()}")
        assert resp.status_code == 404
        resp = await client.get(f"/api/v1/media/{uuid.uuid4()}/download")
        assert resp.status_code == 404

    api.run(scenario)


def test_download_unavailable_asset_conflict():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        created = (
            await client.post(
                f"/api/v1/tracks/{seed['track_id']}/audio", files=_audio_file()
            )
        ).json()
        asset_id = created["asset_id"]

        # break the storage object state directly (simulates an
        # inconsistent registration)
        from sqlalchemy import select

        from app.models import MediaAsset

        async with api.factory() as session:
            asset = (
                await session.execute(
                    select(MediaAsset).where(
                        MediaAsset.asset_id == uuid.UUID(asset_id)
                    )
                )
            ).scalar_one()
            await session.execute(
                db_update(StorageObject)
                .where(StorageObject.object_id == asset.object_id)
                .values(status=StorageObjectStatus.PENDING)
            )
            await session.commit()

        resp = await client.get(f"/api/v1/media/{asset_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        assert resp.json()["download_url"] is None

        resp = await client.get(f"/api/v1/media/{asset_id}/download")
        assert resp.status_code == 409

    api.run(scenario)


def test_delete_media_asset():
    api = Api()

    async def scenario(client):
        seed = await api.seed_track()
        created = (
            await client.post(
                f"/api/v1/tracks/{seed['track_id']}/audio", files=_audio_file()
            )
        ).json()
        asset_id = created["asset_id"]

        # capture the internal key via the service to verify removal
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from app.models import MediaAsset

        async with api.factory() as session:
            asset = (
                await session.execute(
                    select(MediaAsset)
                    .options(joinedload(MediaAsset.object))
                    .where(MediaAsset.asset_id == uuid.UUID(asset_id))
                )
            ).scalar_one()
            object_key = asset.object.object_key
        storage = make_storage()
        assert await storage.exists(object_key)

        resp = await client.delete(f"/api/v1/media/{asset_id}")
        assert resp.status_code == 204
        assert not await storage.exists(object_key)

        resp = await client.get(f"/api/v1/media/{asset_id}")
        assert resp.status_code == 404

        resp = await client.delete(f"/api/v1/media/{asset_id}")
        assert resp.status_code == 404

    api.run(scenario)

