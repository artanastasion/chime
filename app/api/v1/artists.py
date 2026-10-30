"""Artist endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_catalog_service
from app.models import ArtistStatus
from app.schemas.artist import ArtistCreate, ArtistOut, ArtistUpdate
from app.schemas.common import Page, Pagination
from app.services.catalog import CatalogService

router = APIRouter(prefix="/artists", tags=["Artists"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ArtistOut)
async def create_artist(
    payload: ArtistCreate,
    service: CatalogService = Depends(get_catalog_service),
) -> ArtistOut:
    return ArtistOut.model_validate(await service.create_artist(payload))


@router.get("", response_model=Page[ArtistOut])
async def list_artists(
    service: CatalogService = Depends(get_catalog_service),
    pagination: Pagination = Depends(),
    artist_status: ArtistStatus | None = Query(
        default=None, alias="status", description="Filter by status"
    ),
    name_icontains: str | None = Query(
        default=None, max_length=300, description="Case-insensitive name substring"
    ),
) -> Page[ArtistOut]:
    items, total = await service.list_artists(
        status=artist_status,
        name_icontains=name_icontains,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[ArtistOut.model_validate(a) for a in items],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/{artist_id}", response_model=ArtistOut)
async def get_artist(
    artist_id: uuid.UUID,
    service: CatalogService = Depends(get_catalog_service),
) -> ArtistOut:
    return ArtistOut.model_validate(await service.get_artist(artist_id))


@router.patch("/{artist_id}", response_model=ArtistOut)
async def update_artist(
    artist_id: uuid.UUID,
    payload: ArtistUpdate,
    service: CatalogService = Depends(get_catalog_service),
) -> ArtistOut:
    return ArtistOut.model_validate(
        await service.update_artist(artist_id, payload)
    )
