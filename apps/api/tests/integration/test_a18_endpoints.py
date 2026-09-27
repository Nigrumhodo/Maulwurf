"""A1.8 contra PostgreSQL real: contrato HTTP de ingesta S1, perfil, logout e integraciones.

Matriz de verificación de S1.md §3 (A1.8): códigos, cabeceras, envelope y ausencia de
secretos. Los flujos completos (recepción real, dedupe) los cubre S2.
"""
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, CSRF_HEADER
from app.main import app
from app.models import Audio, GoogleCredential, IngestionAttempt, Subject, User
from app.services import sessions

pytestmark = pytest.mark.integration


class Actor:
    """Usuario con sesión válida y las cabeceras que enviaría su navegador."""

    def __init__(self, user: User, issued: sessions.IssuedSession) -> None:
        self.user = user
        self.cookies = {COOKIE_NAME: issued.cookie_token}
        self.headers = {"Origin": settings.allowed_origin, CSRF_HEADER: issued.csrf_token}


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Los handlers confirman con commit(); dentro del savepoint del fixture eso no escapa
    # de la transacción que se revierte al final del test.
    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _actor(db: AsyncSession) -> Actor:
    user = User(google_sub=f"sub-{uuid.uuid4()}", email="student@example.test")
    db.add(user)
    await db.flush()
    return Actor(user, await sessions.create_session(db, user.id))


async def _subject(db: AsyncSession, actor: Actor) -> Subject:
    subject = Subject(user_id=actor.user.id, name=f"Materia {uuid.uuid4().hex[:6]}")
    db.add(subject)
    await db.flush()
    return subject


def _body(subject: Subject, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(subject.id),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "title": "Derivadas",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


async def _post_audio(client: AsyncClient, actor: Actor, body: dict[str, Any]) -> Any:
    return await client.post("/audios", json=body, cookies=actor.cookies, headers=actor.headers)


async def _count(db: AsyncSession, model: type[Any]) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


# --- POST /audios ---------------------------------------------------------------------


async def test_post_audios_creates_attempt_awaiting_upload(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    subject = await _subject(db_session, actor)

    response = await _post_audio(client, actor, _body(subject))

    assert response.status_code == 201
    data = response.json()
    assert data["outcome"] == "new"
    assert data["upload_url"] == (
        f"/audios/{data['audio_id']}/content?attempt_id={data['attempt_id']}"
    )
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(data["attempt_id"]))
    assert attempt is not None
    assert attempt.status == "awaiting_upload"
    assert attempt.cleanup_status == "pending"
    assert attempt.privacy_notice_version == settings.privacy_notice_version
    audio = await db_session.get(Audio, uuid.UUID(data["audio_id"]))
    assert audio is not None and audio.sha256 is None  # sin dedupe antes del hash (S2)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"cloud_processing_accepted": False}, "consent_required"),
        ({"third_party_voice_acknowledged": False}, "consent_required"),
        ({"privacy_notice_version": "2020-01-v0"}, "consent_required"),
        ({"language_code": "multi"}, "language_not_allowed"),
        ({"language_code": "de"}, "language_not_allowed"),
        ({"class_timezone": "Mars/Olympus"}, "validation_failed"),
        ({"class_date": "not-a-date"}, "validation_failed"),
        ({"unexpected": "field"}, "validation_failed"),
    ],
)
async def test_post_audios_rejects_before_reserving(
    client: AsyncClient, db_session: AsyncSession, overrides: dict[str, Any], code: str
) -> None:
    actor = await _actor(db_session)
    subject = await _subject(db_session, actor)
    before = (await _count(db_session, Audio), await _count(db_session, IngestionAttempt))

    response = await _post_audio(client, actor, _body(subject, **overrides))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    after = (await _count(db_session, Audio), await _count(db_session, IngestionAttempt))
    assert after == before


async def test_validation_errors_do_not_echo_submitted_values(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    subject = await _subject(db_session, actor)

    response = await _post_audio(client, actor, _body(subject, title="x" * 201 + "SECRETO"))

    assert response.status_code == 422
    assert "SECRETO" not in response.text
    assert "title" in response.json()["error"]["details"]["fields"]


async def test_post_audios_with_another_users_subject_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _actor(db_session)
    bob = await _actor(db_session)
    alice_subject = await _subject(db_session, alice)

    response = await _post_audio(client, bob, _body(alice_subject))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_post_audios_requires_csrf_and_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    body = _body(await _subject(db_session, actor))

    no_csrf = await client.post("/audios", json=body, cookies=actor.cookies)
    no_session = await client.post("/audios", json=body, headers=actor.headers)

    assert no_csrf.status_code == 403
    assert no_session.status_code == 401


# --- PUT /audios/{id}/content ---------------------------------------------------------


async def _reserved(client: AsyncClient, db: AsyncSession, actor: Actor) -> dict[str, Any]:
    response = await _post_audio(client, actor, _body(await _subject(db, actor)))
    assert response.status_code == 201
    data: dict[str, Any] = response.json()
    return data


async def _put(
    client: AsyncClient, actor: Actor, url: str, extra: dict[str, str] | None = None
) -> Any:
    headers = {**actor.headers, "Content-Type": "audio/wav", **(extra or {})}
    return await client.put(url, content=b"RIFF" + b"\x00" * 60, cookies=actor.cookies,
                            headers=headers)


async def test_put_with_valid_contract_is_503_until_s2(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None and attempt.status == "awaiting_upload"  # nada recibido


async def test_put_requires_csrf_like_any_mutation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)

    response = await _put(client, actor, reserved["upload_url"], {CSRF_HEADER: "forged"})

    assert response.status_code == 403


async def test_put_declared_too_large_is_413_without_reading(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)
    headers = {**actor.headers, "Content-Length": str(settings.upload_max_bytes + 1)}

    # Se envía un cuerpo corto con la cabecera declarada: el servidor decide sin leerlo.
    response = await client.put(
        reserved["upload_url"], cookies=actor.cookies, headers=headers, content=b""
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_put_chunked_without_content_length_is_still_503(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Fija el comportamiento de S1 antes de que S2 cuente bytes durante el streaming.
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"RIFF"
        yield b"\x00" * 60

    response = await client.put(
        reserved["upload_url"], cookies=actor.cookies, headers=actor.headers, content=chunks()
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 503


async def test_put_on_expired_attempt_is_410(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    attempt.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "upload_expired"


async def test_put_on_inactive_attempt_is_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    reserved = await _reserved(client, db_session, actor)
    attempt = await db_session.get(IngestionAttempt, uuid.UUID(reserved["attempt_id"]))
    assert attempt is not None
    attempt.status = "cancelled"
    await db_session.flush()

    response = await _put(client, actor, reserved["upload_url"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attempt_not_active"


async def test_put_on_another_users_audio_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _actor(db_session)
    bob = await _actor(db_session)
    reserved = await _reserved(client, db_session, alice)

    wrong_owner = await _put(client, bob, reserved["upload_url"])
    wrong_attempt = await _put(
        client, alice, f"/audios/{reserved['audio_id']}/content?attempt_id={uuid.uuid4()}"
    )

    assert wrong_owner.status_code == 404
    assert wrong_attempt.status_code == 404


# --- /me, logout, integraciones -------------------------------------------------------


async def test_patch_me_updates_timezone_and_stays_uncacheable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)

    ok = await client.patch(
        "/me", json={"timezone": "Europe/Madrid"}, cookies=actor.cookies, headers=actor.headers
    )
    bad = await client.patch(
        "/me", json={"timezone": "Nowhere/Zone"}, cookies=actor.cookies, headers=actor.headers
    )

    assert ok.status_code == 200
    assert ok.json()["timezone"] == "Europe/Madrid"
    assert ok.headers["cache-control"] == "no-store"
    assert bad.status_code == 422


async def test_logout_revokes_immediately_and_clears_cookie(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)

    response = await client.post("/auth/logout", cookies=actor.cookies, headers=actor.headers)
    after = await client.get("/me", cookies=actor.cookies)

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert after.status_code == 401


async def test_integrations_status_without_and_with_google(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)

    before = await client.get("/integrations/status", cookies=actor.cookies)
    db_session.add(
        GoogleCredential(
            user_id=actor.user.id, access_token_enc=b"\x00" * 40, key_version=1,
            scopes=["openid", "email"],
        )
    )
    await db_session.flush()
    after = await client.get("/integrations/status", cookies=actor.cookies)

    assert before.json()["google"] == {
        "status": "disconnected", "scopes": [], "token_expires_at": None,
        "last_refresh_at": None,
    }
    assert after.headers["cache-control"] == "no-store"
    assert after.json()["google"]["status"] == "connected"
    assert after.json()["google"]["scopes"] == ["openid", "email"]
    assert "access_token" not in after.text


async def test_openapi_exposes_contract_without_secrets(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    paths = response.json()["paths"]

    assert {"/audios", "/audios/{audio_id}/content", "/me", "/auth/logout",
            "/integrations/status"} <= set(paths)
    for marker in ("NVIDIA_API_KEY", "Bearer", "secret_key", "encryption_key"):
        assert marker not in response.text
    put = paths["/audios/{audio_id}/content"]["put"]["responses"]
    assert "202" not in put
    assert {"401", "403", "404", "409", "410", "413", "503"} <= set(put)


async def test_writes_persist_through_real_sessions(migrated_database_url: str) -> None:
    # Sin el savepoint del fixture: cada petición usa su propia sesión y su commit real,
    # igual que en producción. Comprueba que ningún handler depende de un commit implícito.
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def real_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async with factory() as setup:
        actor = await _actor(setup)
        subject = await _subject(setup, actor)
        await setup.commit()

    app.dependency_overrides[get_session] = real_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            created = await _post_audio(client, actor, _body(subject))
            patched = await client.patch(
                "/me", json={"timezone": "Asia/Tokyo"}, cookies=actor.cookies,
                headers=actor.headers,
            )
            logout = await client.post(
                "/auth/logout", cookies=actor.cookies, headers=actor.headers
            )
    finally:
        app.dependency_overrides.clear()

    assert (created.status_code, patched.status_code, logout.status_code) == (201, 200, 204)
    async with factory() as check:
        attempt = await check.get(IngestionAttempt, uuid.UUID(created.json()["attempt_id"]))
        user = await check.get(User, actor.user.id)
        revoked = await sessions.resolve_session(check, actor.cookies[COOKIE_NAME])
    await engine.dispose()
    assert attempt is not None and attempt.status == "awaiting_upload"
    assert user is not None and user.timezone == "Asia/Tokyo"
    assert revoked is None
