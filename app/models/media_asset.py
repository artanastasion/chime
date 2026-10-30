"""MediaAsset: logical media resource linked to a RustFS object.

AUDIO / LYRICS / COVER are the supported asset types. The binary file is
referenced through STORAGE_OBJECTS; catalog rules (which target an asset
may reference) are enforced by CHECK constraints below and by the
application service layer.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

# Forward references only: relationships resolve string names via the
# declarative registry, so no runtime import (would be circular).
if TYPE_CHECKING:
    from app.models.album import Album
    from app.models.storage_object import StorageObject
    from app.models.track import Track


class MediaAssetType(enum.StrEnum):
    AUDIO = "audio"
    LYRICS = "lyrics"
    COVER = "cover"


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        # Exactly one catalog target must be set.
        CheckConstraint(
            "(track_id IS NOT NULL)::int + (album_id IS NOT NULL)::int = 1",
            name="single_catalog_target",
        ),
        # Spec rules 1-4: audio and lyrics belong to a track; a cover
        # belongs either to a track or to an album.
        CheckConstraint(
            "(asset_type IN ('audio', 'lyrics') AND track_id IS NOT NULL)"
            " OR (asset_type = 'cover' AND (track_id IS NOT NULL"
            " OR album_id IS NOT NULL))",
            name="type_target_compatible",
        ),
        CheckConstraint(
            "audio_bitrate IS NULL OR audio_bitrate > 0",
            name="audio_bitrate_positive",
        ),
        # Strict one-object-per-asset model: a binary in RustFS is owned
        # by exactly one media asset, so deleting an asset can never
        # destroy a binary still referenced by another asset.
        UniqueConstraint("object_id", name="uq_media_assets_object_id"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tracks.track_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    album_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("albums.album_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_objects.object_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_type: Mapped[MediaAssetType] = mapped_column(
        SAEnum(MediaAssetType, name="media_asset_type", values_callable=enum_values),
        nullable=False,
    )
    # Only relevant for lyrics (original lyrics only, no translations).
    language: Mapped[str | None] = mapped_column(String(20))
    # Only relevant for audio renditions.
    audio_format: Mapped[str | None] = mapped_column(String(30))
    audio_bitrate: Mapped[int | None] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    track: Mapped[Track | None] = relationship("Track", back_populates="media_assets")
    album: Mapped[Album | None] = relationship("Album", back_populates="media_assets")
    object: Mapped[StorageObject] = relationship(
        "StorageObject", back_populates="media_assets"
    )
