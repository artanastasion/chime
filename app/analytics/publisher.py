from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.core.config import settings

logger = logging.getLogger(__name__)

RECONNECT_DELAYS_SECONDS = (1, 2, 5, 10, 30)


class EventPublisherUnavailableError(RuntimeError):
    pass


def _serialize(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def _encode_key(key: str | None) -> bytes | None:
    return key.encode() if key is not None else None


class EventPublisher:
    def __init__(self, bootstrap_servers: str | None, client_id: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._producer: AIOKafkaProducer | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._dropped = 0

    @property
    def enabled(self) -> bool:
        return self._bootstrap_servers is not None

    @property
    def ready(self) -> bool:
        return self._producer is not None

    @property
    def dropped(self) -> int:
        return self._dropped

    async def start(self) -> None:
        if not self.enabled or self._connect_task is not None:
            return
        if not await self._try_connect():
            self._connect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        if self._connect_task is not None:
            self._connect_task.cancel()
            await asyncio.gather(self._connect_task, return_exceptions=True)
            self._connect_task = None
        if self._producer is not None:
            producer, self._producer = self._producer, None
            await producer.stop()

    async def publish_nowait(
        self, topic: str, value: Mapping[str, Any], key: str | None = None
    ) -> None:
        if self._producer is None:
            self._dropped += 1
            return
        try:
            await self._producer.send(topic, _serialize(value), key=_encode_key(key))
        except KafkaError as exc:
            self._dropped += 1
            logger.warning("Analytics event dropped (topic=%s): %s", topic, exc)

    async def publish_batch(
        self, topic: str, records: Iterable[tuple[str | None, Mapping[str, Any]]]
    ) -> int:
        if self._producer is None:
            raise EventPublisherUnavailableError("event bus is not connected")
        try:
            deliveries = [
                await self._producer.send(
                    topic, _serialize(value), key=_encode_key(key)
                )
                for key, value in records
            ]
            await asyncio.gather(*deliveries)
        except KafkaError as exc:
            raise EventPublisherUnavailableError(str(exc)) from exc
        return len(deliveries)

    async def _try_connect(self) -> bool:
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            acks=1,
            linger_ms=20,
            compression_type="gzip",
            request_timeout_ms=10_000,
        )
        try:
            await producer.start()
        except KafkaError as exc:
            await producer.stop()
            logger.warning(
                "Event bus %s is unavailable: %s", self._bootstrap_servers, exc
            )
            return False
        self._producer = producer
        logger.info("Event bus connected: %s", self._bootstrap_servers)
        return True

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while not await self._try_connect():
            delay = RECONNECT_DELAYS_SECONDS[
                min(attempt, len(RECONNECT_DELAYS_SECONDS) - 1)
            ]
            attempt += 1
            await asyncio.sleep(delay)
        self._connect_task = None


event_publisher = EventPublisher(
    settings.kafka_bootstrap_servers, settings.kafka_client_id
)
