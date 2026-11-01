from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.analytics.access_log import AccessLogMiddleware, _route_template
from app.analytics.publisher import EventPublisher
from app.api.deps import get_event_ingest_service
from app.services.events import EventIngestService
from tests.conftest import Api, app

TOPIC = "test.app.events"


class RecordingPublisher(EventPublisher):
    def __init__(self, connected: bool = True) -> None:
        super().__init__("recording:9092" if connected else None, "test")
        self.connected = connected
        self.records: list[tuple[str, str | None, dict]] = []

    async def publish_nowait(self, topic, value, key=None) -> None:
        self.records.append((topic, key, dict(value)))

    async def publish_batch(self, topic, records) -> int:
        if not self.connected:
            return await super().publish_batch(topic, records)
        batch = [(topic, key, dict(value)) for key, value in records]
        self.records.extend(batch)
        return len(batch)


def _event(**overrides) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "play_start",
        "occurred_at": datetime.now(UTC).isoformat(),
        "session_id": str(uuid.uuid4()),
        "track_id": str(uuid.uuid4()),
        "position_ms": 0,
        **overrides,
    }


def _batch(*events: dict, user_id: str | None = None) -> dict:
    return {
        "context": {
            "device_id": "device-1",
            "user_id": user_id,
            "platform": "ios",
            "app_version": "2.3.0",
            "country": "RU",
        },
        "events": list(events),
    }


def _with_publisher(publisher: RecordingPublisher, scenario):
    app.dependency_overrides[get_event_ingest_service] = lambda: EventIngestService(
        publisher, TOPIC
    )
    try:
        return Api().run(scenario)
    finally:
        app.dependency_overrides.pop(get_event_ingest_service, None)


def test_ingest_events_publishes_enriched_records():
    publisher = RecordingPublisher()
    user_id = str(uuid.uuid4())

    async def scenario(client):
        resp = await client.post(
            "/api/v1/events",
            json=_batch(
                _event(),
                _event(
                    event_type="search", track_id=None, properties={"query": "lofi"}
                ),
                user_id=user_id,
            ),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json() == {"accepted": 2}

    _with_publisher(publisher, scenario)
    assert [(topic, key) for topic, key, _ in publisher.records] == [
        (TOPIC, user_id),
        (TOPIC, user_id),
    ]
    play, search = (record for _, _, record in publisher.records)
    assert (
        play["platform"] == "ios"
        and play["country"] == "RU"
        and play["user_id"] == user_id
    )
    assert play["received_at"] and play["position_ms"] == 0
    assert search["track_id"] is None and search["properties"] == {"query": "lofi"}


def test_ingest_events_rejects_invalid_batches():
    publisher = RecordingPublisher()
    now = datetime.now(UTC)

    async def scenario(client):
        for payload in (
            _batch(_event(occurred_at=(now + timedelta(hours=1)).isoformat())),
            _batch(_event(occurred_at=(now - timedelta(days=40)).isoformat())),
            _batch(_event(occurred_at=now.replace(tzinfo=None).isoformat())),
            _batch(_event(event_type="unknown")),
            _batch(_event(position_ms=-1)),
            _batch(_event(properties={str(i): "v" for i in range(21)})),
            _batch(),
            {
                **_batch(_event()),
                "context": {"device_id": "d", "platform": "tv", "app_version": "1"},
            },
        ):
            resp = await client.post("/api/v1/events", json=payload)
            assert resp.status_code == 422, (payload, resp.text)

    _with_publisher(publisher, scenario)
    assert publisher.records == []


def test_ingest_events_unavailable_bus_returns_503():
    publisher = RecordingPublisher(connected=False)

    async def scenario(client):
        resp = await client.post("/api/v1/events", json=_batch(_event()))
        assert resp.status_code == 503, resp.text

    _with_publisher(publisher, scenario)


def test_route_template_restores_path_parameters():
    scope = {
        "endpoint": object(),
        "path": "/api/v1/tracks/abc/artists/def",
        "path_params": {"track_id": "abc", "track_artist_id": "def"},
    }
    assert (
        _route_template(scope) == "/api/v1/tracks/{track_id}/artists/{track_artist_id}"
    )
    assert _route_template({"path": "/nope"}) == "<unmatched>"
    assert (
        _route_template({"endpoint": object(), "path": "/health", "path_params": {}})
        == "/health"
    )


def test_access_log_middleware_records_requests():
    publisher = RecordingPublisher()
    demo = FastAPI()
    demo.add_middleware(AccessLogMiddleware, publisher=publisher, topic="test.access")

    @demo.get("/items/{item_id}")
    async def read_item(item_id: str) -> dict:
        return {"item_id": item_id}

    user_id = str(uuid.uuid4())

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=demo), base_url="http://test"
        ) as client:
            ok = await client.get(
                "/items/42",
                headers={
                    "X-Device-ID": "device-9",
                    "X-User-ID": user_id,
                    "X-App-Platform": "android",
                },
            )
            missing = await client.get("/missing")
            return ok, missing

    ok, missing = asyncio.run(scenario())
    assert ok.status_code == 200 and uuid.UUID(ok.headers["X-Request-ID"])
    assert missing.status_code == 404

    (_, key, first), (_, _, second) = publisher.records
    assert key == ok.headers["X-Request-ID"] == first["request_id"]
    assert first["route"] == "/items/{item_id}" and first["path"] == "/items/42"
    assert first["status_code"] == 200 and first["response_bytes"] == len(ok.content)
    assert (
        first["device_id"] == "device-9"
        and first["user_id"] == user_id
        and first["platform"] == "android"
    )
    assert second["route"] == "<unmatched>" and second["status_code"] == 404
