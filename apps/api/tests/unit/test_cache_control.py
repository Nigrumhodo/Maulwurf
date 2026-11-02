"""A4.6: `Cache-Control: no-store` is structural, not a per-route convention.

A route added later, a redirect, a framework 404 or an error envelope must never be cached
by a browser, a shared proxy or the service worker (M1/M9, DISENO §3.1).
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient

from app.core.cache_control import NoStoreMiddleware
from app.core.errors import ApiError, _response, install_error_handlers

NO_STORE = "no-store"


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(NoStoreMiddleware)

    @app.get("/public-looking")
    async def public_looking() -> JSONResponse:
        # A route must not be able to opt out of the privacy rule by accident.
        return JSONResponse({"ok": True}, headers={"Cache-Control": "public, max-age=60"})

    @app.get("/redirect")
    async def redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=302)

    @app.get("/boom")
    async def boom() -> None:
        raise ApiError(429, "rate_limited", "Demasiadas solicitudes.", headers={"Retry-After": "7"})

    return app


@pytest.mark.parametrize("path", ["/public-looking", "/redirect", "/boom", "/missing"])
def test_every_http_response_is_no_store(path: str) -> None:
    response = TestClient(_app(), follow_redirects=False).get(path)

    assert response.headers["cache-control"] == NO_STORE


def test_rate_limit_headers_survive_the_no_store_rewrite() -> None:
    response = TestClient(_app()).get("/boom")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.headers["cache-control"] == NO_STORE


def test_the_error_envelope_carries_no_store_on_its_own() -> None:
    # The unhandled-500 handler runs outside the user middleware stack, so the envelope
    # builder repeats the header instead of assuming it.
    response = _response(ApiError(500, "internal_error", "Error interno del servidor."))

    assert response.headers["cache-control"] == NO_STORE
