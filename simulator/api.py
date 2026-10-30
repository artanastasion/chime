from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

KEEPALIVE_EXPIRY_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class RangeChunk:
    received: int
    total: int


class ApiError(RuntimeError):
    def __init__(self, status_code: int, method: str, url: str, detail: str) -> None:
        super().__init__(f"{method} {url} -> {status_code}: {detail}")
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    device_id: str
    user_id: uuid.UUID | None
    platform: str
    app_version: str
    country: str

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "X-Device-ID": self.device_id,
            "X-App-Platform": self.platform,
            "X-App-Version": self.app_version,
            "User-Agent": f"ChimeApp/{self.app_version} ({self.platform})",
        }
        if self.user_id is not None:
            headers["X-User-ID"] = str(self.user_id)
        return headers

    @property
    def context(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "platform": self.platform,
            "app_version": self.app_version,
            "country": self.country,
        }


STUDIO_DEVICE = DeviceProfile(
    device_id="chime-studio",
    user_id=None,
    platform="macos",
    app_version="studio-1.0.0",
    country="ZZ",
)


class ChimeApi:
    def __init__(
        self, base_url: str, timeout: float, max_connections: int = 64
    ) -> None:
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
            keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
        )
        self._api = httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits)
        self._storage = httpx.AsyncClient(timeout=timeout, limits=limits)

    async def __aenter__(self) -> ChimeApi:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._api.aclose()
        await self._storage.aclose()

    async def _call(
        self,
        method: str,
        url: str,
        device: DeviceProfile,
        expected: Iterable[int] = (200, 201, 202, 204),
        **kwargs: Any,
    ) -> httpx.Response:
        response = await self._api.request(
            method, url, headers=device.headers, **kwargs
        )
        if response.status_code not in expected:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, method, url, str(detail))
        return response

    async def _json(
        self, method: str, url: str, device: DeviceProfile, **kwargs: Any
    ) -> Any:
        return (await self._call(method, url, device, **kwargs)).json()

    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/health", STUDIO_DEVICE)

    async def create_artist(
        self, payload: dict[str, Any], device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json("POST", "/api/v1/artists", device, json=payload)

    async def update_artist(
        self,
        artist_id: str,
        payload: dict[str, Any],
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "PATCH", f"/api/v1/artists/{artist_id}", device, json=payload
        )

    async def list_artists(
        self, device: DeviceProfile, **params: Any
    ) -> dict[str, Any]:
        return await self._json("GET", "/api/v1/artists", device, params=params)

    async def create_user(
        self, email: str, is_active: bool = True, device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            "/api/v1/users",
            device,
            json={"email": email, "is_active": is_active},
        )

    async def update_user(
        self,
        user_id: str,
        payload: dict[str, Any],
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "PATCH", f"/api/v1/users/{user_id}", device, json=payload
        )

    async def list_users(self, device: DeviceProfile, **params: Any) -> dict[str, Any]:
        return await self._json("GET", "/api/v1/users", device, params=params)

    async def create_album(
        self, payload: dict[str, Any], device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json("POST", "/api/v1/albums", device, json=payload)

    async def update_album(
        self,
        album_id: str,
        payload: dict[str, Any],
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "PATCH", f"/api/v1/albums/{album_id}", device, json=payload
        )

    async def list_albums(self, device: DeviceProfile, **params: Any) -> dict[str, Any]:
        return await self._json("GET", "/api/v1/albums", device, params=params)

    async def get_album(self, album_id: str, device: DeviceProfile) -> dict[str, Any]:
        return await self._json("GET", f"/api/v1/albums/{album_id}", device)

    async def create_track(
        self, payload: dict[str, Any], device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json("POST", "/api/v1/tracks", device, json=payload)

    async def update_track(
        self,
        track_id: str,
        payload: dict[str, Any],
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "PATCH", f"/api/v1/tracks/{track_id}", device, json=payload
        )

    async def list_tracks(self, device: DeviceProfile, **params: Any) -> dict[str, Any]:
        return await self._json("GET", "/api/v1/tracks", device, params=params)

    async def get_track(self, track_id: str, device: DeviceProfile) -> dict[str, Any]:
        return await self._json("GET", f"/api/v1/tracks/{track_id}", device)

    async def add_track_artist(
        self,
        track_id: str,
        artist_id: str,
        role: str,
        display_order: int | None = None,
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            f"/api/v1/tracks/{track_id}/artists",
            device,
            json={"artist_id": artist_id, "role": role, "display_order": display_order},
        )

    async def remove_track_artist(
        self, track_id: str, track_artist_id: str, device: DeviceProfile = STUDIO_DEVICE
    ) -> None:
        await self._call(
            "DELETE", f"/api/v1/tracks/{track_id}/artists/{track_artist_id}", device
        )

    async def upload_audio(
        self,
        track_id: str,
        content: bytes,
        filename: str,
        mime_type: str,
        audio_format: str | None = None,
        audio_bitrate: int | None = None,
        is_primary: bool = False,
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        data: dict[str, str] = {"is_primary": str(is_primary).lower()}
        if audio_format is not None:
            data["audio_format"] = audio_format
        if audio_bitrate is not None:
            data["audio_bitrate"] = str(audio_bitrate)
        return await self._json(
            "POST",
            f"/api/v1/tracks/{track_id}/audio",
            device,
            files={"file": (filename, content, mime_type)},
            data=data,
        )

    async def upload_lyrics(
        self,
        track_id: str,
        content: str,
        language: str,
        device: DeviceProfile = STUDIO_DEVICE,
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            f"/api/v1/tracks/{track_id}/lyrics",
            device,
            json={"content": content, "language": language},
        )

    async def upload_track_cover(
        self, track_id: str, content: bytes, device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            f"/api/v1/tracks/{track_id}/cover",
            device,
            files={"file": ("cover.png", content, "image/png")},
        )

    async def upload_album_cover(
        self, album_id: str, content: bytes, device: DeviceProfile = STUDIO_DEVICE
    ) -> dict[str, Any]:
        return await self._json(
            "POST",
            f"/api/v1/albums/{album_id}/cover",
            device,
            files={"file": ("cover.png", content, "image/png")},
        )

    async def list_track_media(
        self, track_id: str, device: DeviceProfile, asset_type: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"asset_type": asset_type} if asset_type else {}
        return await self._json(
            "GET", f"/api/v1/tracks/{track_id}/media", device, params=params
        )

    async def delete_media(
        self, asset_id: str, device: DeviceProfile = STUDIO_DEVICE
    ) -> None:
        await self._call("DELETE", f"/api/v1/media/{asset_id}", device)

    async def resolve_stream(self, asset_id: str, device: DeviceProfile) -> str:
        redirect = await self._call(
            "GET", f"/api/v1/media/{asset_id}/download", device, expected=(307,)
        )
        return redirect.headers["location"]

    async def read_range(self, url: str, offset: int, length: int) -> RangeChunk:
        response = await self._storage.get(
            url, headers={"Range": f"bytes={offset}-{offset + length - 1}"}
        )
        if response.status_code == 416:
            return RangeChunk(0, offset)
        if response.status_code != 206:
            raise ApiError(
                response.status_code, "GET", "presigned-url", response.text[:200]
            )
        total = int(response.headers["content-range"].rsplit("/", 1)[1])
        return RangeChunk(len(response.content), total)

    async def fetch_text(self, url: str) -> str:
        response = await self._storage.get(url)
        if response.status_code != 200:
            raise ApiError(
                response.status_code, "GET", "presigned-url", response.text[:200]
            )
        return response.content.decode("utf-8")

    async def send_events(
        self, device: DeviceProfile, events: list[dict[str, Any]]
    ) -> int:
        body = await self._json(
            "POST",
            "/api/v1/events",
            device,
            json={"context": device.context, "events": events},
        )
        return body["accepted"]
