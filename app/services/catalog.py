"""Catalog CRUD operations: artists, albums, tracks and track-artist links.

Follows the same transaction contract as MediaService: every public
mutating method commits on success and rolls back on failure. Callers
(including the HTTP layer) must not keep uncommitted changes in the
session across service calls.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    Album,
    AlbumStatus,
    AlbumType,
    Artist,
    ArtistRole,
    ArtistStatus,
    Track,
    TrackArtist,
    TrackStatus,
)
from app.schemas.album import AlbumCreate, AlbumUpdate
from app.schemas.artist import ArtistCreate, ArtistUpdate
from app.schemas.track import TrackCreate, TrackUpdate
from app.services.errors import (
    CatalogEntityNotFoundError,
    DuplicateLinkError,
    InvalidReferenceError,
)

logger = logging.getLogger(__name__)


def _icontains(value: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class CatalogService:
    """CRUD operations over the music catalog (HTTP-agnostic)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- shared helpers -------------------------------------------------

    async def _require(
        self,
        model: type,
        entity_id: uuid.UUID,
        *,
        label: str,
    ) -> Any:
        """Fetch an entity addressed through the URL or raise 404 error."""
        instance = await self._session.get(model, entity_id)
        if instance is None:
            raise CatalogEntityNotFoundError(f"{label} {entity_id} not found")
        return instance

    # -- artists ----------------------------------------------------------

    async def create_artist(self, data: ArtistCreate) -> Artist:
        artist = Artist(
            name=data.name,
            description=data.description,
            status=data.status,
        )
        self._session.add(artist)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(artist)
        return artist

    async def list_artists(
        self,
        *,
        status: ArtistStatus | None = None,
        name_icontains: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[Artist], int]:
        stmt = select(Artist)
        if status is not None:
            stmt = stmt.where(Artist.status == status)
        if name_icontains:
            stmt = stmt.where(
                Artist.name.ilike(_icontains(name_icontains), escape="\\")
            )
        total = await self._session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        stmt = stmt.order_by(Artist.created_at.desc(), Artist.artist_id)
        items = (
            (await self._session.execute(stmt.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return list(items), total or 0

    async def get_artist(self, artist_id: uuid.UUID) -> Artist:
        return await self._require(Artist, artist_id, label="artist")

    async def update_artist(
        self, artist_id: uuid.UUID, data: ArtistUpdate
    ) -> Artist:
        artist = await self.get_artist(artist_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(artist, field, value)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(artist)
        return artist

    # -- albums ------------------------------------------------------------

    async def create_album(self, data: AlbumCreate) -> Album:
        album = Album(
            title=data.title,
            release_date=data.release_date,
            album_type=data.album_type,
            status=data.status,
        )
        self._session.add(album)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(album)
        return album

    async def list_albums(
        self,
        *,
        status: AlbumStatus | None = None,
        album_type: AlbumType | None = None,
        artist_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[Album], int]:
        stmt = select(Album)
        if artist_id is not None:
            # Albums are linked to artists only through their tracks;
            # DISTINCT keeps one album per row even when the artist has
            # several roles/tracks in it.
            stmt = (
                stmt.join(Track, Track.album_id == Album.album_id)
                .join(TrackArtist, TrackArtist.track_id == Track.track_id)
                .where(TrackArtist.artist_id == artist_id)
                .distinct()
            )
        if status is not None:
            stmt = stmt.where(Album.status == status)
        if album_type is not None:
            stmt = stmt.where(Album.album_type == album_type)
        total = await self._session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        stmt = stmt.order_by(Album.created_at.desc(), Album.album_id)
        items = (
            (await self._session.execute(stmt.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return list(items), total or 0

    async def get_album(self, album_id: uuid.UUID) -> Album:
        return await self._require(Album, album_id, label="album")

    async def get_album_tracks(self, album_id: uuid.UUID) -> list[Track]:
        await self.get_album(album_id)
        stmt = (
            select(Track)
            .where(Track.album_id == album_id)
            .order_by(
                Track.track_number.asc().nullslast(),
                Track.created_at.desc(),
                Track.track_id,
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_album_artists(self, album_id: uuid.UUID) -> list[Artist]:
        """Distinct artists of an album, derived through its tracks."""
        stmt = (
            select(Artist)
            .join(TrackArtist, TrackArtist.artist_id == Artist.artist_id)
            .join(Track, Track.track_id == TrackArtist.track_id)
            .where(Track.album_id == album_id)
            .distinct()
            .order_by(Artist.name, Artist.artist_id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def update_album(
        self, album_id: uuid.UUID, data: AlbumUpdate
    ) -> Album:
        album = await self.get_album(album_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(album, field, value)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(album)
        return album

    # -- tracks ------------------------------------------------------------

    async def _check_album_reference(self, album_id: uuid.UUID | None) -> None:
        """Body reference contract: unknown album_id -> 422."""
        if album_id is not None and await self._session.get(Album, album_id) is None:
            raise InvalidReferenceError(f"album {album_id} does not exist")

    async def create_track(self, data: TrackCreate) -> Track:
        await self._check_album_reference(data.album_id)
        track = Track(
            title=data.title,
            duration_seconds=data.duration_seconds,
            album_id=data.album_id,
            track_number=data.track_number,
            explicit_content=data.explicit_content,
            status=data.status,
        )
        self._session.add(track)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(track)
        return track

    async def list_tracks(
        self,
        *,
        status: TrackStatus | None = None,
        album_id: uuid.UUID | None = None,
        artist_id: uuid.UUID | None = None,
        explicit: bool | None = None,
        title_icontains: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[Track], int]:
        stmt = select(Track)
        if artist_id is not None:
            # DISTINCT: one track row even if the artist holds several
            # roles in it (UNIQUE is on (track, artist, role)).
            stmt = (
                stmt.join(TrackArtist, TrackArtist.track_id == Track.track_id)
                .where(TrackArtist.artist_id == artist_id)
                .distinct()
            )
        if status is not None:
            stmt = stmt.where(Track.status == status)
        if album_id is not None:
            stmt = stmt.where(Track.album_id == album_id)
        if explicit is not None:
            stmt = stmt.where(Track.explicit_content == explicit)
        if title_icontains:
            stmt = stmt.where(
                Track.title.ilike(_icontains(title_icontains), escape="\\")
            )
        total = await self._session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        stmt = stmt.order_by(Track.created_at.desc(), Track.track_id)
        items = (
            (await self._session.execute(stmt.limit(limit).offset(offset)))
            .scalars()
            .all()
        )
        return list(items), total or 0

    async def get_track(self, track_id: uuid.UUID) -> Track:
        """Track with its album eagerly loaded (for detail responses)."""
        stmt = (
            select(Track)
            .where(Track.track_id == track_id)
            .options(joinedload(Track.album))
        )
        track = (await self._session.execute(stmt)).scalars().one_or_none()
        if track is None:
            raise CatalogEntityNotFoundError(f"track {track_id} not found")
        return track

    async def update_track(
        self, track_id: uuid.UUID, data: TrackUpdate
    ) -> Track:
        track = await self.get_track(track_id)
        changes = data.model_dump(exclude_unset=True)
        # album_id may be explicitly set to None (detach from album).
        if changes.get("album_id") is not None:
            await self._check_album_reference(changes["album_id"])
        for field, value in changes.items():
            setattr(track, field, value)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        # Full refresh: refreshing a subset would expire all other
        # attributes, and accessing them later would trigger a lazy load
        # outside the async context.
        await self._session.refresh(track)
        return track

    # -- track-artist links --------------------------------------------------

    async def list_track_artists(
        self, track_id: uuid.UUID
    ) -> list[tuple[TrackArtist, Artist]]:
        """Links of a track with the artist rows, in display order."""
        await self.get_track(track_id)
        stmt = (
            select(TrackArtist, Artist)
            .join(Artist, Artist.artist_id == TrackArtist.artist_id)
            .where(TrackArtist.track_id == track_id)
            .order_by(
                TrackArtist.display_order.asc().nullslast(),
                Artist.name,
                TrackArtist.track_artist_id,
            )
        )
        return [
            (link, artist)
            for link, artist in (await self._session.execute(stmt)).all()
        ]

    async def add_track_artist(
        self,
        track_id: uuid.UUID,
        *,
        artist_id: uuid.UUID,
        role: ArtistRole,
        display_order: int | None,
    ) -> TrackArtist:
        await self._require(Track, track_id, label="track")
        if await self._session.get(Artist, artist_id) is None:
            raise InvalidReferenceError(f"artist {artist_id} does not exist")

        duplicate = await self._session.scalar(
            select(TrackArtist).where(
                TrackArtist.track_id == track_id,
                TrackArtist.artist_id == artist_id,
                TrackArtist.role == role,
            )
        )
        if duplicate is not None:
            raise DuplicateLinkError(
                f"artist {artist_id} already has role '{role.value}' "
                f"on track {track_id}"
            )

        link = TrackArtist(
            track_id=track_id,
            artist_id=artist_id,
            role=role,
            display_order=display_order,
        )
        self._session.add(link)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            # Concurrent insert hit uq_track_artist_role.
            raise DuplicateLinkError(
                f"artist {artist_id} already has role '{role.value}' "
                f"on track {track_id}"
            ) from None
        except Exception:
            await self._session.rollback()
            raise
        await self._session.refresh(link)
        return link

    async def remove_track_artist(
        self, track_id: uuid.UUID, track_artist_id: uuid.UUID
    ) -> None:
        """Delete one specific link identified by its unambiguous id."""
        await self._require(Track, track_id, label="track")
        link = await self._session.scalar(
            select(TrackArtist).where(
                TrackArtist.track_artist_id == track_artist_id,
                TrackArtist.track_id == track_id,
            )
        )
        if link is None:
            raise CatalogEntityNotFoundError(
                f"track artist link {track_artist_id} not found "
                f"on track {track_id}"
            )
        await self._session.delete(link)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise



