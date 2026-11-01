from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from faker import Faker

from simulator.api import ChimeApi
from simulator.media import (
    RENDITION_SAMPLE_RATE,
    render_cover_png,
    synth_clip,
    wav_bitrate_kbps,
    write_lyrics,
)

ALBUM_TYPE_TRACKS = {
    "single": (1, 2),
    "ep": (3, 5),
    "album": (6, 10),
    "compilation": (6, 9),
}
ALBUM_TYPE_WEIGHTS = {"single": 3, "ep": 3, "album": 4, "compilation": 1}
STATUS_WEIGHTS = {"published": 82, "draft": 13, "withdrawn": 5}
LYRICS_LANGUAGES = {"en": 6, "ru": 3, "es": 1}


@dataclass(frozen=True, slots=True)
class SeedPlan:
    users: int = 60
    artists: int = 16
    albums: int = 20
    min_track_seconds: int = 15
    max_track_seconds: int = 45
    concurrency: int = 6
    seed: int = 42


@dataclass(slots=True)
class SeedReport:
    users: int = 0
    artists: int = 0
    albums: int = 0
    tracks: int = 0
    track_artists: int = 0
    media_assets: int = 0
    uploaded_bytes: int = 0
    failures: list[str] = field(default_factory=list)


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _title(fake: Faker, rng: random.Random, words: tuple[int, int]) -> str:
    return " ".join(fake.words(rng.randint(*words))).title()


class CatalogSeeder:
    def __init__(self, api: ChimeApi, plan: SeedPlan) -> None:
        self.api = api
        self.plan = plan
        self.rng = random.Random(plan.seed)
        self.fake = Faker(["en_US", "ru_RU", "es_ES"])
        self.fake.seed_instance(plan.seed)
        self.report = SeedReport()
        self._slots = asyncio.Semaphore(plan.concurrency)

    async def run(self) -> SeedReport:
        users = await asyncio.gather(
            *(
                self.api.create_user(
                    f"{self.fake.user_name()}.{index}.{uuid.uuid4().hex[:6]}@example.com"
                )
                for index in range(self.plan.users)
            )
        )
        self.report.users = len(users)
        artists = await asyncio.gather(
            *(self._create_artist() for _ in range(self.plan.artists))
        )
        self.report.artists = len(artists)
        album_specs = [self._album_spec(artists) for _ in range(self.plan.albums)]
        await asyncio.gather(*(self._seed_album(spec) for spec in album_specs))
        return self.report

    async def _create_artist(self) -> dict:
        status = "published" if self.rng.random() < 0.9 else "draft"
        return await self.api.create_artist(
            {
                "name": self.fake.unique.name()
                if self.rng.random() < 0.5
                else _title(self.fake, self.rng, (1, 3)),
                "description": self.fake.paragraph(nb_sentences=2),
                "status": status,
            }
        )

    def _album_spec(self, artists: list[dict]) -> dict:
        album_type = _weighted(self.rng, ALBUM_TYPE_WEIGHTS)
        status = _weighted(self.rng, STATUS_WEIGHTS)
        release = date.today() - timedelta(days=self.rng.randint(0, 5 * 365))
        return {
            "payload": {
                "title": _title(self.fake, self.rng, (1, 4)),
                "release_date": release.isoformat(),
                "album_type": album_type,
                "status": status,
            },
            "primary": self.rng.choice(artists),
            "guests": self.rng.sample(artists, k=min(3, len(artists))),
            "tracks": self.rng.randint(*ALBUM_TYPE_TRACKS[album_type]),
            "rng_seed": self.rng.getrandbits(32),
        }

    async def _seed_album(self, spec: dict) -> None:
        async with self._slots:
            rng = random.Random(spec["rng_seed"])
            album = await self.api.create_album(spec["payload"])
            self.report.albums += 1
            await self._upload(
                self.api.upload_album_cover(album["album_id"], render_cover_png(rng))
            )
            for number in range(1, spec["tracks"] + 1):
                await self._seed_track(album, number, spec, rng)

    async def _seed_track(
        self, album: dict, number: int, spec: dict, rng: random.Random
    ) -> None:
        status = album["status"] if rng.random() < 0.92 else "draft"
        clip = await asyncio.to_thread(
            synth_clip,
            rng,
            rng.randint(self.plan.min_track_seconds, self.plan.max_track_seconds),
        )
        track = await self.api.create_track(
            {
                "title": _title(self.fake, rng, (1, 4)),
                "duration_seconds": clip.seconds,
                "album_id": album["album_id"],
                "track_number": number,
                "explicit_content": rng.random() < 0.15,
                "status": status,
            }
        )
        self.report.tracks += 1
        track_id = track["track_id"]

        await self.api.add_track_artist(
            track_id, spec["primary"]["artist_id"], "primary", 1
        )
        self.report.track_artists += 1
        guests = [
            a for a in spec["guests"] if a["artist_id"] != spec["primary"]["artist_id"]
        ]
        if guests and rng.random() < 0.3:
            await self.api.add_track_artist(
                track_id, rng.choice(guests)["artist_id"], "featured", 2
            )
            self.report.track_artists += 1
        if guests and rng.random() < 0.2:
            await self.api.add_track_artist(
                track_id, rng.choice(guests)["artist_id"], "producer"
            )
            self.report.track_artists += 1

        await self._upload(
            self.api.upload_audio(
                track_id, clip.wav(), "master.wav", "audio/wav", is_primary=True
            )
        )
        await self._upload(
            self.api.upload_audio(
                track_id,
                clip.wav(RENDITION_SAMPLE_RATE),
                "preview.wav",
                "audio/wav",
                audio_format="wav",
                audio_bitrate=wav_bitrate_kbps(RENDITION_SAMPLE_RATE),
            )
        )
        if rng.random() < 0.6:
            await self._upload(
                self.api.upload_lyrics(
                    track_id,
                    write_lyrics(self.fake, rng),
                    _weighted(rng, LYRICS_LANGUAGES),
                )
            )
        if rng.random() < 0.25:
            await self._upload(
                self.api.upload_track_cover(track_id, render_cover_png(rng))
            )

    async def _upload(self, call) -> None:
        try:
            asset = await call
        except Exception as exc:
            self.report.failures.append(str(exc))
            return
        self.report.media_assets += 1
        self.report.uploaded_bytes += asset["size_bytes"]
