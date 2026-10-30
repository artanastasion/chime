"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import albums, artists, media, tracks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(artists.router)
api_router.include_router(albums.router)
api_router.include_router(tracks.router)
api_router.include_router(media.router)


