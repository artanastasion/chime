from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from faker import Faker

from simulator.api import STUDIO_DEVICE, ApiError, ChimeApi, DeviceProfile
from simulator.media import WAV_HEADER_BYTES, render_cover_png, synth_clip
from simulator.profiles import build_profiles, load_user_ids, track_appeal

logger = logging.getLogger(__name__)

EVENT_FLUSH_SIZE = 25
CATALOG_REFRESH_SECONDS = 30
PLAYBACK_CHUNK_SECONDS = 2.0
NEW_TRACK_SECONDS = (15, 45)


@dataclass(slots=True)
class TrafficStats:
    sessions: int = 0
    events: int = 0
    plays: int = 0
    completed: int = 0
    seeks: int = 0
    range_requests: int = 0
    streamed_bytes: int = 0
    listened_ms: int = 0
    studio_ops: Counter[str] = field(default_factory=Counter)
    errors: Counter[int] = field(default_factory=Counter)


@dataclass(slots=True)
class CatalogSnapshot:
    tracks: list[dict[str, Any]] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    albums: list[dict[str, Any]] = field(default_factory=list)
    artists: list[dict[str, Any]] = field(default_factory=list)

    def pick_track(self, rng: random.Random) -> dict[str, Any] | None:
        if not self.tracks:
            return None
        return rng.choices(self.tracks, weights=self.weights)[0]

    def search_term(self, rng: random.Random) -> str | None:
        if not self.tracks:
            return None
        words = [w for w in rng.choice(self.tracks)["title"].split() if len(w) > 3]
        return rng.choice(words)[:5].lower() if words else None


async def _fetch_all(
    fetch, device: DeviceProfile, **params: Any
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await fetch(device, limit=100, offset=offset, **params)
        items.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return items


async def load_catalog(
    api: ChimeApi, device: DeviceProfile, seed: int = 7
) -> CatalogSnapshot:
    tracks = await _fetch_all(api.list_tracks, device, status="published")
    albums = await _fetch_all(api.list_albums, device)
    artists = await _fetch_all(api.list_artists, device)
    ranked = sorted(tracks, key=lambda t: track_appeal(t["track_id"]), reverse=True)
    random.Random(seed).shuffle(ranked[len(ranked) // 3 :])
    weights = [1.0 / (rank + 1) ** 0.85 for rank in range(len(ranked))]
    return CatalogSnapshot(ranked, weights, albums, artists)


class EventBuffer:
    def __init__(
        self, api: ChimeApi, device: DeviceProfile, stats: TrafficStats
    ) -> None:
        self.api = api
        self.device = device
        self.stats = stats
        self.session_id = str(uuid.uuid4())
        self._pending: list[dict[str, Any]] = []

    def add(self, event_type: str, **fields: Any) -> None:
        self._pending.append(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(UTC).isoformat(),
                "session_id": self.session_id,
                **{k: v for k, v in fields.items() if v is not None},
            }
        )
        details = " ".join(
            f"{k}={v}" for k, v in fields.items() if v is not None and k != "album_id"
        )
        logger.debug(
            "%s %s/%s/%s %s %s",
            self.device.device_id[:13],
            self.device.platform,
            self.device.country,
            "user" if self.device.user_id else "guest",
            event_type,
            details,
        )

    async def flush(self, force: bool = False) -> None:
        if not self._pending or (not force and len(self._pending) < EVENT_FLUSH_SIZE):
            return
        batch, self._pending = self._pending, []
        try:
            accepted = await self.api.send_events(self.device, batch)
            self.stats.events += accepted
        except (ApiError, httpx.HTTPError) as exc:
            self.stats.errors[getattr(exc, "status_code", 0)] += 1
            self._pending = batch + self._pending


class ListenerSimulator:
    def __init__(
        self,
        api: ChimeApi,
        catalog: CatalogSnapshot,
        stats: TrafficStats,
        speed: float,
        rng: random.Random,
        stop: asyncio.Event | None = None,
    ) -> None:
        self.api = api
        self.catalog = catalog
        self.stats = stats
        self.speed = speed
        self.rng = rng
        self.stop = stop or asyncio.Event()

    async def _think(self, low: float, high: float) -> None:
        await asyncio.sleep(self.rng.uniform(low, high) / self.speed)

    async def run_session(self, device: DeviceProfile) -> None:
        events = EventBuffer(self.api, device, self.stats)
        self.stats.sessions += 1
        events.add("session_start")
        try:
            for _ in range(self.rng.randint(2, 6)):
                if self.stop.is_set():
                    break
                track = await self._discover(device, events)
                if track is not None:
                    await self._listen(device, events, track)
                await events.flush()
                await self._think(0.5, 2.0)
        except ApiError as exc:
            self.stats.errors[exc.status_code] += 1
        except httpx.HTTPError as exc:
            self.stats.errors[0] += 1
            logger.warning("listener transport error: %s", exc)
        finally:
            await events.flush(force=True)

    async def _discover(
        self, device: DeviceProfile, events: EventBuffer
    ) -> dict[str, Any] | None:
        mode = self.rng.random()
        if mode < 0.25 and (term := self.catalog.search_term(self.rng)):
            page = await self.api.list_tracks(
                device, status="published", title_icontains=term, limit=20
            )
            events.add(
                "search", properties={"query": term, "results": str(page["total"])}
            )
            if page["items"]:
                return self.rng.choice(page["items"])
        elif mode < 0.4 and self.catalog.albums:
            album = await self.api.get_album(
                self.rng.choice(self.catalog.albums)["album_id"], device
            )
            events.add("album_view", album_id=album["album_id"])
            published = [t for t in album["tracks"] if t["status"] == "published"]
            if published:
                return self.rng.choice(published)
        else:
            await self.api.list_tracks(
                device,
                status="published",
                limit=20,
                offset=self.rng.randint(0, max(len(self.catalog.tracks) - 20, 0)),
            )
        return self.catalog.pick_track(self.rng)

    async def _listen(
        self, device: DeviceProfile, events: EventBuffer, track: dict[str, Any]
    ) -> None:
        detail = await self.api.get_track(track["track_id"], device)
        track_id, album_id = detail["track_id"], detail["album_id"]
        logger.debug(
            "%s opens «%s» (%ss)",
            device.device_id[:13],
            detail["title"],
            detail["duration_seconds"],
        )
        events.add("track_view", track_id=track_id, album_id=album_id)
        if self.rng.random() > 0.85:
            return

        audio = await self.api.list_track_media(track_id, device, asset_type="audio")
        available = [a for a in audio if a["status"] == "available"]
        if not available:
            return
        stream = min(available, key=lambda a: a["audio_bitrate"] or 10**9)
        await self._play(device, events, stream, detail, album_id)

    async def _play(
        self,
        device: DeviceProfile,
        events: EventBuffer,
        stream: dict[str, Any],
        track: dict[str, Any],
        album_id: str | None,
    ) -> None:
        track_id = track["track_id"]
        size = stream["size_bytes"]
        payload = max(size - WAV_HEADER_BYTES, 1)
        bytes_per_ms = payload / (track["duration_seconds"] * 1000)
        chunk = max(int(bytes_per_ms * PLAYBACK_CHUNK_SECONDS * 1000), 1)

        def position_ms(offset: int) -> int:
            return int(max(offset - WAV_HEADER_BYTES, 0) / bytes_per_ms)

        appeal = track_appeal(track_id)
        if self.rng.random() < 0.3 + 0.55 * appeal:
            stop_at = size
        else:
            stop_at = WAV_HEADER_BYTES + int(payload * self.rng.betavariate(1.3, 4.0))
        seek_after = (
            WAV_HEADER_BYTES + int(payload * self.rng.uniform(0.05, 0.4))
            if self.rng.random() < 0.25
            else None
        )

        url = await self.api.resolve_stream(stream["asset_id"], device)
        events.add("play_start", track_id=track_id, album_id=album_id, position_ms=0)
        self.stats.plays += 1
        offset = 0
        listened_bytes = 0
        while offset < min(stop_at, size) and not self.stop.is_set():
            requested = min(chunk, stop_at - offset)
            part = await self.api.read_range(url, offset, requested)
            self.stats.range_requests += 1
            if part.received == 0:
                break
            self.stats.streamed_bytes += part.received
            listened_bytes += part.received - max(
                0, min(WAV_HEADER_BYTES, offset + part.received) - offset
            )
            offset += part.received
            await asyncio.sleep(
                PLAYBACK_CHUNK_SECONDS * part.received / chunk / self.speed
            )
            if seek_after is not None and offset >= seek_after:
                target = min(
                    size - 1, offset + int(payload * self.rng.uniform(0.1, 0.4))
                )
                events.add(
                    "play_seek",
                    track_id=track_id,
                    album_id=album_id,
                    position_ms=position_ms(target),
                )
                self.stats.seeks += 1
                offset, seek_after = target, None

        listened_ms = int(listened_bytes / bytes_per_ms)
        self.stats.listened_ms += listened_ms
        if offset >= size:
            self.stats.completed += 1
            events.add(
                "play_complete",
                track_id=track_id,
                album_id=album_id,
                position_ms=track["duration_seconds"] * 1000,
                duration_ms=listened_ms,
            )
        else:
            events.add(
                "play_skip",
                track_id=track_id,
                album_id=album_id,
                position_ms=position_ms(offset),
                duration_ms=listened_ms,
            )


class StudioSimulator:
    OPERATIONS = {
        "release_single": 3,
        "publish_draft": 3,
        "retitle_track": 3,
        "move_track": 3,
        "feature_artist": 2,
        "unfeature_artist": 2,
        "replace_audio": 2,
        "drop_track_cover": 1,
        "withdraw_track": 1,
        "describe_artist": 1,
        "duplicate_upload": 1,
        "signup_users": 2,
        "deactivate_user": 1,
    }

    def __init__(
        self,
        api: ChimeApi,
        catalog: CatalogSnapshot,
        stats: TrafficStats,
        rng: random.Random,
    ) -> None:
        self.api = api
        self.catalog = catalog
        self.stats = stats
        self.rng = rng
        self.fake = Faker(["en_US", "ru_RU"])
        self.fake.seed_instance(rng.getrandbits(32))

    async def step(self) -> None:
        operation = self.rng.choices(
            list(self.OPERATIONS), weights=list(self.OPERATIONS.values())
        )[0]
        try:
            await getattr(self, f"_{operation}")()
            self.stats.studio_ops[operation] += 1
            logger.debug("studio %s ok", operation)
        except ApiError as exc:
            self.stats.errors[exc.status_code] += 1
            self.stats.studio_ops[f"{operation}:{exc.status_code}"] += 1
            logger.debug("studio %s -> HTTP %s", operation, exc.status_code)
        except httpx.HTTPError as exc:
            self.stats.errors[0] += 1
            logger.warning("studio transport error during %s: %s", operation, exc)

    def _any_track(self) -> dict[str, Any] | None:
        return self.rng.choice(self.catalog.tracks) if self.catalog.tracks else None

    def _title(self) -> str:
        return " ".join(self.fake.words(self.rng.randint(1, 3))).title()

    async def _master(self, seconds: int) -> bytes:
        clip = await asyncio.to_thread(synth_clip, self.rng, seconds)
        return clip.wav()

    async def _release_single(self) -> None:
        album = await self.api.create_album(
            {
                "title": self._title(),
                "album_type": "single",
                "status": "draft",
                "release_date": datetime.now(UTC).date().isoformat(),
            }
        )
        seconds = self.rng.randint(*NEW_TRACK_SECONDS)
        track = await self.api.create_track(
            {
                "title": album["title"],
                "duration_seconds": seconds,
                "album_id": album["album_id"],
                "track_number": 1,
                "status": "draft",
            }
        )
        if self.catalog.artists:
            await self.api.add_track_artist(
                track["track_id"],
                self.rng.choice(self.catalog.artists)["artist_id"],
                "primary",
                1,
            )
        await self.api.upload_audio(
            track["track_id"],
            await self._master(seconds),
            "master.wav",
            "audio/wav",
            is_primary=True,
        )
        await self.api.upload_album_cover(
            album["album_id"], render_cover_png(self.rng, 128)
        )
        await self.api.update_album(album["album_id"], {"status": "published"})
        published = await self.api.update_track(
            track["track_id"], {"status": "published"}
        )
        self.catalog.albums.append(album)
        self.catalog.tracks.append(published)
        self.catalog.weights.append(
            self.catalog.weights[-1] if self.catalog.weights else 1.0
        )

    async def _publish_draft(self) -> None:
        drafts = (await self.api.list_tracks(STUDIO_DEVICE, status="draft", limit=100))[
            "items"
        ]
        if drafts:
            await self.api.update_track(
                self.rng.choice(drafts)["track_id"], {"status": "published"}
            )

    async def _retitle_track(self) -> None:
        if track := self._any_track():
            suffix = self.rng.choice(
                ["Remastered", "Radio Edit", "Live", "Extended Mix", "Acoustic"]
            )
            track["title"] = f"{track['title'].split(' (')[0]} ({suffix})"
            await self.api.update_track(track["track_id"], {"title": track["title"]})

    async def _move_track(self) -> None:
        track = self._any_track()
        candidates = [
            a
            for a in self.catalog.albums
            if track and a["album_id"] != track["album_id"]
        ]
        if track and candidates:
            moved = await self.api.update_track(
                track["track_id"], {"album_id": self.rng.choice(candidates)["album_id"]}
            )
            track["album_id"] = moved["album_id"]

    async def _feature_artist(self) -> None:
        track = self._any_track()
        if track and self.catalog.artists:
            artist = self.rng.choice(self.catalog.artists)
            await self.api.add_track_artist(
                track["track_id"], artist["artist_id"], "featured", 2
            )

    async def _unfeature_artist(self) -> None:
        if track := self._any_track():
            detail = await self.api.get_track(track["track_id"], STUDIO_DEVICE)
            featured = [a for a in detail["artists"] if a["role"] != "primary"]
            if featured:
                await self.api.remove_track_artist(
                    track["track_id"], self.rng.choice(featured)["track_artist_id"]
                )

    async def _replace_audio(self) -> None:
        if track := self._any_track():
            audio = await self.api.list_track_media(
                track["track_id"], STUDIO_DEVICE, asset_type="audio"
            )
            original = next((a for a in audio if a["audio_bitrate"] is None), None)
            if original is not None:
                await self.api.delete_media(original["asset_id"])
            content = await self._master(track["duration_seconds"])
            await self.api.upload_audio(
                track["track_id"], content, "master.wav", "audio/wav", is_primary=True
            )

    async def _drop_track_cover(self) -> None:
        if track := self._any_track():
            covers = await self.api.list_track_media(
                track["track_id"], STUDIO_DEVICE, asset_type="cover"
            )
            if covers:
                await self.api.delete_media(covers[0]["asset_id"])
            else:
                await self.api.upload_track_cover(
                    track["track_id"], render_cover_png(self.rng, 128)
                )

    async def _withdraw_track(self) -> None:
        if len(self.catalog.tracks) > 20 and (track := self._any_track()):
            await self.api.update_track(track["track_id"], {"status": "withdrawn"})
            index = self.catalog.tracks.index(track)
            del self.catalog.tracks[index]
            del self.catalog.weights[index]

    async def _describe_artist(self) -> None:
        if self.catalog.artists:
            artist = self.rng.choice(self.catalog.artists)
            await self.api.update_artist(
                artist["artist_id"],
                {"description": self.fake.paragraph(nb_sentences=2)},
            )

    async def _duplicate_upload(self) -> None:
        if track := self._any_track():
            content = await self._master(track["duration_seconds"])
            await self.api.upload_audio(
                track["track_id"], content, "master.wav", "audio/wav"
            )

    async def _signup_users(self) -> None:
        for _ in range(self.rng.randint(1, 3)):
            await self.api.create_user(
                f"{self.fake.user_name()}.{uuid.uuid4().hex[:8]}@example.com"
            )

    async def _deactivate_user(self) -> None:
        user_ids = await load_user_ids(self.api)
        if len(user_ids) > 10:
            await self.api.update_user(
                str(self.rng.choice(user_ids)), {"is_active": False}
            )


async def run_traffic(
    api: ChimeApi,
    duration_seconds: float | None,
    listeners: int,
    studio_interval: float,
    speed: float,
    seed: int,
    progress_every: float = 10.0,
    stop: asyncio.Event | None = None,
    stats: TrafficStats | None = None,
) -> TrafficStats:
    rng = random.Random(seed)
    stats = stats if stats is not None else TrafficStats()
    stop = stop if stop is not None else asyncio.Event()
    catalog = await load_catalog(api, STUDIO_DEVICE)
    if not catalog.tracks:
        raise RuntimeError("catalog has no published tracks, run `seed` first")
    profiles = build_profiles(await load_user_ids(api), rng)

    async def pause(seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def listener_worker(worker_rng: random.Random) -> None:
        simulator = ListenerSimulator(api, catalog, stats, speed, worker_rng, stop)
        while not stop.is_set():
            await simulator.run_session(worker_rng.choice(profiles))
            await pause(worker_rng.uniform(1.0, 5.0) / speed)

    async def studio_worker() -> None:
        studio = StudioSimulator(
            api, catalog, stats, random.Random(rng.getrandbits(32))
        )
        while not stop.is_set():
            await studio.step()
            await pause(rng.uniform(0.5, 1.5) * studio_interval)

    async def refresher() -> None:
        while not stop.is_set():
            await pause(CATALOG_REFRESH_SECONDS)
            fresh = await load_catalog(api, STUDIO_DEVICE)
            catalog.tracks, catalog.weights, catalog.albums, catalog.artists = (
                fresh.tracks,
                fresh.weights,
                fresh.albums,
                fresh.artists,
            )

    async def reporter() -> None:
        while not stop.is_set():
            await pause(progress_every)
            logger.info(
                "sessions=%d plays=%d completed=%d range_requests=%d events=%d studio_ops=%d errors=%s",
                stats.sessions,
                stats.plays,
                stats.completed,
                stats.range_requests,
                stats.events,
                sum(stats.studio_ops.values()),
                dict(stats.errors),
            )

    async def timer() -> None:
        if duration_seconds is not None:
            await pause(duration_seconds)
            stop.set()

    tasks = [
        listener_worker(random.Random(rng.getrandbits(32))) for _ in range(listeners)
    ]
    if studio_interval > 0:
        tasks.append(studio_worker())
    tasks += [refresher(), reporter(), timer()]
    await asyncio.gather(*tasks)
    return stats
