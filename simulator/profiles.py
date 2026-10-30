from __future__ import annotations

import random
import uuid
import zlib

from simulator.api import STUDIO_DEVICE, ChimeApi, DeviceProfile

PLATFORM_WEIGHTS = {"ios": 42, "android": 38, "macos": 12, "windows": 8}
APP_VERSIONS = {"2.3.0": 45, "2.2.1": 30, "2.1.4": 15, "1.9.8": 10}
COUNTRY_WEIGHTS = {
    "RU": 30,
    "US": 18,
    "DE": 10,
    "BR": 9,
    "KZ": 7,
    "GB": 7,
    "ES": 6,
    "TR": 5,
    "IN": 5,
    "FR": 3,
}


def _pick(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


async def load_user_ids(api: ChimeApi) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    offset = 0
    while True:
        page = await api.list_users(
            STUDIO_DEVICE, is_active=True, limit=100, offset=offset
        )
        ids.extend(uuid.UUID(user["id"]) for user in page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return ids


def build_profiles(
    user_ids: list[uuid.UUID], rng: random.Random, anonymous_share: float = 0.25
) -> list[DeviceProfile]:
    profiles: list[DeviceProfile] = []
    for user_id in user_ids:
        country = _pick(rng, COUNTRY_WEIGHTS)
        for _ in range(1 if rng.random() < 0.8 else 2):
            profiles.append(
                DeviceProfile(
                    device_id=f"dev-{uuid.uuid4().hex[:16]}",
                    user_id=user_id,
                    platform=_pick(rng, PLATFORM_WEIGHTS),
                    app_version=_pick(rng, APP_VERSIONS),
                    country=country,
                )
            )
    anonymous = (
        int(len(profiles) * anonymous_share / (1 - anonymous_share)) if profiles else 10
    )
    for _ in range(anonymous):
        profiles.append(
            DeviceProfile(
                device_id=f"anon-{uuid.uuid4().hex[:16]}",
                user_id=None,
                platform=_pick(rng, PLATFORM_WEIGHTS),
                app_version=_pick(rng, APP_VERSIONS),
                country=_pick(rng, COUNTRY_WEIGHTS),
            )
        )
    return profiles


def track_appeal(track_id: str) -> float:
    return (zlib.crc32(track_id.encode()) % 1000) / 1000
