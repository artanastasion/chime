"""Media asset schemas of the HTTP API.

Deliberately does NOT expose internal storage details such as the
bucket name or the S3 object key: ``download_url`` is a short-lived
presigned link generated per request.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models import MediaAssetType, StorageObjectStatus


class LyricsUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="Original lyrics, UTF-8")
    language: str | None = Field(
        default=None, max_length=20, description="ISO-ish language tag"
    )


class MediaAssetOut(BaseModel):
    asset_id: uuid.UUID
    asset_type: MediaAssetType
    track_id: uuid.UUID | None
    album_id: uuid.UUID | None
    language: str | None
    audio_format: str | None
    audio_bitrate: int | None
    is_primary: bool
    status: StorageObjectStatus
    mime_type: str
    size_bytes: int
    checksum: str
    created_at: datetime.datetime
    download_url: str | None
