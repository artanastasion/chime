from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.analytics.publisher import EventPublisher
from app.schemas.events import ClientEventBatch


class EventIngestService:
    def __init__(self, publisher: EventPublisher, topic: str) -> None:
        self._publisher = publisher
        self._topic = topic

    async def ingest(self, batch: ClientEventBatch) -> int:
        received_at = datetime.now(UTC).isoformat()
        context = batch.context
        partition_key = str(context.user_id or context.device_id)
        common: dict[str, Any] = {
            "received_at": received_at,
            "user_id": str(context.user_id) if context.user_id else None,
            "device_id": context.device_id,
            "platform": context.platform.value,
            "app_version": context.app_version,
            "country": context.country,
        }
        records = (
            (
                partition_key,
                {
                    **common,
                    "event_id": str(event.event_id),
                    "event_type": event.event_type.value,
                    "occurred_at": event.occurred_at.isoformat(),
                    "session_id": str(event.session_id),
                    "track_id": str(event.track_id) if event.track_id else None,
                    "album_id": str(event.album_id) if event.album_id else None,
                    "position_ms": event.position_ms,
                    "duration_ms": event.duration_ms,
                    "properties": event.properties,
                },
            )
            for event in batch.events
        )
        return await self._publisher.publish_batch(self._topic, records)
