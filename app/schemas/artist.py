"""Artist schemas of the HTTP API."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import ArtistStatus


class ArtistCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    status: ArtistStatus = ArtistStatus.DRAFT


class ArtistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: ArtistStatus | None = None


class ArtistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artist_id: uuid.UUID
    name: str
    description: str | None
    status: ArtistStatus
    created_at: datetime.datetime
