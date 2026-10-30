from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

MAX_EVENTS_PER_BATCH = 500
MAX_EVENT_AGE = timedelta(days=31)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_PROPERTIES = 20
MAX_PROPERTY_LENGTH = 256


class ClientEventType(enum.StrEnum):
    SESSION_START = "session_start"
    SEARCH = "search"
    ALBUM_VIEW = "album_view"
    TRACK_VIEW = "track_view"
    PLAY_START = "play_start"
    PLAY_SEEK = "play_seek"
    PLAY_SKIP = "play_skip"
    PLAY_COMPLETE = "play_complete"


class ClientPlatform(enum.StrEnum):
    IOS = "ios"
    ANDROID = "android"
    MACOS = "macos"
    WINDOWS = "windows"


class ClientContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(min_length=1, max_length=64)
    user_id: uuid.UUID | None = None
    platform: ClientPlatform
    app_version: str = Field(min_length=1, max_length=32)
    country: str = Field(default="ZZ", pattern=r"^[A-Z]{2}$")


class ClientEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: ClientEventType
    occurred_at: AwareDatetime
    session_id: uuid.UUID
    track_id: uuid.UUID | None = None
    album_id: uuid.UUID | None = None
    position_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    properties: dict[str, str] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def _within_accepted_window(cls, value: datetime) -> datetime:
        now = datetime.now(UTC)
        if value > now + MAX_CLOCK_SKEW:
            raise ValueError("occurred_at is in the future")
        if value < now - MAX_EVENT_AGE:
            raise ValueError("occurred_at is older than the accepted window")
        return value.astimezone(UTC)

    @field_validator("properties")
    @classmethod
    def _bounded_properties(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_PROPERTIES:
            raise ValueError(f"at most {MAX_PROPERTIES} properties are allowed")
        if any(
            len(k) > MAX_PROPERTY_LENGTH or len(v) > MAX_PROPERTY_LENGTH
            for k, v in value.items()
        ):
            raise ValueError(
                f"property keys and values are limited to {MAX_PROPERTY_LENGTH} chars"
            )
        return value


class ClientEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: ClientContext
    events: list[ClientEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


class ClientEventBatchAccepted(BaseModel):
    accepted: int
