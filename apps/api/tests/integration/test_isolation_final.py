"""I-S4-AN-07 (G3): final isolation matrix and orphan sweep after deletions.

User B must not reach anything of user A on any private channel: repository helpers,
library reads, transcript exports, retry/reupload/cancel, SSE progress and chat, search,
conversations, notifications, Calendar mirrors, integrations and account-deletion status.
The matrix uses two complete tenants and two ids per channel (A's real id and a random one)
so a foreign resource is indistinguishable from a missing one (ADR-0006).

The second half runs the A4.7 read-only sweep. A live class is clean; marking it deleted
without purging its derivatives must make every check fire; the real ``DELETE /audios``
purge and account deletion must leave zero chunks, embeddings or other derived rows.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import chat_session_factory, progress_session_factory
from app.core.providers import get_embedding_provider
from app.main import app
from app.models import (
    AccountDeletion,
    Audio,
    CalendarEvent,
    CalendarSettings,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    IndexGeneration,
    IngestionAttempt,
    Message,
    MessageSource,
    Notification,
    ProcessingRun,
    Segment,
    Task,
    TaskRevision,
    TaskSource,
    Transcript,
)
from app.models.base import Base, TenantOwned
from app.services.indexing import chunk_tsvector
from app.services.notifications import dedupe_key
from app.services.orphan_sweep import ORPHAN_CHECKS, sweep_orphans
from app.services.runs import INDEX_CONFIG_VERSION
from app.services.tenant import get_owned, owned_by
from tests.integration import actors
from tests.integration.actors import FACTORIES, Actor

pytestmark = pytest.mark.integration

DIMENSION = 1536
ALICE_TEXT = "alpha secreto unico de la clase de alice"
BOB_TEXT = "beta notas privadas de la clase de bob"
TENANT_MODELS: list[type[TenantOwned]] = sorted(
    (mapper.class_ for mapper in Base.registry.mappers if issubclass(mapper.class_, TenantOwned)),
    key=lambda model: model.__name__,
)


class FakeEmbeddingProvider:
    """Deterministic provider: one fixed vector per input, no network."""

    model = "test-model"
    dimension = DIMENSION
    batch_size = 100

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        vector = [1.0] + [0.0] * (DIMENSION - 1)
        return [list(vector) for _ in texts]


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
async def client(
    db_session: AsyncSession, provider: FakeEmbeddingProvider
) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def search_quota_cleanup(redis: tuple[Any, str]) -> AsyncIterator[None]:
    """Remove the per-user search counters this module creates.

    The Redis policy sweep expects only catalogued `arq:`/`mw:` keys. The quota key is not
    part of that catalog, so the integration run must not leak it into the next module.
    """
    client, _queue = redis
    try:
        yield
    finally:
        keys = [key async for key in client.scan_iter(match="search:quota:*")]
        if keys:
            await client.delete(*keys)


@dataclass(frozen=True)
class SeededClass:
    subject_id: uuid.UUID
    audio_id: uuid.UUID
    attempt_id: uuid.UUID
    transcript_id: uuid.UUID
    segment_id: uuid.UUID
    chunk_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    task_id: uuid.UUID
    notification_id: uuid.UUID
    calendar_event_id: uuid.UUID
    text: str


async def _seed_full_class(
    db: AsyncSession, actor: Actor, *, text: str = ALICE_TEXT
) -> SeededClass:
    """Create one class with every durable derivative the sweep knows about."""
    subject = await actors.make_subject(db, actor)
    audio = await actors.make_audio(db, actor, subject=subject)
    audio.duration_seconds = 1.0
    attempt = await actors.make_attempt(db, actor, audio=audio)
    attempt.status = "succeeded"
    attempt.cleanup_status = "verified"

    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision="word",
        text=text,
        char_count=len(text),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = transcript.version

    await db.execute(
        update(IndexGeneration)
        .where(
            IndexGeneration.user_id == actor.user_id,
            IndexGeneration.status == "active",
        )
        .values(status="retired")
        .execution_options(synchronize_session=False)
    )
    generation = IndexGeneration(
        user_id=actor.user_id,
        version=f"final-{uuid.uuid4().hex}",
        model="test-model",
        dimension=DIMENSION,
        status="active",
        chunk_count=1,
    )
    db.add(generation)
    await db.flush()
    audio.active_index_version = generation.version

    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=1,
        text=text,
        char_start=0,
        char_end=len(text),
        t_start=0.0,
        t_end=1.0,
    )
    chunk = Chunk(
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_id=transcript.id,
        transcript_version=transcript.version,
        index_version=generation.version,
        ordinal=1,
        content=text,
        char_start=0,
        char_end=len(text),
        token_count=len(text.split()),
        tsv=chunk_tsvector("spanish", text),
    )
    db.add_all([segment, chunk])
    await db.flush()
    db.add(
        ChunkSegment(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            segment_id=segment.id,
            overlap_span={
                "segment_ordinal": 1,
                "inicio_char": 0,
                "fin_char": len(text),
            },
        )
    )
    db.add(
        Embedding(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            index_version=generation.version,
            model=generation.model,
            dimension=DIMENSION,
            vector=[1.0] + [0.0] * (DIMENSION - 1),
        )
    )

    conversation = Conversation(
        user_id=actor.user_id, mode="class", audio_id=audio.id, title="Clase"
    )
    db.add(conversation)
    await db.flush()
    message = Message(
        user_id=actor.user_id,
        conversation_id=conversation.id,
        role="assistant",
        content=f"respuesta sobre {text}",
        citas=[],
    )
    db.add(message)
    await db.flush()
    db.add(
        MessageSource(
            user_id=actor.user_id,
            message_id=message.id,
            kind="transcript",
            audio_id=audio.id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            chunk_id=chunk.id,
            segment_id=segment.id,
        )
    )

    task = Task(
        user_id=actor.user_id,
        audio_id=audio.id,
        subject_id=subject.id,
        source="extraction",
        tipo="tarea",
        titulo=f"Tarea de {text}",
        timezone="America/Bogota",
        all_day=True,
        date_status="missing",
    )
    db.add(task)
    await db.flush()
    db.add(
        TaskSource(
            user_id=actor.user_id,
            task_id=task.id,
            audio_id=audio.id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            segment_id=segment.id,
            char_start=0,
            char_end=len(text),
        )
    )
    db.add(
        TaskRevision(
            user_id=actor.user_id,
            task_id=task.id,
            base_task_version=task.version,
            revision_type="edit_of_confirmed",
            proposed_fields={"titulo": "Tarea revisada"},
        )
    )
    occurrence = date(2026, 11, 9)
    notification = Notification(
        user_id=actor.user_id,
        task_id=task.id,
        type="reminder_t24",
        occurrence=occurrence,
        due_version=task.due_version,
        scheduled_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="reminder_t24",
            task_id=task.id,
            due_version=task.due_version,
            occurrence=occurrence,
        ),
    )
    db.add(notification)
    start = datetime.now(UTC) + timedelta(days=1)
    calendar_event = CalendarEvent(
        user_id=actor.user_id,
        task_id=task.id,
        calendar_id="primary",
        event_id=f"event-{uuid.uuid4().hex}",
        source="maulwurf",
        title=f"Evento de {text}",
        start_at=start,
        end_at=start + timedelta(hours=1),
        last_synced_at=datetime.now(UTC),
    )
    db.add(calendar_event)
    db.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="index",
            transcript_version=transcript.version,
            config_version=INDEX_CONFIG_VERSION,
            status="succeeded",
        )
    )
    db.add(
        CalendarSettings(
            user_id=actor.user_id, calendar_read_calendar_ids=["primary"]
        )
    )
    await db.flush()
    return SeededClass(
        subject_id=subject.id,
        audio_id=audio.id,
        attempt_id=attempt.id,
        transcript_id=transcript.id,
        segment_id=segment.id,
        chunk_id=chunk.id,
        conversation_id=conversation.id,
        message_id=message.id,
        task_id=task.id,
        notification_id=notification.id,
        calendar_event_id=calendar_event.id,
        text=text,
    )


async def _count(db: AsyncSession, model: type[Any], **where: Any) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return int(await db.scalar(query) or 0)


def _assert_indistinguishable(foreign: Response, random: Response) -> None:
    assert foreign.status_code == random.status_code == 404
    assert foreign.json() == random.json()
    assert foreign.json()["error"]["code"] == "not_found"


def _assert_no_leak(response: Response, actor: Actor, *secrets: str) -> None:
    assert str(actor.user_id) not in response.text
    assert actor.email not in response.text
    for secret in secrets:
        assert secret not in response.text


# --- Repository and schema barriers ----------------------------------------------------


def test_every_tenant_model_has_a_factory() -> None:
    assert TENANT_MODELS
    assert set(TENANT_MODELS) == set(FACTORIES)


@pytest.mark.parametrize("model", TENANT_MODELS, ids=lambda model: model.__name__)
async def test_repository_never_returns_or_lists_another_tenants_row(
    db_session: AsyncSession, model: type[TenantOwned]
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    row = await FACTORIES[model](db_session, alice)
    row_id = row.id

    assert await get_owned(db_session, model, bob.user_id, row_id) is None
    assert await get_owned(db_session, model, alice.user_id, row_id) is row
    bob_rows: list[Any] = list((await db_session.scalars(owned_by(model, bob.user_id))).all())
    assert row_id not in {candidate.id for candidate in bob_rows}


async def test_composite_derived_rows_reject_cross_tenant_links(
    db_session: AsyncSession,
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    alice_chunk = await actors.make_chunk(db_session, alice)
    bob_segment = await actors.make_segment(db_session, bob)

    async with db_session.begin_nested():
        db_session.add(
            ChunkSegment(
                user_id=bob.user_id,
                chunk_id=alice_chunk.id,
                segment_id=bob_segment.id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async with db_session.begin_nested():
        db_session.add(
            Embedding(
                user_id=bob.user_id,
                chunk_id=alice_chunk.id,
                index_version=alice_chunk.index_version,
                model="test-model",
                dimension=DIMENSION,
                vector=[0.0] * DIMENSION,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


# --- HTTP and SSE channels -------------------------------------------------------------


async def test_library_subject_and_audio_channels_never_serve_another_tenant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    a = await _seed_full_class(db_session, alice)
    b = await _seed_full_class(db_session, bob, text=BOB_TEXT)

    subjects = await client.get("/subjects", cookies=bob.cookies)
    assert subjects.status_code == 200
    assert {item["id"] for item in subjects.json()["items"]} == {str(b.subject_id)}
    _assert_no_leak(subjects, alice, a.text)

    foreign_patch = await client.patch(
        f"/subjects/{a.subject_id}",
        json={"name": "robada"},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_patch = await client.patch(
        f"/subjects/{uuid.uuid4()}",
        json={"name": "robada"},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(foreign_patch, random_patch)
    deleted = await client.delete(
        f"/subjects/{a.subject_id}", cookies=bob.cookies, headers=bob.headers
    )
    assert deleted.status_code == 404

    library = await client.get("/audios", cookies=bob.cookies)
    assert library.status_code == 200
    assert {item["id"] for item in library.json()["items"]} == {str(b.audio_id)}
    _assert_no_leak(library, alice, a.text)

    filtered = await client.get(
        f"/audios?subject_id={a.subject_id}", cookies=bob.cookies
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    paths = (
        f"/audios/{a.audio_id}",
        f"/audios/{a.audio_id}/transcript.txt",
        f"/audios/{a.audio_id}/transcript.srt",
        f"/audios/{a.audio_id}/runs",
    )
    for path in paths:
        foreign = await client.get(path, cookies=bob.cookies)
        random = await client.get(
            path.replace(str(a.audio_id), str(uuid.uuid4())), cookies=bob.cookies
        )
        _assert_indistinguishable(foreign, random)
        _assert_no_leak(foreign, alice, a.text)

    retry = await client.post(
        f"/audios/{a.audio_id}/retry",
        json={"stages": ["index"], "transcript_version": 1},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_retry = await client.post(
        f"/audios/{uuid.uuid4()}/retry",
        json={"stages": ["index"], "transcript_version": 1},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(retry, random_retry)

    reupload = await client.post(
        f"/audios/{a.audio_id}/reupload", cookies=bob.cookies, headers=bob.headers
    )
    assert reupload.status_code == 404

    cancel = await client.post(
        f"/audios/{a.audio_id}/attempts/{a.attempt_id}/cancel",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_cancel = await client.post(
        f"/audios/{uuid.uuid4()}/attempts/{uuid.uuid4()}/cancel",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(cancel, random_cancel)

    put = await client.put(
        f"/audios/{a.audio_id}/content?attempt_id={a.attempt_id}",
        content=b"RIFF" + b"\x00" * 60,
        cookies=bob.cookies,
        headers={**bob.headers, "Content-Type": "audio/wav"},
    )
    random_put = await client.put(
        f"/audios/{uuid.uuid4()}/content?attempt_id={uuid.uuid4()}",
        content=b"RIFF" + b"\x00" * 60,
        cookies=bob.cookies,
        headers={**bob.headers, "Content-Type": "audio/wav"},
    )
    _assert_indistinguishable(put, random_put)

    delete = await client.delete(
        f"/audios/{a.audio_id}", cookies=bob.cookies, headers=bob.headers
    )
    assert delete.status_code == 404
    assert await _count(db_session, Transcript, user_id=alice.user_id) == 1


async def test_search_and_chat_channels_never_serve_another_tenant(
    client: AsyncClient,
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
    search_quota_cleanup: None,
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    a = await _seed_full_class(db_session, alice)
    b = await _seed_full_class(db_session, bob, text=BOB_TEXT)

    own_search = await client.get(
        "/search", params={"q": a.text}, cookies=bob.cookies
    )
    assert own_search.status_code == 200
    assert [row["chunk_id"] for row in own_search.json()["results"]] == [str(b.chunk_id)]
    _assert_no_leak(own_search, alice, a.text)
    assert provider.calls >= 1

    owner_search = await client.get(
        "/search", params={"q": a.text}, cookies=alice.cookies
    )
    assert owner_search.status_code == 200
    assert [row["chunk_id"] for row in owner_search.json()["results"]] == [str(a.chunk_id)]

    foreign_filter = await client.get(
        "/search",
        params={"q": "clase", "subject_id": str(a.subject_id)},
        cookies=bob.cookies,
    )
    assert foreign_filter.status_code == 200
    assert foreign_filter.json()["results"] == []

    conversations = await client.get("/chat/conversations", cookies=bob.cookies)
    assert conversations.status_code == 200
    assert {item["id"] for item in conversations.json()["items"]} == {
        str(b.conversation_id)
    }
    _assert_no_leak(conversations, alice, a.text)

    foreign_history = await client.get(
        f"/chat/conversations/{a.conversation_id}/messages", cookies=bob.cookies
    )
    random_history = await client.get(
        f"/chat/conversations/{uuid.uuid4()}/messages", cookies=bob.cookies
    )
    _assert_indistinguishable(foreign_history, random_history)
    _assert_no_leak(foreign_history, alice, a.text)

    foreign_delete = await client.delete(
        f"/chat/conversations/{a.conversation_id}",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_delete = await client.delete(
        f"/chat/conversations/{uuid.uuid4()}",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(foreign_delete, random_delete)
    _assert_no_leak(foreign_delete, alice, a.text)

    foreign_scope = await client.post(
        "/chat/conversations",
        json={"mode": "class", "audio_id": str(a.audio_id)},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    assert foreign_scope.status_code == 404
    foreign_subject = await client.post(
        "/chat/conversations",
        json={"mode": "subject", "subject_id": str(a.subject_id)},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    assert foreign_subject.status_code == 404

    cancel = await client.post(
        f"/chat/conversations/{a.conversation_id}/messages/{a.message_id}/cancel",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_cancel = await client.post(
        f"/chat/conversations/{uuid.uuid4()}/messages/{uuid.uuid4()}/cancel",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(cancel, random_cancel)

    owner_history = await client.get(
        f"/chat/conversations/{a.conversation_id}/messages", cookies=alice.cookies
    )
    assert owner_history.status_code == 200
    assert a.text in owner_history.text


async def test_notifications_calendar_integrations_and_deletion_are_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    a = await _seed_full_class(db_session, alice)
    b = await _seed_full_class(db_session, bob, text=BOB_TEXT)
    await actors.make_google_credential(db_session, alice)
    operation = AccountDeletion(
        former_user_id=alice.user_id,
        status="pending",
        delete_events=False,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db_session.add(operation)
    await db_session.flush()
    operation_id = operation.id

    me = await client.get("/me", cookies=bob.cookies)
    assert me.status_code == 200
    assert me.json()["id"] == str(bob.user_id)
    _assert_no_leak(me, alice, a.text)
    choose_owner = await client.patch(
        "/me",
        json={"timezone": "Europe/Madrid", "user_id": str(alice.user_id)},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    assert choose_owner.status_code == 422
    _assert_no_leak(choose_owner, alice, a.text)

    notifications = await client.get("/notifications", cookies=bob.cookies)
    assert notifications.status_code == 200
    assert {item["id"] for item in notifications.json()["items"]} == {
        str(b.notification_id)
    }
    _assert_no_leak(notifications, alice, a.text)

    resend = await client.post(
        f"/notifications/{a.notification_id}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_resend = await client.post(
        f"/notifications/{uuid.uuid4()}/resend",
        json={"acknowledge_duplicate_risk": True},
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(resend, random_resend)

    events = await client.get("/calendar/events", cookies=bob.cookies)
    assert events.status_code == 200
    assert {event["id"] for event in events.json()["events"]} == {
        str(b.calendar_event_id)
    }
    _assert_no_leak(events, alice, a.text)

    conflicts = await client.get("/calendar/conflicts", cookies=bob.cookies)
    assert conflicts.status_code == 200
    assert conflicts.json()["time_conflicts"] == []
    assert conflicts.json()["deadline_clashes"] == []
    _assert_no_leak(conflicts, alice, a.text)

    status = await client.get("/integrations/status", cookies=bob.cookies)
    owner_status = await client.get("/integrations/status", cookies=alice.cookies)
    assert status.status_code == owner_status.status_code == 200
    assert status.json()["google"]["status"] == "disconnected"
    assert status.json()["google"]["scopes"] == []
    assert owner_status.json()["google"]["status"] == "connected"
    _assert_no_leak(status, alice, a.text)

    foreign_status = await client.get(
        f"/me/deletion/{operation_id}", cookies=bob.cookies
    )
    random_status = await client.get(
        f"/me/deletion/{uuid.uuid4()}", cookies=bob.cookies
    )
    _assert_indistinguishable(foreign_status, random_status)
    foreign_resume = await client.post(
        f"/me/deletion/{operation_id}/resume",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    random_resume = await client.post(
        f"/me/deletion/{uuid.uuid4()}/resume",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    _assert_indistinguishable(foreign_resume, random_resume)


async def test_sse_channels_reject_foreign_ids_before_streaming(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as setup:
        alice, bob = await actors.make_actor(setup), await actors.make_actor(setup)
        seeded = await _seed_full_class(setup, alice)
        await setup.commit()
        audio_id = seeded.audio_id
        conversation_id = seeded.conversation_id

    app.dependency_overrides[progress_session_factory] = lambda: factory
    app.dependency_overrides[chat_session_factory] = lambda: factory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            foreign_progress = await http.get(
                f"/audios/{audio_id}/progress", cookies=bob.cookies
            )
            random_progress = await http.get(
                f"/audios/{uuid.uuid4()}/progress", cookies=bob.cookies
            )
            _assert_indistinguishable(foreign_progress, random_progress)
            owner_progress = await http.get(
                f"/audios/{audio_id}/progress", cookies=alice.cookies
            )
            assert owner_progress.status_code == 200
            assert owner_progress.headers["content-type"].startswith("text/event-stream")

            foreign_stream = await http.post(
                f"/chat/conversations/{conversation_id}/messages",
                json={"content": "hola"},
                cookies=bob.cookies,
                headers=bob.headers,
            )
            random_stream = await http.post(
                f"/chat/conversations/{uuid.uuid4()}/messages",
                json={"content": "hola"},
                cookies=bob.cookies,
                headers=bob.headers,
            )
            _assert_indistinguishable(foreign_stream, random_stream)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


# --- Orphan sweep and deletions --------------------------------------------------------


async def test_sweep_is_clean_on_live_data_and_detects_every_failed_purge(
    db_session: AsyncSession,
) -> None:
    alice = await actors.make_actor(db_session)
    seeded = await _seed_full_class(db_session, alice)

    clean = await sweep_orphans(db_session, user_ids=[alice.user_id])
    assert clean.clean is True
    assert clean.total == 0

    audio = await db_session.get(Audio, seeded.audio_id)
    assert audio is not None
    audio.deleted_at = datetime.now(UTC)
    await db_session.flush()

    broken = await sweep_orphans(db_session, user_ids=[alice.user_id])
    assert broken.clean is False
    assert {finding.name for finding in broken.findings if finding.count} == {
        check.name for check in ORPHAN_CHECKS
    }


async def test_class_deletion_purges_every_derived_row_and_the_sweep_stays_clean(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice = await actors.make_actor(db_session)
    seeded = await _seed_full_class(db_session, alice)

    response = await client.delete(
        f"/audios/{seeded.audio_id}", cookies=alice.cookies, headers=alice.headers
    )

    assert response.status_code == 202
    purged = response.json()["purged"]
    assert purged["transcripts_deleted"] == 1
    assert purged["segments_deleted"] == 1
    assert purged["chunks_deleted"] == 1
    assert purged["chunk_segments_deleted"] == 1
    assert purged["embeddings_deleted"] == 1
    assert purged["message_sources_deleted"] == 1
    assert purged["tasks_deleted"] == 1
    assert purged["task_sources_deleted"] == 1
    assert purged["task_revisions_deleted"] == 1

    for model, where in (
        (Transcript, {"user_id": alice.user_id}),
        (Segment, {"user_id": alice.user_id}),
        (Chunk, {"user_id": alice.user_id}),
        (ChunkSegment, {"user_id": alice.user_id}),
        (Embedding, {"user_id": alice.user_id}),
        (MessageSource, {"user_id": alice.user_id}),
        (Task, {"user_id": alice.user_id}),
        (TaskSource, {"user_id": alice.user_id}),
        (TaskRevision, {"user_id": alice.user_id}),
        (Notification, {"user_id": alice.user_id}),
    ):
        assert await _count(db_session, model, **where) == 0, model.__name__

    audio = await db_session.get(Audio, seeded.audio_id)
    assert audio is not None
    assert audio.deleted_at is not None
    assert audio.active_transcript_version is None

    report = await sweep_orphans(db_session, user_ids=[alice.user_id])
    assert report.clean is True, report.as_dict()
    assert (
        await client.get(f"/audios/{seeded.audio_id}", cookies=alice.cookies)
    ).status_code == 404


async def test_account_deletion_leaves_no_derived_rows_and_the_sweep_stays_clean(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    a = await _seed_full_class(db_session, alice)
    b = await _seed_full_class(db_session, bob, text=BOB_TEXT)

    response = await client.request(
        "DELETE",
        "/me",
        json={"delete_maulwurf_events": False},
        cookies=alice.cookies,
        headers=alice.headers,
    )

    assert response.status_code == 202
    operation_id = uuid.UUID(response.json()["deletion_operation_id"])
    operation = await db_session.get(AccountDeletion, operation_id)
    assert operation is not None
    assert operation.status == "completed"

    for model in (
        Transcript,
        Segment,
        Chunk,
        ChunkSegment,
        Embedding,
        MessageSource,
        Task,
        TaskSource,
        TaskRevision,
        Notification,
        Conversation,
        CalendarEvent,
        IngestionAttempt,
        ProcessingRun,
    ):
        assert await _count(db_session, model, user_id=alice.user_id) == 0, model.__name__

    former = await sweep_orphans(db_session, user_ids=[alice.user_id])
    assert former.clean is True, former.as_dict()
    remaining = await sweep_orphans(db_session, user_ids=[bob.user_id])
    assert remaining.clean is True, remaining.as_dict()
    global_report = await sweep_orphans(db_session)
    assert global_report.clean is True, global_report.as_dict()

    # The other tenant keeps its own class and its own data.
    library = await client.get("/audios", cookies=bob.cookies)
    assert library.status_code == 200
    assert {item["id"] for item in library.json()["items"]} == {str(b.audio_id)}
    assert a.audio_id not in {item["id"] for item in library.json()["items"]}
