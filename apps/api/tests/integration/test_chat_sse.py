"""I-S2-JL-07/08: chat SSE pipeline against real PostgreSQL+pgvector (L2.5).

Embeddings and the LLM are deterministic fakes; the corpus is synthetic and contains no real
class content. `ASGITransport` buffers open streams, so mid-stream cancellation/disconnect tests
iterate `RAGService.stream_events` directly, as the progress SSE suite does.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import chat_session_factory
from app.core.providers import get_embedding_provider, get_llm_provider
from app.main import app
from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    IndexGeneration,
    Message,
    MessageSource,
    Segment,
    Subject,
    Transcript,
)
from app.routers import chat as chat_router
from app.services.indexing import chunk_tsvector
from app.services.llm import ChatMessage, LLMProviderError
from app.services.rag import RAGService, cancel_message
from tests.integration import actors

pytestmark = pytest.mark.integration

DIMENSION = 1536
QUERY_VECTOR = [1.0] + [0.0] * (DIMENSION - 1)
ABSTENTION = (
    "No encuentro evidencia suficiente en tus clases para responder a eso. "
    "¿Puedes darme más contexto o reformular la pregunta?"
)


class FakeEmbeddingProvider:
    model = "test-model"
    dimension = DIMENSION
    batch_size = 100

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [list(QUERY_VECTOR) for _ in texts]


class FakeLLM:
    model = "test-model"

    def __init__(self) -> None:
        self.context_window = 8192
        self.chunks: list[str] = ["Según tu clase, ", "[S1]", " la integral acumula área."]
        self.calls = 0
        self.last_messages: list[ChatMessage] = []
        self.error_after: int | None = None

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        self.last_messages = list(messages)
        assert max_tokens > 0
        for index, chunk in enumerate(self.chunks):
            if self.error_after is not None and index >= self.error_after:
                raise LLMProviderError("fake provider failure")
            yield chunk
        if self.error_after is not None:
            raise LLMProviderError("fake provider failure")


class BlockingLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        self.last_messages = list(messages)
        yield "parcial"
        self.started.set()
        await self.release.wait()


@dataclass
class Harness:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    embeddings: FakeEmbeddingProvider
    llm: FakeLLM | None


@pytest.fixture
async def chat_sse(migrated_database_url: str) -> AsyncIterator[Harness]:
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    embeddings = FakeEmbeddingProvider()
    llm = FakeLLM()
    harness = Harness(client=None, factory=factory, embeddings=embeddings, llm=llm)  # type: ignore[arg-type]

    async def test_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_get_session
    app.dependency_overrides[chat_session_factory] = lambda: factory
    app.dependency_overrides[get_embedding_provider] = lambda: embeddings
    app.dependency_overrides[get_llm_provider] = lambda: harness.llm
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            harness.client = client
            yield harness
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _frames(body: str) -> list[str]:
    return [frame for frame in body.split("\n\n") if frame and not frame.startswith(":")]


def _event_name(frame: str) -> str:
    return frame.split("\n", 1)[0].removeprefix("event: ")


def _event_id(frame: str) -> int:
    for line in frame.splitlines():
        if line.startswith("id: "):
            return int(line[len("id: ") :])
    raise AssertionError(f"frame without id: {frame!r}")


def _event_data(frame: str) -> dict[str, Any]:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return cast(dict[str, Any], json.loads(line[len("data: ") :]))
    raise AssertionError(f"frame without data: {frame!r}")


def _events(response_text: str) -> list[tuple[str, int, dict[str, Any]]]:
    return [
        (_event_name(frame), _event_id(frame), _event_data(frame))
        for frame in _frames(response_text)
    ]


async def _make_generation(db: AsyncSession, actor: actors.Actor) -> IndexGeneration:
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
        version=f"chat-{uuid.uuid4().hex}",
        model="test-model",
        dimension=DIMENSION,
        status="active",
        chunk_count=0,
    )
    db.add(generation)
    await db.flush()
    return generation


async def _isolated_generation(
    db: AsyncSession, actor: actors.Actor, *, status: str = "building"
) -> IndexGeneration:
    generation = IndexGeneration(
        user_id=actor.user_id,
        version=f"old-{uuid.uuid4().hex}",
        model="test-model",
        dimension=DIMENSION,
        status=status,
        chunk_count=0,
    )
    db.add(generation)
    await db.flush()
    return generation


async def _indexed_class(
    db: AsyncSession,
    actor: actors.Actor,
    *,
    generation: IndexGeneration,
    content: str,
    subject: Subject | None = None,
    audio: Audio | None = None,
    ordinal: int = 1,
    t_start: float | None = 65.0,
    t_end: float | None = 70.0,
    precision: str = "segment",
    duration_seconds: float | None = 100.0,
) -> tuple[Audio, Transcript, Chunk, Segment]:
    audio = audio or await actors.make_audio(db, actor, subject=subject)
    audio.duration_seconds = duration_seconds
    transcript = Transcript(
        user_id=actor.user_id,
        audio_id=audio.id,
        version=1,
        language_requested="es",
        model="test-model",
        model_config={},
        timestamp_precision=precision,
        text=content,
        char_count=len(content),
        fts_config="spanish",
    )
    db.add(transcript)
    await db.flush()
    audio.active_transcript_version = 1
    audio.active_index_version = generation.version
    chunk = Chunk(
        user_id=actor.user_id,
        audio_id=audio.id,
        transcript_id=transcript.id,
        transcript_version=1,
        index_version=generation.version,
        ordinal=ordinal,
        content=content,
        char_start=0,
        char_end=len(content),
        token_count=len(content.split()),
        tsv=chunk_tsvector("spanish", content),
    )
    db.add(chunk)
    await db.flush()
    segment = Segment(
        user_id=actor.user_id,
        transcript_id=transcript.id,
        ordinal=ordinal,
        text=content,
        char_start=0,
        char_end=len(content),
        t_start=t_start,
        t_end=t_end,
    )
    db.add(segment)
    await db.flush()
    db.add(
        ChunkSegment(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            segment_id=segment.id,
            overlap_span={"char_start": 0, "char_end": len(content)},
        )
    )
    db.add(
        Embedding(
            user_id=actor.user_id,
            chunk_id=chunk.id,
            index_version=generation.version,
            model=generation.model,
            dimension=DIMENSION,
            vector=list(QUERY_VECTOR),
        )
    )
    await db.flush()
    return audio, transcript, chunk, segment


async def _conversation(db: AsyncSession, actor: actors.Actor, **kwargs: Any) -> Conversation:
    conversation = Conversation(user_id=actor.user_id, mode=kwargs.pop("mode", "all"), **kwargs)
    db.add(conversation)
    await db.flush()
    return conversation


async def _setup_corpus(
    factory: async_sessionmaker[AsyncSession],
    *,
    count: int = 1,
    conversation_mode: str = "all",
) -> tuple[actors.Actor, Conversation, list[Audio], IndexGeneration]:
    async with factory() as db:
        actor = await actors.make_actor(db)
        generation = await _make_generation(db, actor)
        audios: list[Audio] = []
        for index in range(count):
            audio, _, _, _ = await _indexed_class(
                db,
                actor,
                generation=generation,
                content=f"tema {index} integral definida",
                ordinal=index + 1,
            )
            audios.append(audio)
        if conversation_mode == "class":
            conversation = await _conversation(db, actor, mode="class", audio_id=audios[0].id)
        else:
            conversation = await _conversation(db, actor)
        await db.commit()
        return actor, conversation, audios, generation


async def _post(
    harness: Harness,
    actor: actors.Actor,
    conversation_id: uuid.UUID,
    *,
    content: str = "¿Qué hace la integral?",
    client_message_id: str | None = "retry-1",
    headers: dict[str, str] | None = None,
) -> Any:
    return await harness.client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"content": content, "client_message_id": client_message_id},
        cookies=actor.cookies,
        headers=headers or actor.headers,
    )


async def test_chat_stream_emits_ordered_deltas_citations_and_one_done(
    chat_sse: Harness,
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    response = await _post(chat_sse, actor, conversation.id)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = _events(response.text)
    names = [name for name, _, _ in events]
    assert names[0] == "snapshot"
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert names.count("error") == 0
    assert "delta" in names and "citation" in names
    ids = [event_id for _, event_id, _ in events]
    assert ids == sorted(ids)

    deltas = [data for name, _, data in events if name == "delta"]
    assert all("[S1]" not in data["text"] for data in deltas)
    citation = next(data for name, _, data in events if name == "citation")
    assert citation["index"] == 1
    assert citation["transcript_version"] == 1
    assert citation["t_start"] == 65.0
    assert citation["label"].endswith("01:05]")
    assert citation["url"].startswith("/biblioteca/")
    done = next(data for name, _, data in events if name == "done")
    assert done["status"] == "completed"
    assert done["conversation_id"] == str(conversation.id)
    assert done["sources"] == [{"kind": "transcript", "audio_id": str(citation["audio_id"])}]


async def test_chat_rejects_a_third_stream_before_any_work(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    async with chat_sse.factory() as db:
        before = await db.scalar(
            select(func.count()).select_from(Message).where(Message.user_id == actor.user_id)
        )

    chat_router._active[actor.user_id] = chat_router.MAX_STREAMS_PER_USER
    try:
        response = await _post(chat_sse, actor, conversation.id)
    finally:
        chat_router._active.pop(actor.user_id, None)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.json()["error"]["message"] == "Demasiadas conexiones de chat simultáneas."
    async with chat_sse.factory() as db:
        after = await db.scalar(
            select(func.count()).select_from(Message).where(Message.user_id == actor.user_id)
        )
    assert after == before
    assert chat_sse.llm is not None and chat_sse.llm.calls == 0
    assert chat_sse.embeddings.calls == 0


async def test_chat_ignores_last_event_id_for_a_new_turn(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    response = await _post(
        chat_sse,
        actor,
        conversation.id,
        headers={**actor.headers, "Last-Event-ID": "999"},
    )
    assert response.status_code == 200
    events = _events(response.text)
    assert events[0][0] == "snapshot"
    assert events[0][1] == 0
    ids = [event_id for _, event_id, _ in events]
    assert ids == sorted(ids)
    assert events[-1][0] == "done"


async def test_chat_replay_interleaves_citations_and_keeps_monotonic_event_ids(
    chat_sse: Harness,
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    first = await _post(chat_sse, actor, conversation.id)
    assert first.status_code == 200
    first_content = "".join(
        data["text"] for name, _, data in _events(first.text) if name == "delta"
    )
    assert chat_sse.llm is not None and chat_sse.llm.calls == 1

    replay = await _post(chat_sse, actor, conversation.id)
    assert replay.status_code == 200
    events = _events(replay.text)
    ids = [event_id for _, event_id, _ in events]
    assert ids == sorted(ids)
    visible = ""
    citations = 0
    for name, event_id, data in events:
        if name == "delta":
            visible += data["text"]
        elif name == "citation":
            assert len(visible) == event_id
            citations += 1
    assert citations >= 1
    assert visible == first_content
    assert chat_sse.llm.calls == 1


async def test_chat_persists_assistant_message_sources_and_validated_citations(
    chat_sse: Harness,
) -> None:
    actor, conversation, audios, _ = await _setup_corpus(chat_sse.factory)
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200

    async with chat_sse.factory() as db:
        rows = (
            (await db.execute(select(Message).where(Message.conversation_id == conversation.id)))
            .scalars()
            .all()
        )
        by_role = {row.role: row for row in rows}
        assert set(by_role) == {"user", "assistant"}
        assistant = by_role["assistant"]
        assert assistant.status == "completed"
        assert assistant.model == "test-model"
        assert assistant.prompt_version == "v1"
        assert assistant.input_tokens is not None and assistant.input_tokens > 0
        assert assistant.output_tokens is not None and assistant.output_tokens > 0
        assert assistant.content == "Según tu clase,  la integral acumula área."
        assert assistant.citas[0]["index"] == 1
        assert assistant.citas[0]["audio_id"] == str(audios[0].id)
        assert "quote" in assistant.citas[0]

        sources = (
            (
                await db.execute(
                    select(MessageSource).where(MessageSource.message_id == assistant.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(sources) == 1
        assert sources[0].kind == "transcript"
        assert sources[0].audio_id == audios[0].id
        assert sources[0].chunk_id is not None
        assert sources[0].transcript_id is not None
        assert sources[0].segment_id is not None


async def test_chat_selects_at_most_eight_chunks_under_budget(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory, count=10)
    assert chat_sse.llm is not None
    chat_sse.llm.chunks = ["respuesta [S1] [S3]"]
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200

    assert chat_sse.llm.calls == 1
    final_prompt = chat_sse.llm.last_messages[-1].content
    assert '<source id="S1"' in final_prompt
    assert '<source id="S8"' in final_prompt
    assert '<source id="S9"' not in final_prompt
    assert '<source id="S10"' not in final_prompt
    async with chat_sse.factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(MessageSource)
            .join(Message, Message.id == MessageSource.message_id)
            .where(Message.conversation_id == conversation.id)
        )
        assert count == 8


async def test_chat_history_is_trimmed_by_complete_turns(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    async with chat_sse.factory() as db:
        old_generation = await _isolated_generation(db, actor)
        old_audio, _, old_chunk, old_segment = await _indexed_class(
            db,
            actor,
            generation=old_generation,
            content="respuesta vieja " * 1000,
            ordinal=20,
        )
        user = Message(
            user_id=actor.user_id,
            conversation_id=conversation.id,
            role="user",
            content="pregunta vieja",
            status="completed",
            created_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        assistant = Message(
            user_id=actor.user_id,
            conversation_id=conversation.id,
            role="assistant",
            content="respuesta vieja " * 1000,
            status="completed",
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add_all([user, assistant])
        await db.flush()
        db.add(
            MessageSource(
                user_id=actor.user_id,
                message_id=assistant.id,
                kind="transcript",
                audio_id=old_audio.id,
                transcript_id=old_chunk.transcript_id,
                transcript_version=old_chunk.transcript_version,
                chunk_id=old_chunk.id,
                segment_id=old_segment.id,
            )
        )
        await db.commit()

    service = RAGService(
        session_factory=chat_sse.factory,
        embedding_provider=chat_sse.embeddings,
        llm_provider=chat_sse.llm,
        context_margin_tokens=64,
        output_reserve_tokens=128,
    )
    assert chat_sse.llm is not None
    chat_sse.llm.context_window = 800
    async with chat_sse.factory() as db:
        prepared = await service.prepare(
            db,
            user_id=actor.user_id,
            conversation_id=conversation.id,
            content="¿Qué es la integral?",
            client_message_id="history-trim",
        )
    assert prepared.error is None
    assert all(message.content != "pregunta vieja" for message in prepared.messages)
    async with chat_sse.factory() as db:
        assistant_id = prepared.assistant_message_id
        source_count = await db.scalar(
            select(func.count())
            .select_from(MessageSource)
            .where(MessageSource.message_id == assistant_id)
        )
        assert source_count == 1  # only the current evidence, not the inherited old turn


async def test_chat_context_exceeded_is_an_sse_error_without_calling_llm(
    chat_sse: Harness,
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    assert chat_sse.llm is not None
    chat_sse.llm.context_window = 1
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _, _ in events] == ["snapshot", "error"]
    assert events[1][2]["code"] == "context_exceeded"
    assert chat_sse.llm.calls == 0
    async with chat_sse.factory() as db:
        status = await db.scalar(
            select(Message.status).where(
                Message.conversation_id == conversation.id, Message.role == "assistant"
            )
        )
        assert status == "failed"


async def test_chat_abstains_without_evidence_and_never_calls_llm(
    chat_sse: Harness,
) -> None:
    async with chat_sse.factory() as db:
        actor = await actors.make_actor(db)
        conversation = await _conversation(db, actor)
        await db.commit()
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _, _ in events] == ["snapshot", "delta", "done"]
    assert events[1][2]["text"] == ABSTENTION
    assert events[2][2]["status"] == "completed"
    assert events[2][2]["sources"] == []
    assert chat_sse.llm is not None and chat_sse.llm.calls == 0


async def test_chat_is_idempotent_by_client_message_id(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    first = await _post(chat_sse, actor, conversation.id)
    assert first.status_code == 200
    assert chat_sse.llm is not None and chat_sse.llm.calls == 1

    second = await _post(chat_sse, actor, conversation.id)
    assert second.status_code == 200
    assert chat_sse.llm.calls == 1
    events = _events(second.text)
    assert events[0][0] == "snapshot"
    assert events[0][2]["status"] == "completed"
    assert any(name == "delta" for name, _, _ in events)
    assert events[-1][0] == "done"

    async with chat_sse.factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation.id)
        )
        assert count == 2


async def test_chat_reconnect_with_last_event_id_omits_sent_text(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    first = await _post(chat_sse, actor, conversation.id)
    assert first.status_code == 200
    content = "".join(data["text"] for name, _, data in _events(first.text) if name == "delta")
    assert len(content) > 5

    response = await chat_sse.client.post(
        f"/chat/conversations/{conversation.id}/messages",
        json={"content": "¿Qué hace la integral?", "client_message_id": "retry-1"},
        cookies=actor.cookies,
        headers={**actor.headers, "Last-Event-ID": "5"},
    )
    assert response.status_code == 200
    replay_events = _events(response.text)
    ids = [event_id for _, event_id, _ in replay_events]
    assert ids == sorted(ids)
    deltas = [data["text"] for name, _, data in replay_events if name == "delta"]
    assert "".join(deltas) == content[5:]
    assert chat_sse.llm is not None and chat_sse.llm.calls == 1


async def test_chat_requires_auth_csrf_and_ownership(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    async with chat_sse.factory() as db:
        stranger = await actors.make_actor(db)
        await db.commit()

    assert (
        await chat_sse.client.post(
            f"/chat/conversations/{conversation.id}/messages",
            json={"content": "hola"},
        )
    ).status_code == 401
    assert (
        await chat_sse.client.post(
            f"/chat/conversations/{conversation.id}/messages",
            json={"content": "hola"},
            cookies=actor.cookies,
        )
    ).status_code == 403
    assert (await _post(chat_sse, stranger, conversation.id)).status_code == 404
    assert (await _post(chat_sse, actor, uuid.uuid4())).status_code == 404


async def test_chat_maps_llm_failure_to_error_and_failed_status(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    assert chat_sse.llm is not None
    chat_sse.llm.chunks = ["parcial"]
    chat_sse.llm.error_after = 1
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _, _ in events] == ["snapshot", "delta", "error"]
    assert events[1][2]["text"] == "parcial"
    assert events[2][2]["code"] == "provider_unavailable"
    async with chat_sse.factory() as db:
        row = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
            )
        )
        assert row is not None
        assert row.status == "failed"
        assert row.content == "parcial"


async def test_chat_class_mode_filters_by_audio_before_top_k(chat_sse: Harness) -> None:
    actor, conversation, audios, _ = await _setup_corpus(
        chat_sse.factory, count=2, conversation_mode="class"
    )
    assert chat_sse.llm is not None
    chat_sse.llm.chunks = ["respuesta [S1]"]
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    assert chat_sse.llm.calls == 1
    prompt = chat_sse.llm.last_messages[-1].content
    assert "tema 0 integral" in prompt
    assert "tema 1 integral" not in prompt
    async with chat_sse.factory() as db:
        source_audios = (
            (
                await db.execute(
                    select(MessageSource.audio_id)
                    .join(Message, Message.id == MessageSource.message_id)
                    .where(Message.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
        assert set(source_audios) == {audios[0].id}


async def test_chat_sources_contain_all_delivered_and_reject_invented_indices(
    chat_sse: Harness,
) -> None:
    actor, conversation, audios, _ = await _setup_corpus(chat_sse.factory, count=3)
    assert chat_sse.llm is not None
    chat_sse.llm.chunks = ["[S99] [S1] respuesta"]
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    events = _events(response.text)
    citation_data = [data for name, _, data in events if name == "citation"]
    assert [data["index"] for data in citation_data] == [1]
    deltas = "".join(data["text"] for name, _, data in events if name == "delta")
    assert "[S99]" in deltas
    async with chat_sse.factory() as db:
        rows = (
            (
                await db.execute(
                    select(MessageSource)
                    .join(Message, Message.id == MessageSource.message_id)
                    .where(Message.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert {row.audio_id for row in rows} == {audio.id for audio in audios}
        assistant = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
            )
        )
        assert assistant is not None
        assert [citation["index"] for citation in assistant.citas] == [1]


async def test_chat_message_sources_include_inherited_history_sources(
    chat_sse: Harness,
) -> None:
    actor, conversation, current_audios, _ = await _setup_corpus(chat_sse.factory)
    async with chat_sse.factory() as db:
        old_generation = await _isolated_generation(db, actor)
        old_audio, old_transcript, old_chunk, old_segment = await _indexed_class(
            db,
            actor,
            generation=old_generation,
            content="fuente heredada",
            ordinal=20,
        )
        user = Message(
            user_id=actor.user_id,
            conversation_id=conversation.id,
            role="user",
            content="pregunta anterior",
            status="completed",
            created_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        assistant = Message(
            user_id=actor.user_id,
            conversation_id=conversation.id,
            role="assistant",
            content="respuesta anterior",
            status="completed",
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add_all([user, assistant])
        await db.flush()
        db.add(
            MessageSource(
                user_id=actor.user_id,
                message_id=assistant.id,
                kind="transcript",
                audio_id=old_audio.id,
                transcript_id=old_transcript.id,
                transcript_version=old_transcript.version,
                chunk_id=old_chunk.id,
                segment_id=old_segment.id,
            )
        )
        await db.commit()

    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    assert chat_sse.llm is not None
    assert any("pregunta anterior" in message.content for message in chat_sse.llm.last_messages)
    async with chat_sse.factory() as db:
        stored_assistant = await db.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
                Message.content != "respuesta anterior",
            )
        )
        assert stored_assistant is not None
        rows = (
            (
                await db.execute(
                    select(MessageSource).where(MessageSource.message_id == stored_assistant.id)
                )
            )
            .scalars()
            .all()
        )
        assert {row.audio_id for row in rows} == {old_audio.id, current_audios[0].id}


async def test_chat_cancellation_mid_stream_leaves_partial_cancelled(
    chat_sse: Harness,
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    blocking = BlockingLLM()
    service = RAGService(
        session_factory=chat_sse.factory,
        embedding_provider=chat_sse.embeddings,
        llm_provider=blocking,
        cancel_poll_seconds=0.01,
        heartbeat_seconds=10.0,
        checkpoint_seconds=10.0,
    )
    async with chat_sse.factory() as db:
        prepared = await service.prepare(
            db,
            user_id=actor.user_id,
            conversation_id=conversation.id,
            content="¿Qué hace la integral?",
            client_message_id="cancel-1",
        )
    stream = service.stream_events(prepared)
    assert _event_name(await anext(stream)) == "snapshot"
    delta = await anext(stream)
    assert _event_name(delta) == "delta"
    assert _event_data(delta)["text"] == "parcial"
    await blocking.started.wait()
    async with chat_sse.factory() as db:
        assert (
            await cancel_message(
                db,
                user_id=actor.user_id,
                conversation_id=conversation.id,
                message_id=prepared.assistant_message_id,
            )
            == "cancelled"
        )
    done = await anext(stream)
    assert _event_name(done) == "done"
    assert _event_data(done)["status"] == "cancelled"
    await stream.aclose()

    async with chat_sse.factory() as db:
        row = await db.get(Message, prepared.assistant_message_id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.content == "parcial"


async def test_chat_client_disconnect_leaves_partial_cancelled(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    blocking = BlockingLLM()
    service = RAGService(
        session_factory=chat_sse.factory,
        embedding_provider=chat_sse.embeddings,
        llm_provider=blocking,
        cancel_poll_seconds=30.0,
        heartbeat_seconds=30.0,
        checkpoint_seconds=30.0,
    )
    async with chat_sse.factory() as db:
        prepared = await service.prepare(
            db,
            user_id=actor.user_id,
            conversation_id=conversation.id,
            content="¿Qué hace la integral?",
            client_message_id="disconnect-1",
        )
    stream = service.stream_events(prepared)
    assert _event_name(await anext(stream)) == "snapshot"
    assert _event_data(await anext(stream))["text"] == "parcial"
    await blocking.started.wait()
    await stream.aclose()

    async with chat_sse.factory() as db:
        row = await db.get(Message, prepared.assistant_message_id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.content == "parcial"


async def test_chat_does_not_log_question_content(
    chat_sse: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    caplog.set_level("DEBUG")
    response = await _post(
        chat_sse,
        actor,
        conversation.id,
        content="pregunta-secreta-sintetica",
    )
    assert response.status_code == 200
    assert "pregunta-secreta-sintetica" not in caplog.text
    assert "integral acumula" not in caplog.text


async def test_cancel_terminal_message_is_a_conflict(chat_sse: Harness) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    response = await _post(chat_sse, actor, conversation.id)
    assert response.status_code == 200
    async with chat_sse.factory() as db:
        assistant_id = await db.scalar(
            select(Message.id).where(
                Message.conversation_id == conversation.id,
                Message.role == "assistant",
            )
        )
    assert assistant_id is not None
    cancel = await chat_sse.client.post(
        f"/chat/conversations/{conversation.id}/messages/{assistant_id}/cancel",
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert cancel.status_code == 409
    assert cancel.json()["error"]["code"] == "invalid_transition"


async def test_chat_cancel_requires_csrf_and_returns_not_found_for_foreign(
    chat_sse: Harness,
) -> None:
    actor, conversation, _, _ = await _setup_corpus(chat_sse.factory)
    async with chat_sse.factory() as db:
        stranger = await actors.make_actor(db)
        await db.commit()
    assert (
        await chat_sse.client.post(
            f"/chat/conversations/{conversation.id}/messages/{uuid.uuid4()}/cancel",
            cookies=actor.cookies,
        )
    ).status_code == 403
    assert (
        await chat_sse.client.post(
            f"/chat/conversations/{conversation.id}/messages/{uuid.uuid4()}/cancel",
            cookies=stranger.cookies,
            headers=stranger.headers,
        )
    ).status_code == 404
