"""A1.9 / I-S1-AN-05 (G3): el usuario B no ve recursos de A ni adivinando IDs.

ADR-0006: el filtro de tenant es obligatorio en la aplicación, `user_id` sale solo de la
sesión y un recurso ajeno responde igual que uno inexistente (404 indistinguible).

Capas:
- Repositorio: `get_owned`/`owned_by` sobre cada modelo `TenantOwned`, descubierto en el
  registro del ORM (una tabla nueva sin factoría hace fallar la suite).
- BD: FKs compuestas `(user_id, ...)`. El caso audio -> materia ajena ya está en
  `test_core_schema.py::test_audio_cannot_reference_another_users_subject`; aquí se cubre
  intento -> audio ajeno.
- HTTP: matriz H1-H12 del plan de A1.9 con dos sesiones válidas. Casos previos en
  `test_a18_endpoints.py::test_post_audios_with_another_users_subject_is_404` y
  `::test_put_on_another_users_audio_is_404`; aquí se exige además que la respuesta sea
  idéntica a la de un UUID aleatorio y que no filtre datos de A.

Pendiente cuando lleguen A1.6 (`/subjects/{id}`) y A1.3 (callback OAuth): añadir sus rutas
a la matriz HTTP.
"""
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, CSRF_HEADER
from app.main import app
from app.models import Audio, Base, GoogleCredential, IngestionAttempt
from app.models.base import TenantOwned
from app.services import sessions
from app.services.tenant import get_owned, owned_by
from tests.integration.actors import (
    FACTORIES,
    GOOGLE_SCOPES,
    Actor,
    make_actor,
    make_attempt,
    make_google_credential,
    make_subject,
)

pytestmark = pytest.mark.integration

TENANT_MODELS: list[type[TenantOwned]] = sorted(
    (m.class_ for m in Base.registry.mappers if issubclass(m.class_, TenantOwned)),
    key=lambda model: model.__name__,
)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    # Los commit() de los handlers liberan el savepoint del fixture; nada escapa del test.
    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _pair(db: AsyncSession) -> tuple[Actor, Actor]:
    return await make_actor(db), await make_actor(db)


def _assert_no_leak(response: Response, owner: Actor) -> None:
    """H12: ninguna respuesta a otro usuario menciona el id ni el email del dueño."""
    assert str(owner.user_id) not in response.text
    assert owner.email not in response.text


def _assert_indistinguishable(foreign: Response, random: Response) -> None:
    """Recurso ajeno y UUID aleatorio: mismo status y mismo cuerpo (ADR-0006)."""
    assert foreign.status_code == random.status_code == 404
    assert foreign.json() == random.json()
    assert foreign.json()["error"]["code"] == "not_found"


async def _count(db: AsyncSession, model: type[Any], **where: Any) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return int(await db.scalar(query) or 0)


def _audio_body(subject_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(subject_id),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


async def _post_audio(client: AsyncClient, actor: Actor, body: dict[str, Any]) -> Response:
    return await client.post("/audios", json=body, cookies=actor.cookies, headers=actor.headers)


async def _put_content(
    client: AsyncClient, actor: Actor, audio_id: object, attempt_id: object
) -> Response:
    return await client.put(
        f"/audios/{audio_id}/content?attempt_id={attempt_id}",
        content=b"RIFF" + b"\x00" * 60,
        cookies=actor.cookies,
        headers={**actor.headers, "Content-Type": "audio/wav"},
    )


# --- Repositorio ----------------------------------------------------------------------


def test_every_tenant_model_has_a_factory() -> None:
    assert TENANT_MODELS, "el registro del ORM no expone modelos TenantOwned"
    assert set(TENANT_MODELS) == set(FACTORIES)


@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__name__)
async def test_other_user_cannot_get_owned(
    db_session: AsyncSession, model: type[TenantOwned]
) -> None:
    alice, bob = await _pair(db_session)
    row = await FACTORIES[model](db_session, alice)

    row_id = row.id
    assert await get_owned(db_session, model, bob.user_id, row_id) is None
    # Control positivo: sin él, un get_owned que siempre devuelva None pasaría.
    assert await get_owned(db_session, model, alice.user_id, row_id) is row
    bob_rows: list[Any] = list((await db_session.scalars(owned_by(model, bob.user_id))).all())
    assert row_id not in {r.id for r in bob_rows}
    assert all(r.user_id == bob.user_id for r in bob_rows)


@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda m: m.__name__)
async def test_random_ids_are_indistinguishable(
    db_session: AsyncSession, model: type[TenantOwned]
) -> None:
    alice, bob = await _pair(db_session)
    row = await FACTORIES[model](db_session, alice)

    foreign = await get_owned(db_session, model, bob.user_id, row.id)
    random = await get_owned(db_session, model, bob.user_id, uuid.uuid4())

    assert foreign is None and random is None


async def test_google_credential_is_read_only_by_session_user(db_session: AsyncSession) -> None:
    # `routers/integrations.py` la lee por PK con el `user_id` de la sesión.
    alice, bob = await _pair(db_session)
    credential = await make_google_credential(db_session, alice)

    assert await db_session.get(GoogleCredential, bob.user_id) is None
    assert await db_session.get(GoogleCredential, alice.user_id) is credential


# --- Integridad en BD -----------------------------------------------------------------


async def test_attempt_cannot_reference_another_users_audio(db_session: AsyncSession) -> None:
    alice, bob = await _pair(db_session)
    alice_attempt = await make_attempt(db_session, alice)

    async with db_session.begin_nested():
        db_session.add(
            IngestionAttempt(
                user_id=bob.user_id,
                audio_id=alice_attempt.audio_id,
                privacy_notice_version=settings.privacy_notice_version,
                cloud_processing_accepted_at=alice_attempt.cloud_processing_accepted_at,
                third_party_voice_acknowledged_at=alice_attempt.cloud_processing_accepted_at,
                declared_providers={"asr": "test"},
                status="rejected",  # fuera del índice de intento activo único
                owner_instance="test",
                fencing_token=1,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


# --- HTTP: lo que ve un atacante con sesión válida ------------------------------------


async def test_h1_post_audios_with_foreign_subject_is_indistinguishable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    alice_subject = await make_subject(db_session, alice)
    before = (await _count(db_session, Audio), await _count(db_session, IngestionAttempt))

    foreign = await _post_audio(client, bob, _audio_body(alice_subject.id))
    random = await _post_audio(client, bob, _audio_body(uuid.uuid4()))

    _assert_indistinguishable(foreign, random)
    _assert_no_leak(foreign, alice)
    after = (await _count(db_session, Audio), await _count(db_session, IngestionAttempt))
    assert after == before


async def test_h2_put_on_foreign_audio_and_attempt_is_indistinguishable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    attempt = await make_attempt(db_session, alice)

    foreign = await _put_content(client, bob, attempt.audio_id, attempt.id)
    random = await _put_content(client, bob, uuid.uuid4(), uuid.uuid4())
    # Control positivo: el intento de A es válido y vigente, así que A llega hasta el 503.
    owner = await _put_content(client, alice, attempt.audio_id, attempt.id)

    _assert_indistinguishable(foreign, random)
    _assert_no_leak(foreign, alice)
    assert owner.status_code == 503
    assert owner.json()["error"]["code"] == "capacity_unavailable"


async def test_h3_put_on_own_audio_with_foreign_attempt_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    alice_attempt = await make_attempt(db_session, alice)
    bob_attempt = await make_attempt(db_session, bob)

    mixed = await _put_content(client, bob, bob_attempt.audio_id, alice_attempt.id)
    random = await _put_content(client, bob, bob_attempt.audio_id, uuid.uuid4())

    _assert_indistinguishable(mixed, random)
    _assert_no_leak(mixed, alice)


async def test_h4_put_on_foreign_audio_with_own_attempt_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    alice_attempt = await make_attempt(db_session, alice)
    bob_attempt = await make_attempt(db_session, bob)

    mixed = await _put_content(client, bob, alice_attempt.audio_id, bob_attempt.id)
    random = await _put_content(client, bob, uuid.uuid4(), bob_attempt.id)

    _assert_indistinguishable(mixed, random)
    _assert_no_leak(mixed, alice)


async def test_h5_post_audios_cannot_choose_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    bob_subject = await make_subject(db_session, bob)

    response = await _post_audio(
        client, bob, _audio_body(bob_subject.id, user_id=str(alice.user_id))
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    _assert_no_leak(response, alice)
    assert await _count(db_session, Audio, user_id=alice.user_id) == 0
    assert await _count(db_session, Audio, user_id=bob.user_id) == 0


async def test_h6_patch_me_cannot_target_another_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    alice_timezone = alice.user.timezone

    response = await client.patch(
        "/me",
        json={"timezone": "Europe/Madrid", "user_id": str(alice.user_id)},
        cookies=bob.cookies,
        headers=bob.headers,
    )

    assert response.status_code == 422
    _assert_no_leak(response, alice)
    await db_session.refresh(alice.user)
    await db_session.refresh(bob.user)
    assert alice.user.timezone == alice_timezone
    assert bob.user.timezone != "Europe/Madrid"


async def test_h7_get_me_returns_only_the_session_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)

    response = await client.get("/me", cookies=bob.cookies)

    assert response.status_code == 200
    assert response.json()["id"] == str(bob.user_id)
    assert response.json()["email"] == bob.email
    _assert_no_leak(response, alice)


async def test_h8_integrations_status_ignores_other_users_credential(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    await make_google_credential(db_session, alice)

    response = await client.get("/integrations/status", cookies=bob.cookies)
    owner = await client.get("/integrations/status", cookies=alice.cookies)

    assert response.status_code == 200
    assert response.json()["google"]["status"] == "disconnected"
    assert response.json()["google"]["scopes"] == []
    for scope in GOOGLE_SCOPES:
        assert scope not in response.text
    _assert_no_leak(response, alice)
    # Control positivo: la credencial existe y A sí la ve.
    assert owner.json()["google"]["status"] == "connected"


async def test_h9_logout_revokes_only_the_callers_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)

    response = await client.post("/auth/logout", cookies=bob.cookies, headers=bob.headers)

    assert response.status_code == 204
    _assert_no_leak(response, alice)
    assert await sessions.resolve_session(db_session, alice.cookies[COOKIE_NAME]) is not None
    assert (await client.get("/me", cookies=alice.cookies)).status_code == 200
    assert (await client.get("/me", cookies=bob.cookies)).status_code == 401


async def test_h10_csrf_token_of_another_session_is_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await _pair(db_session)
    bob_timezone = bob.user.timezone

    response = await client.patch(
        "/me",
        json={"timezone": "Europe/Madrid"},
        cookies=bob.cookies,
        headers={**bob.headers, CSRF_HEADER: alice.csrf_token},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_invalid"
    _assert_no_leak(response, alice)
    await db_session.refresh(bob.user)
    assert bob.user.timezone == bob_timezone


@pytest.mark.parametrize(
    ("audio_id", "attempt_id"),
    [
        ("not-a-uuid-SECRETO", "x-SECRETO"),
        (uuid.UUID(int=0), "x-SECRETO"),
        ("not-a-uuid-SECRETO", uuid.UUID(int=0)),
    ],
)
async def test_h11_malformed_ids_are_422_without_echo(
    client: AsyncClient, db_session: AsyncSession, audio_id: object, attempt_id: object
) -> None:
    alice, bob = await _pair(db_session)

    response = await _put_content(client, bob, audio_id, attempt_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert "SECRETO" not in response.text
    _assert_no_leak(response, alice)
