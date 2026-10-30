"""Artist (performer or musical project).

This table describes the performing side only; rights holders are
modelled separately and are out of scope for the catalog layer.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, enum_values

# Forward references only: relationships resolve string names via the
# declarative registry, so no runtime import (would be circular).
if TYPE_CHECKING:
    from app.models.track import Track
    from app.models.track_artist import TrackArtist


class ArtistStatus(enum.StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class Artist(Base):
    __tablename__ = "artists"

    artist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ArtistStatus] = mapped_column(
        SAEnum(ArtistStatus, name="artist_status", values_callable=enum_values),
        nullable=False,
        default=ArtistStatus.DRAFT,
        server_default=ArtistStatus.DRAFT.value,
    )
    # Spec defines created_at only (no updated_at) for artists.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    track_links: Mapped[list[TrackArtist]] = relationship(
        "TrackArtist",
        back_populates="artist",
        passive_deletes=True,
    )
    tracks: Mapped[list[Track]] = relationship(
        "Track",
        secondary="track_artists",
        viewonly=True,
    )
