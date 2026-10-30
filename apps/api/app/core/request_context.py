"""ASGI middleware for `X-Request-ID` (J2.5): binds the id to the log context and echoes it.

Pure ASGI and it never reads the body: the id and the access log use only method, path
(without query string) and status. An incoming `X-Request-ID` is accepted only when it
matches `REQUEST_ID_PATTERN`; otherwise a new one is generated, so a client cannot inject
lines or fields into the logs.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import correlation

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER: Final = b"x-request-id"
REQUEST_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}")


def resolve_request_id(incoming: str | None) -> str:
    """Return the incoming id when it is valid; otherwise a new UUID4 hex."""
    if incoming is not None and REQUEST_ID_PATTERN.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Assign a `request_id` per request and log one access line without the query string."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = resolve_request_id(incoming)
        started = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = [*message.get("headers", []), (REQUEST_ID_HEADER, request_id.encode())]
                message["headers"] = headers
            await send(message)

        with correlation(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            finally:
                logger.info(
                    "http.request method=%s path=%s status=%s duration_ms=%.1f",
                    scope.get("method", ""),
                    scope.get("path", ""),
                    status_holder.get("status", 0),
                    (time.perf_counter() - started) * 1000,
                )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            decoded: str = value.decode("latin-1")
            return decoded
    return None
