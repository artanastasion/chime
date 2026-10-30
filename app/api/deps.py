"""FastAPI dependencies: per-request session and service providers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import async_session_factory
from app.services.catalog import CatalogService
from app.services.media_assets import MediaService
from app.storage.client import storage_client
from app.storage.service import StorageService


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request.

    Services (MediaService, CatalogService) own the transaction boundary
    — they commit on success and roll back on failure — so this
    dependency only opens and closes the session and never commits.
    """
    async with async_session_factory() as session:
        yield session


def get_catalog_service(
    session: AsyncSession = Depends(get_session),
) -> CatalogService:
    """CatalogService bound to the request session."""
    return CatalogService(session)


def get_media_service(
    session: AsyncSession = Depends(get_session),
) -> MediaService:
    """MediaService bound to the request session and the shared client.

    StorageService instances are cheap stateless wrappers around the
    shared MinIO client, so creating one per request is safe.
    """
    storage = StorageService(storage_client, settings.storage_bucket)
    return MediaService(session, storage)
