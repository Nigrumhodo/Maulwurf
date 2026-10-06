"""A1.6 contra PostgreSQL real: CRUD de materias por tenant (I-S1-AN-07)."""
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, CSRF_HEADER
from app.main import app
from app.models import Audio, Subject, User
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


# --- POST /subjects -------------------------------------------------------------------


async def test_post_subject_creates_it_for_the_session_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await _actor(db_session)
    # El commit del handler "vence" los objetos de la sesión compartida: guardamos el id antes.
    user_id = actor.user.id

    response = await client.post(
        "/subjects",
        json={"name": "Cálculo", "color": "#112233", "teacher": "Prof. Gauss"},
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Cálculo"
    assert data["color"] == "#112233"
    assert data["teacher"] == "Prof. Gauss"
    assert data["class_count"] == 0
    row = await db_session.get(Subject, uuid.UUID(data["id"]))
    assert row is not None
    assert row.user_id == user_id  # el dueño sale de la sesión, no del cuerpo


# --- GET /subjects --------------------------------------------------------------------


async def _subject(
    db: AsyncSession, actor: Actor, name: str, *, deleted: bool = False
) -> Subject:
    subject = Subject(
        user_id=actor.user.id, name=name, deleted_at=datetime.now(UTC) if deleted else None
    )
    db.add(subject)
    await db.flush()
    return subject


async def _audio(
    db: AsyncSession, actor: Actor, subject: Subject, *, deleted: bool = False
) -> None:
    db.add(
        Audio(
            user_id=actor.user.id,
            subject_id=subject.id,
            class_date=date(2026, 9, 21),
            class_timezone="America/Bogota",
            language_code="es",
            deleted_at=datetime.now(UTC) if deleted else None,
        )
    )
    await db.flush()


async def test_get_subjects_lists_only_own_active_subjects_with_class_count(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await _actor(db_session)
    bob = await _actor(db_session)
    biologia = await _subject(db_session, alice, "Biología")
    await _subject(db_session, alice, "cálculo")  # minúscula: el orden ignora mayúsculas
    await _subject(db_session, alice, "Vieja", deleted=True)  # borrada: no se lista
    await _subject(db_session, bob, "Ajena")  # de otro usuario: no se lista
    await _audio(db_session, alice, biologia)
    await _audio(db_session, alice, biologia)
    await _audio(db_session, alice, biologia, deleted=True)  # clase borrada: no cuenta

    response = await client.get("/subjects", cookies=alice.cookies)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["name"], i["class_count"]) for i in items] == [("Biología", 2), ("cálculo", 0)]


async def test_get_subjects_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/subjects")

    assert response.status_code == 401
