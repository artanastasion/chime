"""Async facade over the synchronous MinIO SDK for RustFS.

Every blocking SDK call is executed in a worker thread via
``asyncio.to_thread`` so the asyncio event loop is never blocked.
This service deals exclusively with binary objects; the link between
objects and catalog records lives in :mod:`app.services.media_assets`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.error import MinioException, S3Error

from app.core.config import settings
from app.storage.schemas import ObjectInfo, UploadResult

logger = logging.getLogger(__name__)

_DEFAULT_PRESIGN_EXPIRY = timedelta(minutes=15)
_CHUNK_SIZE = 1024 * 1024
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchObject"}


class StorageError(RuntimeError):
    """Base class for object storage failures."""


class StorageUnavailableError(StorageError):
    """RustFS is unreachable or rejected the credentials."""


class ObjectNotFoundError(StorageError):
    """The requested object does not exist in the bucket."""


class StorageService:
    """Operations on binary objects stored in RustFS."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @property
    def bucket(self) -> str:
        """Name of the media bucket this service operates on."""
        return self._bucket

    # -- infrastructure ------------------------------------------------

    async def ping(self) -> None:
        """Verify that RustFS is reachable and accepts the credentials."""
        try:
            await asyncio.to_thread(self._client.list_buckets)
        except S3Error as exc:
            raise StorageUnavailableError(
                f"RustFS at {settings.storage_endpoint} rejected credentials "
                f"(code={exc.code}): {exc.message}",
            ) from exc
        except MinioException as exc:
            raise StorageUnavailableError(
                f"RustFS client error: {exc}",
            ) from exc
        except Exception as exc:  # network layer errors (urllib3, socket, ...)
            raise StorageUnavailableError(
                f"Cannot reach RustFS at {settings.storage_endpoint}: "
                f"{exc.__class__.__name__}",
            ) from exc

    async def check_availability(self) -> None:
        """Health check: server reachable and configured bucket present."""
        await self.ping()
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        except Exception as exc:
            raise StorageUnavailableError(
                f"Failed to inspect bucket '{self._bucket}': {exc.__class__.__name__}",
            ) from exc
        if not exists:
            raise StorageUnavailableError(
                f"Bucket '{self._bucket}' does not exist in RustFS",
            )

    async def ensure_bucket(self) -> None:
        """Idempotently create the media bucket if it is missing."""
        await self.ping()
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
            if not exists:
                await asyncio.to_thread(self._client.make_bucket, self._bucket)
                logger.info("Created object storage bucket '%s'", self._bucket)
            else:
                logger.info("Object storage bucket '%s' is available", self._bucket)
        except Exception as exc:
            raise StorageUnavailableError(
                f"Failed to ensure bucket '{self._bucket}': {exc.__class__.__name__}",
            ) from exc

    # -- object operations ----------------------------------------------

    async def upload(
        self,
        *,
        content: bytes | BinaryIO,
        object_key: str,
        mime_type: str,
    ) -> UploadResult:
        """Upload content and return the metadata of the stored object."""
        data, size, checksum = self._prepare(content)
        try:
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                object_key,
                data,
                size,
                content_type=mime_type,
            )
        except S3Error as exc:
            raise StorageError(
                f"Failed to upload object '{object_key}' "
                f"(code={exc.code}): {exc.message}",
            ) from exc
        except Exception as exc:
            raise StorageError(
                f"Failed to upload object '{object_key}': {exc.__class__.__name__}",
            ) from exc
        return UploadResult(
            bucket=self._bucket,
            object_key=object_key,
            size_bytes=size,
            checksum_sha256=checksum or "",
        )

    async def exists(self, object_key: str) -> bool:
        """Return True when the object is present in the bucket."""
        try:
            await asyncio.to_thread(self._client.stat_object, self._bucket, object_key)
            return True
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                return False
            raise StorageError(
                f"Failed to stat object '{object_key}' (code={exc.code})",
            ) from exc
        except Exception as exc:
            raise StorageError(
                f"Failed to stat object '{object_key}': {exc.__class__.__name__}",
            ) from exc

    async def stat(self, object_key: str) -> ObjectInfo:
        """Return head information for an existing object."""
        try:
            info = await asyncio.to_thread(
                self._client.stat_object, self._bucket, object_key
            )
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(
                    f"Object '{object_key}' not found in bucket '{self._bucket}'",
                ) from exc
            raise StorageError(
                f"Failed to stat object '{object_key}' (code={exc.code})",
            ) from exc
        except Exception as exc:
            raise StorageError(
                f"Failed to stat object '{object_key}': {exc.__class__.__name__}",
            ) from exc
        return ObjectInfo(
            object_key=object_key,
            size_bytes=info.size,
            etag=info.etag,
            last_modified=info.last_modified,
            content_type=info.content_type,
        )

    async def download(self, object_key: str) -> bytes:
        """Read the full content of an object, releasing the connection."""
        try:
            response = await asyncio.to_thread(
                self._client.get_object, self._bucket, object_key
            )
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(
                    f"Object '{object_key}' not found in bucket '{self._bucket}'",
                ) from exc
            raise StorageError(
                f"Failed to download object '{object_key}' (code={exc.code})",
            ) from exc
        except Exception as exc:
            raise StorageError(
                f"Failed to download object '{object_key}': {exc.__class__.__name__}",
            ) from exc

        try:
            return await asyncio.to_thread(response.read)
        except Exception as exc:
            raise StorageError(
                f"Failed to read object '{object_key}': {exc.__class__.__name__}",
            ) from exc
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def presigned_get_url(
        self,
        object_key: str,
        expires: timedelta = _DEFAULT_PRESIGN_EXPIRY,
    ) -> str:
        """Return a short-lived signed download URL for an object."""
        try:
            return await asyncio.to_thread(
                self._client.presigned_get_object,
                self._bucket,
                object_key,
                expires,
            )
        except Exception as exc:
            raise StorageError(
                f"Failed to presign URL for '{object_key}': {exc.__class__.__name__}",
            ) from exc

    async def delete(self, object_key: str) -> None:
        """Remove an object from the bucket.

        S3 deletes are idempotent, so removing an already missing key is
        not treated as an error.
        """
        try:
            await asyncio.to_thread(
                self._client.remove_object, self._bucket, object_key
            )
        except S3Error as exc:
            raise StorageError(
                f"Failed to delete object '{object_key}' (code={exc.code})",
            ) from exc
        except Exception as exc:
            raise StorageError(
                f"Failed to delete object '{object_key}': {exc.__class__.__name__}",
            ) from exc

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _prepare(
        content: bytes | BinaryIO,
    ) -> tuple[BinaryIO, int, str | None]:
        """Normalise input into a stream plus size and sha256 checksum."""
        if isinstance(content, (bytes, bytearray)):
            payload = bytes(content)
            digest = hashlib.sha256(payload).hexdigest()
            return io.BytesIO(payload), len(payload), digest

        if not (hasattr(content, "read") and hasattr(content, "seek")):
            raise TypeError(
                "content must be bytes or a seekable binary file object"
            )
        digest = hashlib.sha256()
        size = 0
        while chunk := content.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
        content.seek(0)
        return content, size, digest.hexdigest()
