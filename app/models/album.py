"""Album (release) metadata."""

from __future__ import annotations

import enum
import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, enum_values

# Forward references only: relationships resolve string names via the
# declarative registry, so no runtime import (would be circular).
if TYPE_CHECKING:
    from app.models.media_asset import MediaAsset
    from app.models.track import Track


class AlbumType(enum.StrEnum):
    ALBUM = "album"
    SINGLE = "single"
    EP = "ep"
    COMPILATION = "compilation"


class AlbumStatus(enum.StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class Album(TimestampMixin, Base):
    __tablename__ = "albums"

    album_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date)
    album_type: Mapped[AlbumType] = mapped_column(
        SAEnum(AlbumType, name="album_type", values_callable=enum_values),
        nullable=False,
        default=AlbumType.ALBUM,
        server_default=AlbumType.ALBUM.value,
    )
    status: Mapped[AlbumStatus] = mapped_column(
        SAEnum(AlbumStatus, name="album_status", values_callable=enum_values),
        nullable=False,
        default=AlbumStatus.DRAFT,
        server_default=AlbumStatus.DRAFT.value,
    )
    # The cover image itself is not stored here: it is linked through
    # MEDIA_ASSETS / STORAGE_OBJECTS.

    tracks: Mapped[list[Track]] = relationship(
        "Track",
        back_populates="album",
        passive_deletes=True,
    )
    media_assets: Mapped[list[MediaAsset]] = relationship(
        "MediaAsset",
        back_populates="album",
    )
