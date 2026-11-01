from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_event_ingest_service
from app.schemas.events import ClientEventBatch, ClientEventBatchAccepted
from app.services.events import EventIngestService

router = APIRouter(prefix="/events", tags=["Analytics"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ClientEventBatchAccepted,
)
async def ingest_client_events(
    batch: ClientEventBatch,
    service: EventIngestService = Depends(get_event_ingest_service),
) -> ClientEventBatchAccepted:
    return ClientEventBatchAccepted(accepted=await service.ingest(batch))
