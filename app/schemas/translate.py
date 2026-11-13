from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TastePreferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    genre_code: str = Field(min_length=1, max_length=50)
    weight: float = Field(ge=0.0, le=1.0)

    @field_validator("genre_code")
    @classmethod
    def _normalize_genre(cls, value: str) -> str:
        return value.strip().lower()


class TastePreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    genre_code: str
    weight: float
    created_at: datetime.datetime


class TranslationLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: uuid.UUID
    source_track_id: uuid.UUID
    token: str | None = Field(default=None, min_length=8, max_length=128)
    expires_in_hours: int = Field(default=24, ge=1, le=720)


class TranslationLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender_id: uuid.UUID
    source_track_id: uuid.UUID
    expires_at: datetime.datetime
    revoked_at: datetime.datetime | None = None
    created_at: datetime.datetime
    token: str | None = None


class TranslationResultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: uuid.UUID
    recipient_id: uuid.UUID
    target_track_id: uuid.UUID
    score: float = Field(ge=0.0, le=1.0)
    feature_version: str = Field(min_length=1, max_length=50)


class TranslationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    link_id: uuid.UUID
    recipient_id: uuid.UUID
    target_track_id: uuid.UUID
    score: float
    feature_version: str
    created_at: datetime.datetime


class TrackFeatureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: uuid.UUID
    feature_version: str = Field(min_length=1, max_length=50)
    embedding: dict[str, float] | None = None
    energy: float | None = None
    valence: float | None = None
    tempo: float | None = None
    status: str = "ready"


class TrackFeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: uuid.UUID
    feature_version: str
    embedding: dict[str, float] | None = None
    energy: float | None = None
    valence: float | None = None
    tempo: float | None = None
    status: str
    ready_at: datetime.datetime | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
