"""Album schemas of the HTTP API."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import AlbumStatus, AlbumType, TrackStatus


class AlbumCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    release_date: datetime.date | None = None
    album_type: AlbumType = AlbumType.ALBUM
    status: AlbumStatus = AlbumStatus.DRAFT


class AlbumUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=300)
    release_date: datetime.date | None = None
    album_type: AlbumType | None = None
    status: AlbumStatus | None = None


class AlbumOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    album_id: uuid.UUID
    title: str
    release_date: datetime.date | None
    album_type: AlbumType
    status: AlbumStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AlbumTrackBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: uuid.UUID
    title: str
    duration_seconds: int
    track_number: int | None
    status: TrackStatus


class AlbumArtistBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artist_id: uuid.UUID
    name: str


class AlbumDetail(AlbumOut):
    tracks: list[AlbumTrackBrief]
    artists: list[AlbumArtistBrief]
