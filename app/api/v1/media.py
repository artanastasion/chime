"""Media endpoints: uploads, listing, download redirect and deletion.

Uses the existing MediaService exclusively: no parallel storage
mechanism. Upload limits are enforced twice — by the spooled file size
before the call (``file.size`` is the actual received size once the
multipart body is parsed) and by the registered size afterwards for
clients that do not report it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Query, UploadFile, status
from fastapi.responses import RedirectResponse

from app.api.deps import get_media_service
from app.models import MediaAssetType, StorageObjectStatus
from app.schemas.media import LyricsUpload, MediaAssetOut
from app.services.errors import MediaStateError, MediaValidationError
from app.services.media_assets import MediaService

router = APIRouter(tags=["Media"])

MAX_AUDIO_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB
MAX_COVER_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_LYRICS_BYTES = 1024 * 1024  # 1 MiB


async def _check_upload_file(
    file: UploadFile, *, expected_prefix: str, max_bytes: int
) -> str:
    """Preliminary client-side-declared checks (authoritative checks
    live in MediaService); keeps invalid requests away from the storage."""
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    if not mime.startswith(expected_prefix):
        raise MediaValidationError(
            f"upload requires a {expected_prefix}* MIME type, "
            f"got {mime or 'unknown'!r}"
        )
    if file.size is not None:
        if file.size == 0:
            raise MediaValidationError("file must not be empty")
        if file.size > max_bytes:
            raise MediaValidationError(
                f"file exceeds the maximum size of {max_bytes} bytes"
            )
    return mime


def _to_out(asset, url: str | None) -> MediaAssetOut:
    return MediaAssetOut(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        track_id=asset.track_id,
        album_id=asset.album_id,
        language=asset.language,
        audio_format=asset.audio_format,
        audio_bitrate=asset.audio_bitrate,
        is_primary=asset.is_primary,
        status=asset.object.status,
        mime_type=asset.object.mime_type,
        size_bytes=asset.object.size_bytes,
        checksum=asset.object.checksum,
        created_at=asset.created_at,
        download_url=url,
    )


async def _to_out_with_url(asset, service: MediaService) -> MediaAssetOut:
    url = None
    if asset.object.status == StorageObjectStatus.AVAILABLE:
        url = await service.get_presigned_url(asset.asset_id)
    return _to_out(asset, url)


@router.post(
    "/tracks/{track_id}/audio",
    status_code=status.HTTP_201_CREATED,
    response_model=MediaAssetOut,
)
async def upload_track_audio(
    track_id: uuid.UUID,
    file: UploadFile,
    service: MediaService = Depends(get_media_service),
    audio_format: str | None = Form(default=None),
    audio_bitrate: int | None = Form(default=None),
    is_primary: bool = Form(default=False),
) -> MediaAssetOut:
    mime = await _check_upload_file(
        file, expected_prefix="audio/", max_bytes=MAX_AUDIO_UPLOAD_BYTES
    )
    asset = await service.upload_track_audio(
        track_id,
        file.file,  # seekable spooled stream: hashed and sent in chunks
        mime_type=mime,
        original_filename=file.filename,
        audio_format=audio_format,
        audio_bitrate=audio_bitrate,
        is_primary=is_primary,
    )
    if asset.object.size_bytes > MAX_AUDIO_UPLOAD_BYTES:
        await service.delete_asset(asset.asset_id)
        raise MediaValidationError(
            f"file exceeds the maximum size of {MAX_AUDIO_UPLOAD_BYTES} bytes"
        )
    return await _to_out_with_url(asset, service)


@router.post(
    "/tracks/{track_id}/lyrics",
    status_code=status.HTTP_201_CREATED,
    response_model=MediaAssetOut,
)
async def upload_track_lyrics(
    track_id: uuid.UUID,
    payload: LyricsUpload,
    service: MediaService = Depends(get_media_service),
) -> MediaAssetOut:
    if len(payload.content.encode("utf-8")) > MAX_LYRICS_BYTES:
        raise MediaValidationError(
            f"lyrics exceed the maximum size of {MAX_LYRICS_BYTES} bytes"
        )
    asset = await service.upload_lyrics(
        track_id, payload.content, language=payload.language
    )
    return await _to_out_with_url(asset, service)


@router.post(
    "/tracks/{track_id}/cover",
    status_code=status.HTTP_201_CREATED,
    response_model=MediaAssetOut,
)
async def upload_track_cover(
    track_id: uuid.UUID,
    file: UploadFile,
    service: MediaService = Depends(get_media_service),
) -> MediaAssetOut:
    mime = await _check_upload_file(
        file, expected_prefix="image/", max_bytes=MAX_COVER_UPLOAD_BYTES
    )
    asset = await service.upload_cover(
        content=file.file,
        mime_type=mime,
        track_id=track_id,
        original_filename=file.filename,
    )
    return await _to_out_with_url(asset, service)


@router.post(
    "/albums/{album_id}/cover",
    status_code=status.HTTP_201_CREATED,
    response_model=MediaAssetOut,
)
async def upload_album_cover(
    album_id: uuid.UUID,
    file: UploadFile,
    service: MediaService = Depends(get_media_service),
) -> MediaAssetOut:
    mime = await _check_upload_file(
        file, expected_prefix="image/", max_bytes=MAX_COVER_UPLOAD_BYTES
    )
    asset = await service.upload_cover(
        content=file.file,
        mime_type=mime,
        album_id=album_id,
        original_filename=file.filename,
    )
    return await _to_out_with_url(asset, service)


@router.get("/tracks/{track_id}/media", response_model=list[MediaAssetOut])
async def list_track_media(
    track_id: uuid.UUID,
    service: MediaService = Depends(get_media_service),
    asset_type: MediaAssetType | None = Query(default=None),
) -> list[MediaAssetOut]:
    assets = await service.list_track_assets(track_id, asset_type=asset_type)
    return [await _to_out_with_url(a, service) for a in assets]


@router.get("/media/{asset_id}", response_model=MediaAssetOut)
async def get_media_asset(
    asset_id: uuid.UUID,
    service: MediaService = Depends(get_media_service),
) -> MediaAssetOut:
    asset = await service.get_asset(asset_id)
    return await _to_out_with_url(asset, service)


@router.get("/media/{asset_id}/download")
async def download_media_asset(
    asset_id: uuid.UUID,
    service: MediaService = Depends(get_media_service),
) -> RedirectResponse:
    """307 redirect to a short-lived presigned URL.

    RustFS serves the binary directly (including HTTP Range), so the
    API never proxies file contents. The redirect is issued only for
    objects in the 'available' state.
    """
    asset = await service.get_asset(asset_id)
    if asset.object.status != StorageObjectStatus.AVAILABLE:
        # Consistent 409 for state conflicts (details in logs only).
        raise MediaStateError(f"asset {asset_id} is not available for download")
    url = await service.get_presigned_url(asset_id)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.delete("/media/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media_asset(
    asset_id: uuid.UUID,
    service: MediaService = Depends(get_media_service),
) -> None:
    await service.delete_asset(asset_id)

