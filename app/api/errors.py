"""Centralised mapping of service/DB/storage errors to HTTP responses.

No internal details (SQL statements, S3 keys, tracebacks) are ever
leaked to the client; the full context goes to the logs only.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.services import errors as service_errors
from app.storage.service import StorageError, StorageUnavailableError

logger = logging.getLogger(__name__)

# PostgreSQL SQLSTATE codes used to classify IntegrityError.
_PG_UNIQUE_VIOLATION = "23505"
_PG_FOREIGN_KEY_VIOLATION = "23503"
_PG_CHECK_VIOLATION = "23514"
_PG_NOT_NULL_VIOLATION = "23502"


def _json(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _sqlstate(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all service-to-HTTP error mappings to the application."""

    @app.exception_handler(service_errors.CatalogEntityNotFoundError)
    async def _entity_not_found(request: Request, exc: Exception) -> JSONResponse:
        # Entity addressed through the URL path does not exist.
        return _json(404, str(exc))

    @app.exception_handler(service_errors.AssetNotFoundError)
    async def _asset_not_found(request: Request, exc: Exception) -> JSONResponse:
        return _json(404, str(exc))

    @app.exception_handler(service_errors.SlotConflictError)
    async def _slot_conflict(request: Request, exc: Exception) -> JSONResponse:
        return _json(409, str(exc))

    @app.exception_handler(service_errors.MediaStateError)
    async def _media_state(request: Request, exc: Exception) -> JSONResponse:
        return _json(409, str(exc))

    @app.exception_handler(service_errors.MediaValidationError)
    async def _media_validation(request: Request, exc: Exception) -> JSONResponse:
        return _json(422, str(exc))

    @app.exception_handler(service_errors.InvalidReferenceError)
    async def _invalid_reference(request: Request, exc: Exception) -> JSONResponse:
        # Unified contract for body references to missing entities.
        return _json(422, str(exc))

    @app.exception_handler(service_errors.DuplicateLinkError)
    async def _duplicate_link(request: Request, exc: Exception) -> JSONResponse:
        return _json(409, str(exc))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        state = _sqlstate(exc)
        if state == _PG_UNIQUE_VIOLATION:
            logger.warning("Unique constraint violation: %s", exc)
            return _json(409, "conflict: the record already exists")
        if state in {
            _PG_FOREIGN_KEY_VIOLATION,
            _PG_CHECK_VIOLATION,
            _PG_NOT_NULL_VIOLATION,
        }:
            # Known integrity rules tied to user input; the message may
            # contain SQL, so only a generic explanation is returned.
            logger.warning("Integrity violation on user input: %s", exc)
            return _json(422, "invalid reference or value")
        logger.exception("Unexpected integrity error")
        return _json(500, "internal server error")

    @app.exception_handler(StorageUnavailableError)
    async def _storage_unavailable(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error("Object storage unavailable: %s", exc)
        return _json(503, "object storage is unavailable")

    @app.exception_handler(StorageError)
    async def _storage_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Object storage operation failed: %s", exc)
        return _json(502, "object storage operation failed")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Last resort: never leak internals to the client.
        logger.exception("Unhandled error in %s %s", request.method, request.url.path)
        return _json(500, "internal server error")
