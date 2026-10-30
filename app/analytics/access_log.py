from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.analytics.publisher import EventPublisher

REQUEST_ID_HEADER = "X-Request-ID"
UNMATCHED_ROUTE = "<unmatched>"


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _route_template(scope: Scope) -> str:
    if scope.get("endpoint") is None:
        return UNMATCHED_ROUTE
    params = scope.get("path_params") or {}
    if not params:
        return scope["path"]
    names_by_value = {str(value): name for name, value in params.items()}
    return "/".join(
        f"{{{names_by_value[segment]}}}" if segment in names_by_value else segment
        for segment in scope["path"].split("/")
    )


def _parse_int(value: str | None) -> int:
    try:
        return max(int(value), 0) if value else 0
    except ValueError:
        return 0


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp, publisher: EventPublisher, topic: str) -> None:
        self.app = app
        self.publisher = publisher
        self.topic = topic

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = datetime.now(UTC)
        started = time.perf_counter()
        headers = Headers(scope=scope)
        request_id = _parse_uuid(headers.get(REQUEST_ID_HEADER)) or uuid.uuid4()
        status_code = 500
        response_bytes = 0

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code, response_bytes
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = str(request_id)
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        finally:
            route = _route_template(scope)
            user_id = _parse_uuid(headers.get("X-User-ID"))
            await self.publisher.publish_nowait(
                self.topic,
                {
                    "ts": started_at.isoformat(),
                    "request_id": str(request_id),
                    "method": scope["method"],
                    "route": route,
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "request_bytes": _parse_int(headers.get("content-length")),
                    "response_bytes": response_bytes,
                    "device_id": headers.get("X-Device-ID", ""),
                    "user_id": str(user_id) if user_id else None,
                    "platform": headers.get("X-App-Platform", "unknown"),
                    "app_version": headers.get("X-App-Version", "unknown"),
                    "user_agent": headers.get("user-agent", ""),
                },
                key=str(request_id),
            )
