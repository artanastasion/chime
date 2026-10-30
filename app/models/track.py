"""Track: the central catalog entity."""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Uuid, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, enum_values

if TYPE_CHECKING:
    from app.models.album import Album
    from app.models.artist import Artist
    from app.models.media_asset import MediaAsset
    from app.models.track_artist import TrackArtist


class TrackStatus(enum.StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class Track(TimestampMixin, Base):
    __tablename__ = "tracks"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
        CheckConstraint(
            "track_number IS NULL OR track_number > 0", name="track_number_positive"
        ),
    )

    track_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    # NULL means a standalone track not tied to a release. "Every published
    # track belongs to an album" is a business rule checked at publication.
    album_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("albums.album_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    track_number: Mapped[int | None] = mapped_column(Integer)
    explicit_content: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    status: Mapped[TrackStatus] = mapped_column(
        SAEnum(TrackStatus, name="track_status", values_callable=enum_values),
        nullable=False,
        default=TrackStatus.DRAFT,
        server_default=TrackStatus.DRAFT.value,
        index=True,
    )

    album: Mapped[Album | None] = relationship("Album", back_populates="tracks")
    artist_links: Mapped[list[TrackArtist]] = relationship(
        "TrackArtist",
        back_populates="track",
        passive_deletes=True,
    )
    artists: Mapped[list[Artist]] = relationship(
        "Artist",
        secondary="track_artists",
        viewonly=True,
    )
    media_assets: Mapped[list[MediaAsset]] = relationship(
        "MediaAsset",
        back_populates="track",
    )

