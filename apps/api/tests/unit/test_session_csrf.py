"""U-S1-AN-01 / U-S1-AN-02 (A1.4): cookie de sesión, CSRF y Origin, sin base de datos."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.core import deps
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.models import Session
from app.services import sessions

COOKIE = "cookie-token-for-tests"
ORIGIN = settings.allowed_origin


def _session(cookie: str = COOKIE) -> Session:
    csrf = sessions.csrf_token_for(cookie)
    return Session(
        id=uuid.uuid4(), user_id=uuid.uuid4(), token_hash=sessions._sha256(cookie),
        csrf_hash=sessions._sha256(csrf), expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


# --- U-S1-AN-01: cookie y hashes -------------------------------------------------------


def test_session_cookie_has_required_flags() -> None:
    response = Response()
    deps.set_session_cookie(response, COOKIE)
    header = response.headers["set-cookie"]

    assert header.startswith(f"{deps.COOKIE_NAME}={COOKIE};")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in header
    assert f"Max-Age={settings.session_ttl_hours * 3600}" in header


def test_cleared_cookie_expires_immediately() -> None:
    response = Response()
    deps.clear_session_cookie(response)

    assert "Max-Age=0" in response.headers["set-cookie"]


def test_stored_hashes_never_equal_the_plain_values() -> None:
    session = _session()

    assert COOKIE not in session.token_hash
    assert sessions.csrf_token_for(COOKIE) != session.csrf_hash


def test_csrf_token_is_stable_per_session_and_distinct_across_sessions() -> None:
    assert sessions.csrf_token_for(COOKIE) == sessions.csrf_token_for(COOKIE)
    assert sessions.csrf_token_for(COOKIE) != sessions.csrf_token_for("other-cookie")


def test_csrf_matches_only_its_own_session() -> None:
    session = _session()

    assert sessions.csrf_matches(session, COOKIE, sessions.csrf_token_for(COOKIE))
    assert not sessions.csrf_matches(session, COOKIE, None)
    assert not sessions.csrf_matches(session, COOKIE, "forged")
    assert not sessions.csrf_matches(session, COOKIE, sessions.csrf_token_for("other-cookie"))
    assert not sessions.csrf_matches(session, COOKIE, "t\u00fcken\udcff")


# --- U-S1-AN-02: CSRF y Origin en mutaciones --------------------------------------------


def _app(session: Session) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    async def fake_current_session() -> Session:
        return session

    app.dependency_overrides[deps.current_session] = fake_current_session

    @app.get("/thing")
    async def read(_: deps.MutationSession) -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/thing")
    async def create(_: deps.MutationSession) -> dict[str, str]:
        return {"ok": "created"}

    @app.put("/thing/content")
    async def upload(_: deps.MutationSession) -> dict[str, str]:
        return {"ok": "uploaded"}

    return app


async def _call(
    method: str, path: str, headers: dict[str, str], content: bytes | None = None
) -> tuple[int, dict[str, object]]:
    transport = ASGITransport(app=_app(_session()))
    async with AsyncClient(
        transport=transport, base_url=ORIGIN, cookies={deps.COOKIE_NAME: COOKIE}
    ) as client:
        # Bytes latin-1: así llegan los headers por ASGI y httpx no rechaza los no ASCII.
        raw = {k.encode(): v.encode("latin-1") for k, v in headers.items()}
        response = await client.request(method, path, headers=raw, content=content)
    return response.status_code, response.json()


VALID = {"Origin": ORIGIN, deps.CSRF_HEADER: sessions.csrf_token_for(COOKIE)}


async def test_mutation_with_token_and_origin_is_accepted() -> None:
    assert (await _call("POST", "/thing", VALID))[0] == 200


async def test_binary_put_also_requires_and_accepts_csrf() -> None:
    status, _ = await _call("PUT", "/thing/content", VALID, content=b"\x00" * 1024)
    assert status == 200


async def test_reads_do_not_require_csrf() -> None:
    assert (await _call("GET", "/thing", {}))[0] == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": ORIGIN},  # sin token
        {"Origin": ORIGIN, deps.CSRF_HEADER: "forged"},
        {deps.CSRF_HEADER: VALID[deps.CSRF_HEADER]},  # sin Origin
        {"Origin": "https://evil.example", deps.CSRF_HEADER: VALID[deps.CSRF_HEADER]},
        {"Origin": f"{ORIGIN}.evil.example", deps.CSRF_HEADER: VALID[deps.CSRF_HEADER]},
        # No ASCII: compare_digest(str) lanzaría TypeError y la respuesta sería 500.
        {"Origin": "https://caf\u00e9.example", deps.CSRF_HEADER: VALID[deps.CSRF_HEADER]},
        {"Origin": ORIGIN, deps.CSRF_HEADER: "t\u00fcken-no-ascii"},
    ],
    ids=[
        "no-token", "forged-token", "no-origin", "foreign-origin", "suffix-origin",
        "non-ascii-origin", "non-ascii-token",
    ],
)
@pytest.mark.parametrize(("method", "path"), [("POST", "/thing"), ("PUT", "/thing/content")])
async def test_mutation_without_valid_csrf_or_origin_is_rejected(
    method: str, path: str, headers: dict[str, str]
) -> None:
    status, body = await _call(method, path, headers)

    assert status == 403
    assert body["error"]["code"] == "csrf_invalid"  # type: ignore[index]
