"""StorageObject: registration of a binary object stored in RustFS.

The binary content itself lives in the object storage; PostgreSQL keeps
only the information required to locate the object (bucket + key),
verify its integrity and link it to catalog entities.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

# Forward reference only: the relationship resolves the string name via
# the declarative registry, so no runtime import (would be circular).
if TYPE_CHECKING:
    from app.models.media_asset import MediaAsset


class StorageObjectStatus(enum.StrEnum):
    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


class StorageObject(Base):
    __tablename__ = "storage_objects"
    __table_args__ = (
        UniqueConstraint("bucket_name", "object_key", name="uq_bucket_object_key"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
    )

    object_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    bucket_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Internal object key, not a public URL. Presigned links are generated
    # on demand and never stored here.
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # We always compute SHA-256 ourselves; the MinIO ETag is intentionally
    # not treated as a checksum (it depends on the upload method).
    checksum_algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    checksum: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[StorageObjectStatus] = mapped_column(
        SAEnum(
            StorageObjectStatus,
            name="storage_object_status",
            values_callable=enum_values,
        ),
        nullable=False,
        default=StorageObjectStatus.PENDING,
        server_default=StorageObjectStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    media_assets: Mapped[list[MediaAsset]] = relationship(
        "MediaAsset",
        back_populates="object",
    )
