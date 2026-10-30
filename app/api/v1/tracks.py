"""Track endpoints (including track-artist links)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_catalog_service
from app.models import TrackStatus
from app.schemas.common import Page, Pagination
from app.schemas.track import (
    ArtistInTrack,
    TrackAlbumBrief,
    TrackArtistAdd,
    TrackArtistOut,
    TrackCreate,
    TrackDetail,
    TrackOut,
    TrackUpdate,
)
from app.services.catalog import CatalogService

router = APIRouter(prefix="/tracks", tags=["Tracks"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TrackOut)
async def create_track(
    payload: TrackCreate,
    service: CatalogService = Depends(get_catalog_service),
) -> TrackOut:
    return TrackOut.model_validate(await service.create_track(payload))


@router.get("", response_model=Page[TrackOut])
async def list_tracks(
    service: CatalogService = Depends(get_catalog_service),
    pagination: Pagination = Depends(),
    track_status: TrackStatus | None = Query(
        default=None, alias="status", description="Filter by status"
    ),
    album_id: uuid.UUID | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(
        default=None, description="Tracks featuring the artist"
    ),
    explicit: bool | None = Query(default=None),
    title_icontains: str | None = Query(
        default=None, max_length=300, description="Case-insensitive title substring"
    ),
) -> Page[TrackOut]:
    items, total = await service.list_tracks(
        status=track_status,
        album_id=album_id,
        artist_id=artist_id,
        explicit=explicit,
        title_icontains=title_icontains,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[TrackOut.model_validate(t) for t in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{track_id}", response_model=TrackDetail)
async def get_track(
    track_id: uuid.UUID,
    service: CatalogService = Depends(get_catalog_service),
) -> TrackDetail:
    track = await service.get_track(track_id)
    links = await service.list_track_artists(track_id)
    return TrackDetail(
        **TrackOut.model_validate(track).model_dump(),
        album=(
            TrackAlbumBrief.model_validate(track.album)
            if track.album is not None
            else None
        ),
        artists=[
            ArtistInTrack(
                track_artist_id=link.track_artist_id,
                artist_id=artist.artist_id,
                name=artist.name,
                role=link.role,
                display_order=link.display_order,
            )
            for link, artist in links
        ],
    )


@router.patch("/{track_id}", response_model=TrackOut)
async def update_track(
    track_id: uuid.UUID,
    payload: TrackUpdate,
    service: CatalogService = Depends(get_catalog_service),
) -> TrackOut:
    return TrackOut.model_validate(await service.update_track(track_id, payload))


@router.post(
    "/{track_id}/artists",
    status_code=status.HTTP_201_CREATED,
    response_model=TrackArtistOut,
)
async def add_track_artist(
    track_id: uuid.UUID,
    payload: TrackArtistAdd,
    service: CatalogService = Depends(get_catalog_service),
) -> TrackArtistOut:
    link = await service.add_track_artist(
        track_id,
        artist_id=payload.artist_id,
        role=payload.role,
        display_order=payload.display_order,
    )
    return TrackArtistOut.model_validate(link)


@router.delete(
    "/{track_id}/artists/{track_artist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_track_artist(
    track_id: uuid.UUID,
    track_artist_id: uuid.UUID,
    service: CatalogService = Depends(get_catalog_service),
) -> None:
    await service.remove_track_artist(track_id, track_artist_id)
