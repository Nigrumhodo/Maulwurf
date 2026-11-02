"""I-S2-AN-11: persisted and tenant-isolated M7 chat conversation surface.

A2.12 adds conversation create/list/history/delete over the A2.1 tables and the L2.5 SSE
turn. These cases run against real PostgreSQL: mode-scope coherence, tenant isolation,
keyset history, idempotent cancellation through the HTTP endpoint and the tombstone that
purges messages and `message_sources`. The streaming idempotency is covered by
`tests/integration/test_chat_sse.py`, which owns the chat session factory and fake providers.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import Audio, Conversation, Message, MessageSource, Subject
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()


async def _create(
    client: AsyncClient, actor: actors.Actor, **payload: object
) -> tuple[int, dict[str, object]]:
    response = await client.post(
        "/chat/conversations",
        json=payload,
        cookies=actor.cookies,
        headers=actor.headers,
    )
    return response.status_code, response.json()


async def _conversation(
    db: AsyncSession, actor: actors.Actor, *, mode: str = "all", **kwargs: object
) -> Conversation:
    conversation = Conversation(user_id=actor.user_id, mode=mode, **kwargs)
    db.add(conversation)
    await db.flush()
    return conversation


async def _message(
    db: AsyncSession,
    actor: actors.Actor,
    conversation: Conversation,
    *,
    role: str = "user",
    content: str = "pregunta",
    status: str = "completed",
    created_at: datetime | None = None,
    client_message_id: str | None = None,
    message_id: uuid.UUID | None = None,
) -> Message:
    message = Message(
        user_id=actor.user_id,
        conversation_id=conversation.id,
        role=role,
        content=content,
        status=status,
        client_message_id=client_message_id,
        created_at=created_at,
        id=message_id,
    )
    db.add(message)
    await db.flush()
    return message


# --- POST /chat/conversations -----------------------------------------------------------


async def test_i_s2_an_11_create_conversation_modes_are_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    alice_subject = await actors.make_subject(db_session, alice)
    alice_audio = await actors.make_audio(db_session, alice, subject=alice_subject)
    bob_subject = await actors.make_subject(db_session, bob)
    bob_audio = await actors.make_audio(db_session, bob, subject=bob_subject)
    # The handler commits the shared session, which expires these ORM instances; keep the
    # UUIDs so later assertions never trigger a lazy load outside the async context.
    alice_id = alice.user_id
    alice_subject_id = alice_subject.id
    alice_audio_id = alice_audio.id
    bob_subject_id = bob_subject.id
    bob_audio_id = bob_audio.id

    status, body = await _create(client, alice, mode="all", title="Todo")
    assert (status, body["mode"], body["subject_id"], body["audio_id"]) == (
        201,
        "all",
        None,
        None,
    )
    assert body["title"] == "Todo"
    assert body["last_message_at"] is None
    all_id = uuid.UUID(str(body["id"]))

    status, subject_body = await _create(
        client, alice, mode="subject", subject_id=str(alice_subject_id)
    )
    assert status == 201
    assert subject_body["subject_id"] == str(alice_subject_id)
    assert subject_body["audio_id"] is None

    status, class_body = await _create(client, alice, mode="class", audio_id=str(alice_audio_id))
    assert status == 201
    assert class_body["audio_id"] == str(alice_audio_id)
    assert class_body["subject_id"] is None

    # The owner comes from the session, never from a payload field.
    for created in (all_id, uuid.UUID(str(subject_body["id"])), uuid.UUID(str(class_body["id"]))):
        row = await db_session.get(Conversation, created)
        assert row is not None
        assert row.user_id == alice_id

    # A foreign or deleted scope is indistinguishable from a missing one.
    assert (await _create(client, alice, mode="subject", subject_id=str(bob_subject_id)))[0] == 404
    assert (await _create(client, alice, mode="class", audio_id=str(bob_audio_id)))[0] == 404

    now = datetime.now(UTC)
    subject_row = await db_session.get(Subject, alice_subject_id)
    audio_row = await db_session.get(Audio, alice_audio_id)
    assert subject_row is not None
    assert audio_row is not None
    subject_row.deleted_at = now
    audio_row.deleted_at = now
    await db_session.flush()
    assert (await _create(client, alice, mode="subject", subject_id=str(alice_subject_id)))[
        0
    ] == 404
    assert (await _create(client, alice, mode="class", audio_id=str(alice_audio_id)))[0] == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "all", "subject_id": "subject"},
        {"mode": "all", "audio_id": "audio"},
        {"mode": "subject"},
        {"mode": "subject", "subject_id": "subject", "audio_id": "audio"},
        {"mode": "class"},
        {"mode": "class", "subject_id": "subject", "audio_id": "audio"},
        {"mode": "everything"},
    ],
)
async def test_i_s2_an_11_mode_scope_mismatch_is_chat_mode_invalid(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, object]
) -> None:
    actor = await actors.make_actor(db_session)
    subject = await actors.make_subject(db_session, actor)
    audio = await actors.make_audio(db_session, actor, subject=subject)
    resolved = dict(payload)
    if "subject_id" in resolved:
        resolved["subject_id"] = str(subject.id)
    if "audio_id" in resolved:
        resolved["audio_id"] = str(audio.id)

    response = await client.post(
        "/chat/conversations",
        json=resolved,
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "chat_mode_invalid", response.json()
    assert response.json()["error"]["details"]["allowed_modes"] == ["all", "subject", "class"]


async def test_i_s2_an_11_create_requires_a_session_and_csrf(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    missing_session = await client.post("/chat/conversations", json={"mode": "all"})
    assert missing_session.status_code == 401

    missing_csrf = await client.post(
        "/chat/conversations", json={"mode": "all"}, cookies=actor.cookies
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_invalid"


async def test_i_s2_an_11_unknown_body_fields_and_blank_title_are_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    extra = await client.post(
        "/chat/conversations",
        json={"mode": "all", "user_id": str(actor.user_id)},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_failed"

    blank = await client.post(
        "/chat/conversations",
        json={"mode": "all", "title": "   "},
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "validation_failed"


# --- GET /chat/conversations ------------------------------------------------------------


async def test_i_s2_an_11_listing_is_tenant_scoped_and_ordered_by_last_message(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    base = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    oldest = await _conversation(db_session, alice)
    newest = await _conversation(db_session, alice)
    middle = await _conversation(db_session, alice)
    deleted = await _conversation(
        db_session, alice, deleted_at=base + timedelta(hours=1), title="borrada"
    )
    foreign = await _conversation(db_session, bob)
    await _message(db_session, alice, oldest, content="uno", created_at=base)
    await _message(
        db_session, alice, middle, content="dos", created_at=base + timedelta(minutes=10)
    )
    await _message(
        db_session, alice, newest, content="tres", created_at=base + timedelta(minutes=20)
    )
    await _message(db_session, bob, foreign, content="ajena", created_at=base)
    await db_session.flush()

    response = await client.get("/chat/conversations", cookies=alice.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [uuid.UUID(item["id"]) for item in items] == [
        newest.id,
        middle.id,
        oldest.id,
    ]
    assert [item["last_message_at"] is not None for item in items] == [True, True, True]
    assert deleted.id not in {uuid.UUID(item["id"]) for item in items}
    assert foreign.id not in {uuid.UUID(item["id"]) for item in items}

    foreign_response = await client.get("/chat/conversations", cookies=bob.cookies)
    assert [uuid.UUID(item["id"]) for item in foreign_response.json()["items"]] == [foreign.id]


async def test_i_s2_an_11_listing_is_empty_without_conversations(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    response = await client.get("/chat/conversations", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_i_s2_an_11_listing_is_keyset_paginated(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    base = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    conversations = [await _conversation(db_session, actor) for _ in range(4)]
    for index, conversation in enumerate(conversations):
        await _message(
            db_session,
            actor,
            conversation,
            content=f"mensaje {index}",
            created_at=base + timedelta(minutes=index),
        )
    await db_session.flush()

    first = await client.get(
        "/chat/conversations", params={"limit": 2}, cookies=actor.cookies
    )
    assert first.status_code == 200
    first_body = first.json()
    assert [uuid.UUID(item["id"]) for item in first_body["items"]] == [
        conversations[3].id,
        conversations[2].id,
    ]
    assert first_body["next_cursor"]

    second = await client.get(
        "/chat/conversations",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
        cookies=actor.cookies,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [uuid.UUID(item["id"]) for item in second_body["items"]] == [
        conversations[1].id,
        conversations[0].id,
    ]
    assert second_body["next_cursor"] is None

    collected = [
        uuid.UUID(item["id"]) for item in first_body["items"] + second_body["items"]
    ]
    assert sorted(collected) == sorted(conversation.id for conversation in conversations)

    for limit in (0, 101):
        invalid = await client.get(
            "/chat/conversations", params={"limit": limit}, cookies=actor.cookies
        )
        assert invalid.status_code == 422
    malformed = await client.get(
        "/chat/conversations", params={"cursor": "***"}, cookies=actor.cookies
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "validation_failed"


# --- GET /chat/conversations/{id}/messages ----------------------------------------------


async def test_i_s2_an_11_history_is_paginated_oldest_first_and_tenant_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    conversation = await _conversation(db_session, alice)
    base = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    messages = [
        await _message(
            db_session,
            alice,
            conversation,
            role="user" if index % 2 == 0 else "assistant",
            content=f"mensaje {index}",
            created_at=base + timedelta(minutes=index),
            client_message_id=f"client-{index}" if index % 2 else None,
        )
        for index in range(1, 6)
    ]

    collected: list[uuid.UUID] = []
    pages_seen: list[list[uuid.UUID]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.get(
            f"/chat/conversations/{conversation.id}/messages",
            params=params,
            cookies=alice.cookies,
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        page_ids = [uuid.UUID(item["id"]) for item in body["items"]]
        pages_seen.append(page_ids)
        collected.extend(page_ids)
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages_seen == [
        [messages[3].id, messages[4].id],
        [messages[1].id, messages[2].id],
        [messages[0].id],
    ]
    # A client prepends older pages; the union covers every message exactly once.
    assert sorted(collected) == sorted(message.id for message in messages)

    # The cursor from another conversation is not reusable.
    other = await _conversation(db_session, alice)
    await _message(db_session, alice, other, content="otra", created_at=base)
    other_page = await client.get(
        f"/chat/conversations/{other.id}/messages", params={"limit": 1}, cookies=alice.cookies
    )
    foreign_cursor = other_page.json()["next_cursor"]
    reused = await client.get(
        f"/chat/conversations/{conversation.id}/messages",
        params={"cursor": foreign_cursor},
        cookies=alice.cookies,
    )
    assert reused.status_code == 422
    assert reused.json()["error"]["code"] == "validation_failed"

    # Malformed cursor and out-of-range limits are typed 422s.
    malformed = await client.get(
        f"/chat/conversations/{conversation.id}/messages",
        params={"cursor": "***"},
        cookies=alice.cookies,
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["details"]["fields"]["cursor"]
    for limit in (0, 101):
        invalid_limit = await client.get(
            f"/chat/conversations/{conversation.id}/messages",
            params={"limit": limit},
            cookies=alice.cookies,
        )
        assert invalid_limit.status_code == 422

    # A foreign conversation is a 404, never a leak of its content.
    foreign = await client.get(
        f"/chat/conversations/{conversation.id}/messages", cookies=bob.cookies
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"

    no_session = await client.get(f"/chat/conversations/{conversation.id}/messages")
    assert no_session.status_code == 401


async def test_i_s2_an_11_history_orders_a_same_timestamp_turn_user_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    conversation = await _conversation(db_session, actor)
    base = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    # The assistant UUID sorts before the question, so only the role tie-break can keep
    # the pair in question-then-answer order. Both rows share one transaction timestamp,
    # as legacy L2.5 turns did before `prepare` wrote an explicit microsecond offset.
    question_id = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
    answer_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    await _message(
        db_session,
        actor,
        conversation,
        role="user",
        content="pregunta",
        created_at=base,
        message_id=question_id,
    )
    await _message(
        db_session,
        actor,
        conversation,
        role="assistant",
        content="respuesta",
        created_at=base,
        message_id=answer_id,
    )

    response = await client.get(
        f"/chat/conversations/{conversation.id}/messages", cookies=actor.cookies
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["role"], uuid.UUID(item["id"])) for item in items] == [
        ("user", question_id),
        ("assistant", answer_id),
    ]

    # Keyset pagination visits the same total order: each row appears exactly once even
    # when the page boundary falls between the question and its answer.
    pages: list[list[uuid.UUID]] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        page = await client.get(
            f"/chat/conversations/{conversation.id}/messages",
            params=params,
            cookies=actor.cookies,
        )
        assert page.status_code == 200
        body = page.json()
        pages.append([uuid.UUID(item["id"]) for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == [[answer_id], [question_id]]
    assert sorted(page[0] for page in pages) == sorted([question_id, answer_id])


async def test_i_s2_an_11_history_keeps_the_partial_cancelled_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    conversation = await _conversation(db_session, actor)
    conversation_id = conversation.id
    await _message(db_session, actor, conversation, content="pregunta")
    assistant = await _message(
        db_session,
        actor,
        conversation,
        role="assistant",
        content="parcial [S1]",
        status="streaming",
    )
    finished = await _message(
        db_session, actor, conversation, role="assistant", content="lista", status="completed"
    )
    assistant_id = assistant.id
    finished_id = finished.id

    response = await client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant_id}/cancel",
        cookies=actor.cookies,
        headers=actor.headers,
    )

    assert response.status_code == 202
    assert response.json() == {"message_id": str(assistant_id), "status": "cancelled"}
    history = await client.get(
        f"/chat/conversations/{conversation_id}/messages", cookies=actor.cookies
    )
    assistant_row = next(
        item for item in history.json()["items"] if uuid.UUID(item["id"]) == assistant_id
    )
    assert assistant_row["status"] == "cancelled"
    assert assistant_row["content"] == "parcial [S1]"

    # Cancelling the same message again is idempotent; a finished message conflicts.
    again = await client.post(
        f"/chat/conversations/{conversation_id}/messages/{assistant_id}/cancel",
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert again.status_code == 202
    conflict = await client.post(
        f"/chat/conversations/{conversation_id}/messages/{finished_id}/cancel",
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "invalid_transition"


async def test_i_s2_an_11_cancel_is_scoped_to_the_tenant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    conversation = await _conversation(db_session, alice)
    assistant = await _message(
        db_session, alice, conversation, role="assistant", status="streaming"
    )

    response = await client.post(
        f"/chat/conversations/{conversation.id}/messages/{assistant.id}/cancel",
        cookies=bob.cookies,
        headers=bob.headers,
    )

    assert response.status_code == 404
    await db_session.refresh(assistant)
    assert assistant.status == "streaming"


# --- DELETE /chat/conversations/{id} ----------------------------------------------------


async def test_i_s2_an_11_delete_tombstones_and_purges_messages_and_sources(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    alice, bob = await actors.make_actor(db_session), await actors.make_actor(db_session)
    conversation = await _conversation(db_session, alice, title="Contenido privado")
    conversation_id = conversation.id
    user_message = await _message(db_session, alice, conversation, content="pregunta")
    assistant = await _message(
        db_session, alice, conversation, role="assistant", content="respuesta"
    )
    for message in (user_message, assistant):
        db_session.add(MessageSource(user_id=alice.user_id, message_id=message.id, kind="general"))
    await db_session.flush()

    # Another tenant cannot delete (or infer) the conversation.
    foreign = await client.delete(
        f"/chat/conversations/{conversation_id}",
        cookies=bob.cookies,
        headers=bob.headers,
    )
    assert foreign.status_code == 404
    assert (
        await db_session.scalar(
            select(func.count()).where(Message.conversation_id == conversation_id)
        )
        == 2
    )

    response = await client.delete(
        f"/chat/conversations/{conversation_id}",
        cookies=alice.cookies,
        headers=alice.headers,
    )

    assert response.status_code == 204
    assert response.content == b""
    db_session.expire_all()
    row = await db_session.get(Conversation, conversation_id)
    assert row is not None
    assert row.deleted_at is not None
    assert row.title is None
    assert (
        await db_session.scalar(
            select(func.count()).where(Message.conversation_id == conversation_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MessageSource)
            .where(MessageSource.user_id == alice.user_id)
        )
        == 0
    )

    history = await client.get(
        f"/chat/conversations/{conversation_id}/messages", cookies=alice.cookies
    )
    assert history.status_code == 404
    listing = await client.get("/chat/conversations", cookies=alice.cookies)
    assert listing.json() == {"items": [], "next_cursor": None}
    second = await client.delete(
        f"/chat/conversations/{conversation_id}",
        cookies=alice.cookies,
        headers=alice.headers,
    )
    assert second.status_code == 404
