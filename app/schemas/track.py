"""Track schemas of the HTTP API."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import AlbumStatus, AlbumType, ArtistRole, TrackStatus


class TrackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    duration_seconds: int = Field(gt=0)
    album_id: uuid.UUID | None = None
    track_number: int | None = Field(default=None, gt=0)
    explicit_content: bool = False
    status: TrackStatus = TrackStatus.DRAFT


class TrackUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    duration_seconds: int | None = Field(default=None, gt=0)
    album_id: uuid.UUID | None = None
    track_number: int | None = Field(default=None, gt=0)
    explicit_content: bool | None = None
    status: TrackStatus | None = None


class TrackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: uuid.UUID
    album_id: uuid.UUID | None
    title: str
    duration_seconds: int
    track_number: int | None
    explicit_content: bool
    status: TrackStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime


class TrackAlbumBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    album_id: uuid.UUID
    title: str
    album_type: AlbumType
    status: AlbumStatus


class TrackArtistAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist_id: uuid.UUID
    role: ArtistRole
    display_order: int | None = None


class TrackArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_artist_id: uuid.UUID
    track_id: uuid.UUID
    artist_id: uuid.UUID
    role: ArtistRole
    display_order: int | None


class ArtistInTrack(BaseModel):
    track_artist_id: uuid.UUID
    artist_id: uuid.UUID
    name: str
    role: ArtistRole
    display_order: int | None


class TrackDetail(TrackOut):
    album: TrackAlbumBrief | None
    artists: list[ArtistInTrack]
