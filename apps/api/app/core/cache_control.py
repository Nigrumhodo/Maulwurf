"""Structural `Cache-Control: no-store` for every API response (A4.6, M1/M9).

Routes already set the header where the contract demanded it, but a per-route guarantee
fails the day a new route (or a framework 404/405, a redirect, an SSE response or an error
envelope) forgets it. This ASGI middleware makes the rule structural: the API serves
tenant-owned content only, so nothing is cacheable by a browser, a shared proxy or the
service worker. `app.core.errors` repeats it for the responses built outside the user
middleware stack (an unhandled 500). It never reads the request body.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CACHE_CONTROL_HEADER = b"cache-control"
NO_STORE = b"no-store"


class NoStoreMiddleware:
    """Replace any `Cache-Control` the route produced with `no-store`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != CACHE_CONTROL_HEADER
                ]
                headers.append((CACHE_CONTROL_HEADER, NO_STORE))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_no_store)
