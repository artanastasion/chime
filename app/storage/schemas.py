"""Data transfer objects used by the storage layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Outcome of a successful object upload to RustFS.

    ``checksum_sha256`` is computed by the application itself; the S3
    ETag is deliberately not exposed as a checksum substitute.
    """

    bucket: str
    object_key: str
    size_bytes: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    """Head/stat information about an existing object."""

    object_key: str
    size_bytes: int
    etag: str | None
    last_modified: datetime | None
    content_type: str | None

