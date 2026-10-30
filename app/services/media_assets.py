"""Coordination between PostgreSQL and the RustFS object storage.

Implements the two-step media model of the spec:

1. ``STORAGE_OBJECTS`` registers the binary object uploaded to RustFS;
2. ``MEDIA_ASSETS`` links the registered object to a catalog entity
   (a track or an album).

Concurrency and consistency model:

* the ``(bucket_name, object_key)`` slot is *reserved* by inserting a
  ``pending`` row BEFORE any upload — the UNIQUE constraint arbitrates
  concurrent uploads atomically, so only one worker ever writes to a
  given S3 key and silent overwrites are impossible;
* if a step fails after the binary was uploaded, the service
  compensates: the object is removed from RustFS and its row deleted;
  when even the compensation fails, the row is marked ``failed`` (and
  the leftover binary logged as ORPHANED OBJECT), which makes every
  inconsistent state discoverable via ``status != 'available'``;
* a storage object is owned by at most one media asset
  (UNIQUE ``media_assets.object_id``), so deleting an asset can never
  destroy a binary referenced by another asset.

Transaction contract: a ``MediaService`` instance owns the transaction
boundary of the session passed to it — every public mutating method
commits on success and rolls back on failure. Callers must not keep
uncommitted changes in that session across service calls and must not
share one session between concurrent tasks.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    Album,
    MediaAsset,
    MediaAssetType,
    StorageObject,
    StorageObjectStatus,
    Track,
)
from app.services.errors import (
    AssetNotFoundError,
    CatalogEntityNotFoundError,
    MediaStateError,
    MediaValidationError,
    SlotConflictError,
)
from app.storage.service import StorageService

logger = logging.getLogger(__name__)

KEY_ROOT = "music"
CHECKSUM_ALGORITHM = "sha256"
_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,8}$")
# Formats that may be used in rendition keys; extensible on demand.
ALLOWED_AUDIO_FORMATS = frozenset({"flac", "mp3", "wav", "aac", "ogg", "opus", "m4a"})


def _safe_extension(filename: str | None) -> str:
    """Extract a safe lower-case extension from a user-supplied filename."""
    if not filename or "." not in filename:
        return ""
    candidate = filename.rsplit(".", 1)[-1].strip().lower()
    return candidate if _EXTENSION_RE.match(candidate) else ""


def build_audio_original_key(
    track_id: uuid.UUID,
    original_filename: str | None = None,
) -> str:
    """``music/audio/{track_id}/original/source_audio.<ext>``."""
    extension = _safe_extension(original_filename)
    name = f"source_audio.{extension}" if extension else "source_audio"
    return f"{KEY_ROOT}/audio/{track_id}/original/{name}"


def build_audio_rendition_key(
    track_id: uuid.UUID,
    audio_format: str,
    bitrate: int,
) -> str:
    """``music/audio/{track_id}/renditions/{format}_{bitrate}.<ext>``."""
    fmt = audio_format.strip().lower()
    return f"{KEY_ROOT}/audio/{track_id}/renditions/{fmt}_{int(bitrate)}.{fmt}"


def build_lyrics_key(track_id: uuid.UUID) -> str:
    """``music/lyrics/{track_id}/original.txt`` (UTF-8 text only)."""
    return f"{KEY_ROOT}/lyrics/{track_id}/original.txt"


def build_cover_key(
    *,
    track_id: uuid.UUID | None = None,
    album_id: uuid.UUID | None = None,
    original_filename: str | None = None,
) -> str:
    """``music/covers/{tracks|albums}/{id}/cover.<ext>``."""
    extension = _safe_extension(original_filename)
    name = f"cover.{extension}" if extension else "cover"
    if track_id is not None:
        return f"{KEY_ROOT}/covers/tracks/{track_id}/{name}"
    return f"{KEY_ROOT}/covers/albums/{album_id}/{name}"


class MediaService:
    """Upload/download/remove media and keep PostgreSQL and RustFS in sync."""

    def __init__(self, session: AsyncSession, storage: StorageService) -> None:
        self._session = session
        self._storage = storage

    # -- step 1: register the binary object ------------------------------

    async def register_object(
        self,
        *,
        content: bytes | BinaryIO,
        object_key: str,
        mime_type: str,
    ) -> StorageObject:
        """Upload bytes to RustFS and register them in STORAGE_OBJECTS.

        The slot is reserved (``pending`` row) BEFORE the upload: the
        UNIQUE constraint on (bucket_name, object_key) atomically
        arbitrates concurrent uploads, so exactly one worker ever
        performs ``put_object`` for a key and an existing binary can
        never be silently overwritten.

        Failure handling:
        * reservation conflict -> ValueError, nothing uploaded;
        * upload failure -> the reservation row is released;
        * finalisation failure -> the uploaded binary and the row are
          removed; if that compensation fails, the row is marked
          ``failed`` and the binary logged as an ORPHANED OBJECT.
        """
        if isinstance(content, (bytes, bytearray)) and not content:
            raise MediaValidationError("content must not be empty")

        # -- 1. reserve the slot atomically -----------------------------
        reservation = StorageObject(
            bucket_name=self._storage.bucket,
            object_key=object_key,
            mime_type=mime_type,
            size_bytes=0,
            checksum_algorithm=CHECKSUM_ALGORITHM,
            checksum="",
            status=StorageObjectStatus.PENDING,
        )
        self._session.add(reservation)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise SlotConflictError(
                f"object slot '{object_key}' is already registered or "
                f"reserved by a concurrent upload"
            ) from None
        except Exception:
            await self._session.rollback()
            raise

        # -- 2. upload the binary ----------------------------------------
        try:
            upload = await self._storage.upload(
                content=content,
                object_key=object_key,
                mime_type=mime_type,
            )
        except Exception:
            await self._release_reservation(reservation, object_key=object_key)
            raise
        if upload.size_bytes == 0:
            # Streams are only measurable after the read pass; discard
            # the just-uploaded empty binary the same way.
            logger.warning("Rejected empty content for '%s'", object_key)
            await self._discard_uploaded_object(
                reservation,
                object_key=object_key,
                bucket_name=self._storage.bucket,
            )
            raise MediaValidationError("content must not be empty")

        # -- 3. finalise the registration ----------------------------------
        reservation.size_bytes = upload.size_bytes
        reservation.checksum = upload.checksum_sha256
        reservation.status = StorageObjectStatus.AVAILABLE
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.exception(
                "Finalising registration of '%s' failed; compensating",
                object_key,
            )
            await self._discard_uploaded_object(
                reservation,
                object_key=object_key,
                bucket_name=self._storage.bucket,
            )
            raise
        await self._session.refresh(reservation)
        return reservation

    # -- step 2: link the object to the catalog --------------------------

    async def create_asset(
        self,
        storage_object: StorageObject,
        asset_type: MediaAssetType,
        *,
        track_id: uuid.UUID | None = None,
        album_id: uuid.UUID | None = None,
        language: str | None = None,
        audio_format: str | None = None,
        audio_bitrate: int | None = None,
        is_primary: bool = False,
    ) -> MediaAsset:
        """Create a MEDIA_ASSETS row linking the object to a catalog entity."""
        if storage_object.status != StorageObjectStatus.AVAILABLE:
            raise MediaStateError(
                f"storage object '{storage_object.object_key}' is not "
                f"available (status={storage_object.status.value})"
            )
        self._validate_target(asset_type, track_id=track_id, album_id=album_id)
        self._validate_payload(
            asset_type,
            mime_type=storage_object.mime_type,
            language=language,
            audio_format=audio_format,
            audio_bitrate=audio_bitrate,
        )
        await self._ensure_entities_exist(
            asset_type, track_id=track_id, album_id=album_id
        )
        asset = MediaAsset(
            track_id=track_id,
            album_id=album_id,
            object_id=storage_object.object_id,
            asset_type=asset_type,
            language=language,
            audio_format=audio_format,
            audio_bitrate=audio_bitrate,
            is_primary=is_primary,
        )
        self._session.add(asset)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        # Eagerly load the storage object so callers can read
        # asset.object without a lazy load in async context.
        await self._session.refresh(asset, attribute_names=["object"])
        return asset

    async def _attach(
        self,
        storage_object: StorageObject,
        asset_type: MediaAssetType,
        **kwargs: object,
    ) -> MediaAsset:
        """create_asset + compensation: the object registered by the
        enclosing composite upload exists solely for this asset, so a
        failure to attach it discards both the binary and its row."""
        # Capture the coordinates before any failure: a rollback expires
        # the ORM instance and attribute access would then require IO.
        object_key = storage_object.object_key
        bucket_name = storage_object.bucket_name
        try:
            return await self.create_asset(storage_object, asset_type, **kwargs)
        except Exception:
            logger.exception(
                "Creating %s asset for object '%s' failed; compensating "
                "by discarding the uploaded object",
                asset_type.value,
                object_key,
            )
            await self._discard_uploaded_object(
                storage_object,
                object_key=object_key,
                bucket_name=bucket_name,
            )
            raise

    # -- high-level operations ---------------------------------------------

    async def upload_track_audio(
        self,
        track_id: uuid.UUID,
        content: bytes | BinaryIO,
        *,
        mime_type: str,
        original_filename: str | None = None,
        audio_format: str | None = None,
        audio_bitrate: int | None = None,
        is_primary: bool = False,
    ) -> MediaAsset:
        """Upload audio for a track.

        Without ``audio_bitrate`` the file lands in the ``original/``
        slot; with both ``audio_format`` and ``audio_bitrate`` it is
        stored as a rendition (several quality levels may coexist).
        """
        # Validate BEFORE reserving/uploading so invalid input leaves
        # neither DB rows nor S3 objects.
        self._validate_payload(
            MediaAssetType.AUDIO,
            mime_type=mime_type,
            language=None,
            audio_format=audio_format,
            audio_bitrate=audio_bitrate,
        )
        await self._check_target(
            MediaAssetType.AUDIO, track_id=track_id, album_id=None
        )
        if audio_bitrate is None:
            object_key = build_audio_original_key(track_id, original_filename)
        else:
            object_key = build_audio_rendition_key(
                track_id, audio_format, audio_bitrate
            )
        storage_object = await self.register_object(
            content=content, object_key=object_key, mime_type=mime_type
        )
        return await self._attach(
            storage_object,
            MediaAssetType.AUDIO,
            track_id=track_id,
            audio_format=audio_format,
            audio_bitrate=audio_bitrate,
            is_primary=is_primary,
        )

    async def upload_lyrics(
        self,
        track_id: uuid.UUID,
        content: bytes | str,
        *,
        language: str | None = None,
    ) -> MediaAsset:
        """Upload the original lyrics (UTF-8 text; no translations)."""
        # Validate BEFORE reserving/uploading so invalid input leaves
        # neither DB rows nor S3 objects.
        self._validate_payload(
            MediaAssetType.LYRICS,
            mime_type="text/plain; charset=utf-8",
            language=language,
            audio_format=None,
            audio_bitrate=None,
        )
        await self._check_target(
            MediaAssetType.LYRICS, track_id=track_id, album_id=None
        )
        if isinstance(content, str):
            content = content.encode("utf-8")
        storage_object = await self.register_object(
            content=content,
            object_key=build_lyrics_key(track_id),
            mime_type="text/plain; charset=utf-8",
        )
        return await self._attach(
            storage_object,
            MediaAssetType.LYRICS,
            track_id=track_id,
            language=language,
        )

    async def upload_cover(
        self,
        *,
        content: bytes | BinaryIO,
        mime_type: str,
        track_id: uuid.UUID | None = None,
        album_id: uuid.UUID | None = None,
        original_filename: str | None = None,
    ) -> MediaAsset:
        """Upload a cover for a track or for an album (exactly one of them)."""
        # Validate BEFORE reserving/uploading so invalid input leaves
        # neither DB rows nor S3 objects.
        self._validate_payload(
            MediaAssetType.COVER,
            mime_type=mime_type,
            language=None,
            audio_format=None,
            audio_bitrate=None,
        )
        await self._check_target(
            MediaAssetType.COVER, track_id=track_id, album_id=album_id
        )
        object_key = build_cover_key(
            track_id=track_id,
            album_id=album_id,
            original_filename=original_filename,
        )
        storage_object = await self.register_object(
            content=content, object_key=object_key, mime_type=mime_type
        )
        return await self._attach(
            storage_object,
            MediaAssetType.COVER,
            track_id=track_id,
            album_id=album_id,
        )

    # -- read / delete ------------------------------------------------------

    async def get_asset(self, asset_id: uuid.UUID) -> MediaAsset:
        """Return the asset with its storage object eagerly loaded."""
        asset = (
            await self._session.execute(
                select(MediaAsset)
                .options(joinedload(MediaAsset.object))
                .where(MediaAsset.asset_id == asset_id)
            )
        ).scalar_one_or_none()
        if asset is None:
            raise AssetNotFoundError(f"media asset {asset_id} not found")
        return asset

    async def list_track_assets(
        self,
        track_id: uuid.UUID,
        *,
        asset_type: MediaAssetType | None = None,
    ) -> list[MediaAsset]:
        """All media assets of a track, newest first (read-only)."""
        if await self._session.get(Track, track_id) is None:
            raise CatalogEntityNotFoundError(f"track {track_id} not found")
        stmt = (
            select(MediaAsset)
            .options(joinedload(MediaAsset.object))
            .where(MediaAsset.track_id == track_id)
        )
        if asset_type is not None:
            stmt = stmt.where(MediaAsset.asset_type == asset_type)
        stmt = stmt.order_by(MediaAsset.created_at.desc(), MediaAsset.asset_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def download(self, asset_id: uuid.UUID) -> bytes:
        asset = await self.get_asset(asset_id)
        return await self._storage.download(asset.object.object_key)

    async def get_presigned_url(self, asset_id: uuid.UUID) -> str:
        asset = await self.get_asset(asset_id)
        return await self._storage.presigned_get_url(asset.object.object_key)

    async def delete_asset(self, asset_id: uuid.UUID) -> None:
        """Remove the asset, its storage object and the binary from RustFS.

        Guarded by the one-object-per-asset model: the binary is only
        deleted when no other asset references the same storage object
        (enforced by UNIQUE media_assets.object_id for new data and
        checked defensively here for legacy rows).

        The binary is deleted first: leftover rows pointing to a missing
        object are detectable and retryable, while untracked orphan
        objects would be much harder to find.
        """
        asset = await self.get_asset(asset_id)
        object_key = asset.object.object_key

        other_refs = await self._session.scalar(
            select(func.count()).select_from(MediaAsset).where(
                MediaAsset.object_id == asset.object_id,
                MediaAsset.asset_id != asset.asset_id,
            )
        )
        if other_refs:
            raise MediaStateError(
                f"storage object '{object_key}' is still referenced by "
                f"{other_refs} other media asset(s); refusing to delete"
            )

        await self._storage.delete(object_key)

        await self._session.delete(asset.object)
        await self._session.delete(asset)
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.error(
                "Object '%s' was deleted but the DB rows for asset %s "
                "could not be removed; they are kept for reconciliation",
                object_key,
                asset_id,
            )
            raise

    # -- validation helpers ---------------------------------------------------

    @staticmethod
    def _validate_payload(
        asset_type: MediaAssetType,
        *,
        mime_type: str,
        language: str | None,
        audio_format: str | None,
        audio_bitrate: int | None,
    ) -> None:
        """Early validation of asset metadata (before any reservation).

        These checks are deliberately performed before touching RustFS
        so that invalid input never leaves DB rows or binaries behind.
        Where PostgreSQL already enforces a rule (e.g. audio_bitrate > 0
        via CHECK) the check here is only an early exit to avoid a
        pointless upload -> compensation cycle, not the authority.
        """
        if asset_type is MediaAssetType.AUDIO:
            if not mime_type or not mime_type.startswith("audio/"):
                raise MediaValidationError(
                    f"audio assets require an audio/* MIME type, "
                    f"got {mime_type!r}"
                )
            if language is not None:
                raise MediaValidationError("language is only allowed for lyrics assets")
            if audio_bitrate is not None:
                if (
                    not isinstance(audio_bitrate, int)
                    or isinstance(audio_bitrate, bool)
                    or audio_bitrate <= 0
                ):
                    raise MediaValidationError(
                        f"audio_bitrate must be a positive integer, "
                        f"got {audio_bitrate!r}"
                    )
                if not audio_format:
                    raise MediaValidationError(
                        "audio_format is required for audio renditions"
                    )
            if audio_format is not None:
                if audio_bitrate is None:
                    raise MediaValidationError(
                        "audio_format is only valid for renditions "
                        "(together with audio_bitrate)"
                    )
                fmt = audio_format.strip().lower()
                if fmt not in ALLOWED_AUDIO_FORMATS:
                    raise MediaValidationError(
                        f"unsupported audio format {audio_format!r}; "
                        f"allowed: {sorted(ALLOWED_AUDIO_FORMATS)}"
                    )
        elif asset_type is MediaAssetType.LYRICS:
            if not mime_type or not mime_type.startswith("text/"):
                raise MediaValidationError(
                    f"lyrics assets require a text/* MIME type, "
                    f"got {mime_type!r}"
                )
            if audio_format is not None or audio_bitrate is not None:
                raise MediaValidationError(
                    "audio_format/audio_bitrate are only allowed for "
                    "audio assets"
                )
            if language is not None and len(language) > 20:
                raise MediaValidationError("language must be at most 20 characters")
        else:  # cover
            if not mime_type or not mime_type.startswith("image/"):
                raise MediaValidationError(
                    f"cover assets require an image/* MIME type, "
                    f"got {mime_type!r}"
                )
            if (
                language is not None
                or audio_format is not None
                or audio_bitrate is not None
            ):
                raise MediaValidationError(
                    "language/audio_format/audio_bitrate are only allowed "
                    "for lyrics/audio assets"
                )

    @staticmethod
    def _validate_target(
        asset_type: MediaAssetType,
        *,
        track_id: uuid.UUID | None,
        album_id: uuid.UUID | None,
    ) -> None:
        given = [
            name
            for name, value in (("track", track_id), ("album", album_id))
            if value is not None
        ]
        if len(given) != 1:
            raise MediaValidationError(
                "exactly one of track_id or album_id must be provided"
            )
        if asset_type in (MediaAssetType.AUDIO, MediaAssetType.LYRICS):
            if track_id is None:
                raise MediaValidationError(
                    f"asset type '{asset_type.value}' must be linked to a track"
                )
        # cover: either target is allowed

    async def _check_target(
        self,
        asset_type: MediaAssetType,
        *,
        track_id: uuid.UUID | None,
        album_id: uuid.UUID | None,
    ) -> None:
        """Validate the asset target and ensure the entity exists."""
        self._validate_target(asset_type, track_id=track_id, album_id=album_id)
        await self._ensure_entities_exist(
            asset_type, track_id=track_id, album_id=album_id
        )

    async def _ensure_entities_exist(
        self,
        asset_type: MediaAssetType,
        *,
        track_id: uuid.UUID | None,
        album_id: uuid.UUID | None,
    ) -> None:
        if track_id is not None and await self._session.get(Track, track_id) is None:
            raise CatalogEntityNotFoundError(f"track {track_id} does not exist")
        if album_id is not None and await self._session.get(Album, album_id) is None:
            raise CatalogEntityNotFoundError(f"album {album_id} does not exist")

    # -- compensation helpers ---------------------------------------------

    async def _release_reservation(
        self, reservation: StorageObject, *, object_key: str
    ) -> None:
        """Delete a PENDING reservation row when nothing was uploaded.

        Best effort: if the DB delete fails the row stays ``pending``,
        which is itself a discoverable inconsistent state.
        """
        try:
            await self._session.delete(reservation)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.error(
                "PENDING reservation '%s' could not be released; left "
                "for reconciliation",
                object_key,
            )

    async def _discard_uploaded_object(
        self,
        storage_object: StorageObject,
        *,
        object_key: str,
        bucket_name: str,
    ) -> None:
        """Compensate an uploaded binary whose registration/attachment failed.

        Deletes the binary first, then the row. If the binary cannot be
        deleted the row is marked ``failed`` so the orphan stays
        discoverable via ``status != 'available'``.
        """
        try:
            await self._storage.delete(object_key)
        except Exception:
            logger.error(
                "ORPHANED OBJECT: bucket='%s' key='%s' could not be "
                "deleted during compensation; marking the registration "
                "as failed",
                bucket_name,
                object_key,
            )
            await self._mark_failed(storage_object)
            return
        try:
            await self._session.delete(storage_object)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.error(
                "Object '%s' was deleted during compensation but its "
                "storage_objects row could not be removed; left for "
                "reconciliation",
                object_key,
            )

    async def _mark_failed(self, storage_object: StorageObject) -> None:
        """Best-effort marking of a storage object as failed."""
        try:
            storage_object.status = StorageObjectStatus.FAILED
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.error(
                "Could not mark storage object '%s' as failed; row is "
                "left in its previous state",
                storage_object.object_key,
            )



