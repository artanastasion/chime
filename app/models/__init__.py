"""ORM models of the music catalog.

Importing this package registers every model on the shared
``Base.metadata``, which is what Alembic autogeneration uses.
"""

from app.db.base import Base
from app.models.album import Album, AlbumStatus, AlbumType
from app.models.artist import Artist, ArtistStatus
from app.models.media_asset import MediaAsset, MediaAssetType
from app.models.storage_object import StorageObject, StorageObjectStatus
from app.models.track import Track, TrackStatus
from app.models.track_artist import ArtistRole, TrackArtist
from app.models.user import User

__all__ = [
    "Album",
    "AlbumStatus",
    "AlbumType",
    "Artist",
    "ArtistRole",
    "ArtistStatus",
    "Base",
    "MediaAsset",
    "MediaAssetType",
    "StorageObject",
    "StorageObjectStatus",
    "Track",
    "TrackArtist",
    "TrackStatus",
    "User",
]

