import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.analytics.access_log import AccessLogMiddleware
from app.analytics.publisher import event_publisher
from app.api.errors import register_exception_handlers
from app.api.v1 import api_router
from app.core.config import settings
from app.db.session import engine
from app.storage.client import storage_client
from app.storage.service import StorageError, StorageService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

storage_service = StorageService(storage_client, settings.storage_bucket)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast with a clear diagnostic when RustFS is not usable:
    # the API must not report a healthy storage when it is not.
    await storage_service.ensure_bucket()
    await event_publisher.start()

    yield

    await event_publisher.stop()
    await engine.dispose()


app = FastAPI(
    title="Music Platform API",
    description="Music catalog and discovery platform for video creators",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.add_middleware(
    AccessLogMiddleware,
    publisher=event_publisher,
    topic=settings.analytics_access_log_topic,
)
app.include_router(api_router)


@app.get("/health", tags=["Health"])
async def health_check() -> dict:
    """Report API, PostgreSQL and RustFS status independently."""
    checks = {"api": "ok", "postgres": "ok", "storage": "ok"}
    errors: dict[str, str] = {}

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        checks["postgres"] = "error"
        errors["postgres"] = exc.__class__.__name__
        logger.warning("PostgreSQL health check failed: %s", exc)

    try:
        await storage_service.check_availability()
    except StorageError as exc:
        checks["storage"] = "error"
        errors["storage"] = str(exc)
        logger.warning("Storage health check failed: %s", exc)

    payload: dict = {"status": "ok" if not errors else "error", "checks": checks}
    if errors:
        payload["errors"] = errors
        raise HTTPException(status_code=503, detail=payload)
    return payload
