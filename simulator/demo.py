from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from typing import Any

from faker import Faker

from app.core.config import settings
from app.storage.client import storage_client
from simulator.api import ApiError, ChimeApi
from simulator.clickhouse import ClickHouseClient
from simulator.media import synth_clip

POLL_INTERVAL_SECONDS = 0.2


class LifecycleDemo:
    def __init__(
        self, api: ChimeApi, ch: ClickHouseClient, timeout: float = 30.0
    ) -> None:
        self.api = api
        self.ch = ch
        self.timeout = timeout
        self.rng = random.Random()
        self.fake = Faker()

    def _say(self, step: str, detail: str = "") -> None:
        print(f"\n== {step}" + (f"\n   {detail}" if detail else ""))

    async def _replicated(
        self,
        description: str,
        sql: str,
        check: Callable[[list[dict[str, Any]]], bool],
        **params: Any,
    ) -> list[dict[str, Any]]:
        started = time.perf_counter()
        deadline = started + self.timeout
        while True:
            rows = await self.ch.query(sql, **params)
            if check(rows):
                print(
                    f"   ClickHouse <- {description}: {int((time.perf_counter() - started) * 1000)} ms"
                )
                return rows
            if time.perf_counter() > deadline:
                raise TimeoutError(
                    f"{description} did not replicate within {self.timeout}s"
                )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _object_exists(self, object_key: str) -> bool:
        def stat() -> bool:
            try:
                storage_client.stat_object(settings.storage_bucket, object_key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(stat)

    async def _storage_key(self, asset_id: str) -> list[dict[str, Any]]:
        return await self._replicated(
            "media asset + storage object",
            """
            SELECT so.object_key AS object_key, so.size_bytes AS size_bytes, so.checksum AS checksum
            FROM v_media_assets AS ma
            INNER JOIN v_storage_objects AS so ON so.object_id = ma.object_id
            WHERE ma.asset_id = {asset_id:UUID}
            """,
            lambda rows: len(rows) == 1,
            asset_id=asset_id,
        )

    async def run(self) -> None:
        tag = self.fake.color_name()

        self._say("1. INSERT: исполнитель создаётся через API")
        artist = await self.api.create_artist(
            {"name": f"Demo {tag}", "status": "published"}
        )
        await self._replicated(
            "artist row",
            "SELECT name FROM v_artists WHERE artist_id = {id:UUID}",
            lambda rows: len(rows) == 1,
            id=artist["artist_id"],
        )

        self._say("2. INSERT: два релиза и трек в первом из них")
        album_a = await self.api.create_album(
            {"title": f"{tag} Sessions", "album_type": "ep", "status": "published"}
        )
        album_b = await self.api.create_album(
            {"title": f"{tag} Live", "album_type": "album", "status": "published"}
        )
        clip = await asyncio.to_thread(synth_clip, self.rng, 30)
        track = await self.api.create_track(
            {
                "title": f"{tag} Theme",
                "duration_seconds": clip.seconds,
                "album_id": album_a["album_id"],
                "track_number": 1,
            }
        )
        await self.api.add_track_artist(
            track["track_id"], artist["artist_id"], "primary", 1
        )
        track_id = track["track_id"]
        await self._replicated(
            "track joined with album and artist",
            "SELECT album_title, primary_artist FROM v_track_catalog WHERE track_id = {id:UUID}",
            lambda rows: len(rows) == 1 and rows[0]["primary_artist"] == artist["name"],
            id=track_id,
        )

        self._say("3. UPDATE: публикация и переименование трека")
        await self.api.update_track(
            track_id, {"status": "published", "title": f"{tag} Theme (Radio Edit)"}
        )
        await self._replicated(
            "new track version",
            "SELECT title, status FROM v_tracks WHERE track_id = {id:UUID}",
            lambda rows: (
                rows
                and rows[0]["status"] == "published"
                and "Radio Edit" in rows[0]["title"]
            ),
            id=track_id,
        )

        self._say(
            "4. MOVE: трек переносится в другой релиз",
            f"{album_a['title']} -> {album_b['title']}",
        )
        await self.api.update_track(
            track_id, {"album_id": album_b["album_id"], "track_number": 3}
        )
        await self._replicated(
            "track moved to album B",
            "SELECT album_title FROM v_track_catalog WHERE track_id = {id:UUID}",
            lambda rows: rows and rows[0]["album_title"] == album_b["title"],
            id=track_id,
        )

        self._say(
            "5. UPLOAD: оригинал аудио -> RustFS + storage_objects + media_assets"
        )
        audio = clip.wav()
        asset = await self.api.upload_audio(
            track_id, audio, "master.wav", "audio/wav", is_primary=True
        )
        stored = (await self._storage_key(asset["asset_id"]))[0]
        print(
            f"   RustFS: {stored['object_key']} ({stored['size_bytes']} B) exists={await self._object_exists(stored['object_key'])}"
        )
        print(f"   sha256 API={asset['checksum'][:16]}… DWH={stored['checksum'][:16]}…")

        self._say("6. CONFLICT: повторная загрузка в занятый слот")
        try:
            await self.api.upload_audio(track_id, audio, "master.wav", "audio/wav")
        except ApiError as exc:
            print(
                f"   API ответил {exc.status_code} — слот занят, перезапись запрещена (ожидаемо)"
            )

        self._say("7. REPLACE: DELETE старого актива -> POST нового")
        await self.api.delete_media(asset["asset_id"])
        await self._replicated(
            "asset deletion",
            "SELECT count() AS n FROM v_media_assets WHERE asset_id = {id:UUID}",
            lambda rows: int(rows[0]["n"]) == 0,
            id=asset["asset_id"],
        )
        print(
            f"   RustFS: {stored['object_key']} exists={await self._object_exists(stored['object_key'])}"
        )
        remaster = await asyncio.to_thread(synth_clip, self.rng, clip.seconds)
        replacement = await self.api.upload_audio(
            track_id, remaster.wav(), "master.wav", "audio/wav", is_primary=True
        )
        await self._storage_key(replacement["asset_id"])

        self._say("8. LINK/UNLINK: фит добавляется и удаляется")
        guest = await self.api.create_artist(
            {"name": f"Guest {tag}", "status": "published"}
        )
        link = await self.api.add_track_artist(
            track_id, guest["artist_id"], "featured", 2
        )
        await self._replicated(
            "featured link",
            "SELECT count() AS n FROM v_track_artists WHERE track_artist_id = {id:UUID}",
            lambda rows: int(rows[0]["n"]) == 1,
            id=link["track_artist_id"],
        )
        await self.api.remove_track_artist(track_id, link["track_artist_id"])
        await self._replicated(
            "featured link deletion",
            "SELECT count() AS n FROM v_track_artists WHERE track_artist_id = {id:UUID}",
            lambda rows: int(rows[0]["n"]) == 0,
            id=link["track_artist_id"],
        )

        self._say("9. HISTORY: полный журнал изменений трека в DWH (cdc_changelog)")
        history = await self.ch.query(
            """
            SELECT op, lsn, source_ts,
                   JSONExtractString(payload, 'title') AS title,
                   JSONExtractString(payload, 'status') AS status,
                   JSONExtractString(payload, 'album_id') AS album_id
            FROM cdc_changelog
            WHERE source_table = 'tracks' AND JSONExtractString(payload, 'track_id') = {id:String}
            ORDER BY lsn
            """,
            id=track_id,
        )
        for row in history:
            print(
                f"   {row['source_ts']}  op={row['op']}  {row['status']:<10} {row['title']}  album={row['album_id'][:8]}…"
            )
        print(f"\nГотово. track_id={track_id}")
