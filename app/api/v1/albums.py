"""Album endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_catalog_service
from app.models import AlbumStatus, AlbumType
from app.schemas.album import (
    AlbumArtistBrief,
    AlbumCreate,
    AlbumDetail,
    AlbumOut,
    AlbumTrackBrief,
    AlbumUpdate,
)
from app.schemas.common import Page, Pagination
from app.services.catalog import CatalogService

router = APIRouter(prefix="/albums", tags=["Albums"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AlbumOut)
async def create_album(
    payload: AlbumCreate,
    service: CatalogService = Depends(get_catalog_service),
) -> AlbumOut:
    return AlbumOut.model_validate(await service.create_album(payload))


@router.get("", response_model=Page[AlbumOut])
async def list_albums(
    service: CatalogService = Depends(get_catalog_service),
    pagination: Pagination = Depends(),
    album_status: AlbumStatus | None = Query(
        default=None, alias="status", description="Filter by status"
    ),
    album_type: AlbumType | None = Query(
        default=None, description="Filter by release type"
    ),
    artist_id: uuid.UUID | None = Query(
        default=None,
        description="Albums featuring the artist (derived via tracks)",
    ),
) -> Page[AlbumOut]:
    items, total = await service.list_albums(
        status=album_status,
        album_type=album_type,
        artist_id=artist_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[AlbumOut.model_validate(a) for a in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{album_id}", response_model=AlbumDetail)
async def get_album(
    album_id: uuid.UUID,
    service: CatalogService = Depends(get_catalog_service),
) -> AlbumDetail:
    album = await service.get_album(album_id)
    tracks = await service.get_album_tracks(album_id)
    artists = await service.get_album_artists(album_id)
    return AlbumDetail(
        **AlbumOut.model_validate(album).model_dump(),
        tracks=[AlbumTrackBrief.model_validate(t) for t in tracks],
        artists=[AlbumArtistBrief.model_validate(a) for a in artists],
    )


@router.patch("/{album_id}", response_model=AlbumOut)
async def update_album(
    album_id: uuid.UUID,
    payload: AlbumUpdate,
    service: CatalogService = Depends(get_catalog_service),
) -> AlbumOut:
    return AlbumOut.model_validate(await service.update_album(album_id, payload))
