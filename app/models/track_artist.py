"""TrackArtists: many-to-many link between tracks and performers."""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

if TYPE_CHECKING:
    from app.models.artist import Artist
    from app.models.track import Track


class ArtistRole(enum.StrEnum):
    PRIMARY = "primary"
    FEATURED = "featured"
    REMIXER = "remixer"
    COMPOSER = "composer"
    PRODUCER = "producer"


class TrackArtist(Base):
    __tablename__ = "track_artists"
    __table_args__ = (
        # One artist may hold several roles in the same track, but the same
        # (track, artist, role) combination must not repeat.
        UniqueConstraint("track_id", "artist_id", "role", name="uq_track_artist_role"),
    )

    # Separate UUID primary key (recommended for the first version) keeps
    # the model open for future extensions.
    track_artist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    track_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tracks.track_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artists.artist_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[ArtistRole] = mapped_column(
        SAEnum(ArtistRole, name="artist_role", values_callable=enum_values),
        nullable=False,
    )
    display_order: Mapped[int | None] = mapped_column(Integer)

    track: Mapped[Track] = relationship("Track", back_populates="artist_links")
    artist: Mapped[Artist] = relationship("Artist", back_populates="track_links")
