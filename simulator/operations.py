from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from simulator.api import STUDIO_DEVICE, ChimeApi
from simulator.media import RENDITION_SAMPLE_RATE, synth_clip, wav_bitrate_kbps


@dataclass(frozen=True, slots=True)
class SongDraft:
    title: str
    seconds: int
    artist_id: str | None = None
    new_artist_name: str | None = None
    album_id: str | None = None
    explicit: bool = False
    publish: bool = True
    lyrics: str | None = None
    lyrics_language: str = "ru"


async def create_song(api: ChimeApi, draft: SongDraft) -> dict[str, Any]:
    if draft.artist_id is None and not draft.new_artist_name:
        raise ValueError("choose an artist or enter a new artist name")
    artist_id = (
        draft.artist_id
        or (
            await api.create_artist(
                {"name": draft.new_artist_name, "status": "published"}
            )
        )["artist_id"]
    )

    if draft.album_id is None:
        album = await api.create_album(
            {
                "title": draft.title,
                "album_type": "single",
                "status": "published" if draft.publish else "draft",
                "release_date": date.today().isoformat(),
            }
        )
        album_id, track_number = album["album_id"], 1
    else:
        album = await api.get_album(draft.album_id, STUDIO_DEVICE)
        album_id, track_number = draft.album_id, len(album["tracks"]) + 1

    clip = await asyncio.to_thread(synth_clip, random.Random(), draft.seconds)
    track = await api.create_track(
        {
            "title": draft.title,
            "duration_seconds": clip.seconds,
            "album_id": album_id,
            "track_number": track_number,
            "explicit_content": draft.explicit,
            "status": "draft",
        }
    )
    track_id = track["track_id"]
    await api.add_track_artist(track_id, artist_id, "primary", 1)
    await api.upload_audio(
        track_id, clip.wav(), "master.wav", "audio/wav", is_primary=True
    )
    await api.upload_audio(
        track_id,
        clip.wav(RENDITION_SAMPLE_RATE),
        "preview.wav",
        "audio/wav",
        audio_format="wav",
        audio_bitrate=wav_bitrate_kbps(RENDITION_SAMPLE_RATE),
    )
    if draft.lyrics and draft.lyrics.strip():
        await api.upload_lyrics(track_id, draft.lyrics, draft.lyrics_language)
    if draft.publish:
        track = await api.update_track(track_id, {"status": "published"})
    return track
