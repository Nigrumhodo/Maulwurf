"""Retrieval-augmented chat pipeline and SSE stream (L2.5, M7/DISENO §3.10).

The request path is split in two phases so the database request session never stays open
while the SSE response streams:

1. `RAGService.prepare` authenticates ownership (the router does it on the same short
   session), persists the user message and the assistant placeholder, retrieves evidence
   through `SearchService`, applies the token budget and persists the validated sources.
2. `RAGService.stream_events` reads only short-lived sessions from `session_factory`; it emits
   `snapshot`, `delta`, `citation`, `ping` and one terminal `done`/`error`.

Cancellation is cooperative and durable: `POST .../cancel` flips the message to `cancelled`;
the stream polls that state, persists its latest partial text and closes with
`done: cancelled`. A client disconnect has the same effect through the generator's `finally`.
"""

import logging
import uuid
from collections.abc import AsyncGenerator, Callable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import bind_user
from app.models.chat import Conversation, Message, MessageSource
from app.schemas.citations import (
    parse_citations,
)
from app.schemas.tools import parse_tool_calls
from app.services.chat_order import ASSISTANT_CREATED_AT_OFFSET, message_role_rank
from app.services.chat_prompt import (
    ABSTENTION_TEXT,
    CANCEL_POLL_SECONDS,
    CHECKPOINT_SECONDS,
    CONTEXT_MARGIN_TOKENS,
    HEARTBEAT_SECONDS,
    HISTORY_MAX_MESSAGES,
    MESSAGE_MAX_CHARS,
    OUTPUT_RESERVE_TOKENS,
    PROMPT_VERSION,
    REPLAY_POLL_SECONDS,
    STREAM_MAX_SECONDS,
    ChatContextExceeded,
    ChatError,
    ChatStreamError,
    HistoryMessage,
    HistoryTurn,
    PreparedStream,
    ReplayState,
    SourceItem,
    build_chat_prompt,
    group_history,
    load_system_prompt,
)
from app.services.chat_stream import _CANCEL_EVENTS, ChatTurnStream
from app.services.chunking import count_tokens_default
from app.services.citations import (
    CitationValidator,
)
from app.services.embeddings import (
    EmbeddingDimensionMismatch,
    EmbeddingError,
    EmbeddingInputTooLong,
    EmbeddingNotConfigured,
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingRateLimited,
    embed_query,
)
from app.services.llm import (
    LLMProvider,
)
from app.services.retrieval import (
    SearchHnswUnsupported,
    SearchInvalidQuery,
    SearchNotConfigured,
    SearchProviderMismatch,
    SearchVectorRequired,
)
from app.services.search import SearchService

logger = logging.getLogger(__name__)




class ChatConversationNotFound(ChatError):
    code = "not_found"


class ChatMessageNotFound(ChatError):
    code = "not_found"


class ChatInvalidTransition(ChatError):
    code = "invalid_transition"




def signal_cancel(message_id: uuid.UUID) -> None:
    """Wake a live stream immediately; the DB status remains authoritative."""
    event = _CANCEL_EVENTS.get(message_id)
    if event is not None:
        event.set()


class RAGService(ChatTurnStream):
    """Prepare and stream one chat answer; providers are injected for deterministic tests."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_provider: EmbeddingProvider | None,
        llm_provider: LLMProvider | None,
        history_max_messages: int = HISTORY_MAX_MESSAGES,
        output_reserve_tokens: int = OUTPUT_RESERVE_TOKENS,
        context_margin_tokens: int = CONTEXT_MARGIN_TOKENS,
        stream_max_seconds: float = STREAM_MAX_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        checkpoint_seconds: float = CHECKPOINT_SECONDS,
        cancel_poll_seconds: float = CANCEL_POLL_SECONDS,
        replay_poll_seconds: float = REPLAY_POLL_SECONDS,
        count_tokens: Callable[[str], int] = count_tokens_default,
        citation_validator: CitationValidator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_provider = embedding_provider
        self._llm = llm_provider
        self._history_max_messages = history_max_messages
        self._output_reserve_tokens = output_reserve_tokens
        self._context_margin_tokens = context_margin_tokens
        self._stream_max_seconds = stream_max_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._checkpoint_seconds = checkpoint_seconds
        self._cancel_poll_seconds = cancel_poll_seconds
        self._replay_poll_seconds = replay_poll_seconds
        self._count_tokens = count_tokens
        self._citation_validator = citation_validator

    @property
    def _context_window(self) -> int | None:
        return self._llm.context_window if self._llm is not None else None


    async def prepare(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content: str,
        client_message_id: str | None,
    ) -> PreparedStream:
        """Persist the turn, retrieve evidence and return everything the stream needs.

        The query embedding is deliberately made before the database session. It is remote
        I/O and must never occupy a pooled SQL connection while the provider is slow.
        """
        question = content.strip()
        if not question:
            raise ChatContextExceeded("La pregunta está vacía.")

        # This is the only provider call in preparation and runs with no SQL session open.
        query_vector: list[float] | None = None
        embedding_error: Exception | None = None
        if self._embedding_provider is None:
            embedding_error = EmbeddingNotConfigured(
                "El proveedor de embeddings no está configurado."
            )
        else:
            try:
                query_vector = await embed_query(self._embedding_provider, question)
            except Exception as exc:  # noqa: BLE001 - mapped to the terminal SSE error below
                embedding_error = exc

        async with self._session_factory() as db:
            # Ownership and idempotency are checked before persistence.  The unique index
            # remains the final arbiter if two concurrent requests share a client-message id.
            conversation = await self._owned_conversation(db, user_id, conversation_id)
            subject_id = conversation.subject_id
            audio_id = conversation.audio_id if conversation.mode == "class" else None
            if client_message_id is not None:
                existing = await self._existing_assistant(
                    db,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    client_message_id=client_message_id,
                )
                if existing is not None:
                    return await self._replay_prepared(db, existing)

            model = self._llm.model if self._llm is not None else None
            # The turn clock comes from the database, not from this process: `CLOCK_TIMESTAMP`
            # is read after `_owned_conversation` took the row lock, so a turn that waited
            # for the lock is still ordered after the previous one even when the API hosts'
            # clocks disagree. `now()` would be wrong here because it is fixed when this
            # transaction started, before a lock wait. The assistant is marked one
            # microsecond later, since both rows otherwise share the transaction timestamp
            # and the random UUID would decide question-vs-answer order.
            turn_created_at = await db.scalar(select(func.clock_timestamp()))
            if turn_created_at is None:  # pragma: no cover - PostgreSQL always returns one row
                raise RuntimeError("clock_timestamp() returned no value")
            user_message = Message(
                user_id=user_id,
                conversation_id=conversation_id,
                role="user",
                content=question,
                status="completed",
                citas=[],
                created_at=turn_created_at,
            )
            assistant = Message(
                user_id=user_id,
                conversation_id=conversation_id,
                role="assistant",
                content="",
                client_message_id=client_message_id,
                status="streaming",
                citas=[],
                model=model,
                prompt_version=PROMPT_VERSION,
                input_tokens=0,
                output_tokens=0,
                created_at=turn_created_at + ASSISTANT_CREATED_AT_OFFSET,
            )
            db.add_all([user_message, assistant])
            try:
                await db.flush()
                user_message_id = user_message.id
                assistant_id = assistant.id
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if client_message_id is not None:
                    existing = await self._existing_assistant(
                        db,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        client_message_id=client_message_id,
                    )
                    if existing is not None:
                        return await self._replay_prepared(db, existing)
                raise

            if embedding_error is not None or query_vector is None:
                await self._fail_message(db, user_id=user_id, message_id=assistant_id)
                return self._error_prepared(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    error=_map_search_error(embedding_error or EmbeddingProviderError("")),
                    model=model,
                )

            try:
                hits = await SearchService(
                    db, provider=self._embedding_provider
                ).retrieve_with_vector(
                    user_id=user_id,
                    query_text=question,
                    query_vector=query_vector,
                    subject_id=subject_id,
                    audio_id=audio_id,
                    max_query_chars=MESSAGE_MAX_CHARS,
                )
            except Exception as exc:  # noqa: BLE001 - every pre-stream failure becomes SSE error
                await db.rollback()
                error = _map_search_error(exc)
                await self._fail_message(db, user_id=user_id, message_id=assistant_id)
                return self._error_prepared(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    error=error,
                    model=model,
                )

            if not hits:
                await db.execute(
                    update(Message)
                    .where(Message.user_id == user_id, Message.id == assistant_id)
                    .values(
                        content=ABSTENTION_TEXT,
                        status="completed",
                        citas=[],
                        output_tokens=self._count_tokens(ABSTENTION_TEXT),
                    )
                )
                await db.commit()
                return PreparedStream(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_id,
                    model=model,
                    abstention_text=ABSTENTION_TEXT,
                )

            if self._llm is None or self._context_window is None:
                await db.rollback()
                await self._fail_message(db, user_id=user_id, message_id=assistant_id)
                return self._error_prepared(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    error=ChatStreamError(
                        code="provider_unavailable",
                        message="El proveedor LLM no está configurado.",
                    ),
                    model=model,
                )

            try:
                system = load_system_prompt()
                history = await self._load_history(
                    db,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    current_ids=(user_message_id, assistant_id),
                )
                plan = build_chat_prompt(
                    system=system,
                    question=question,
                    chunks=hits,
                    history=history,
                    context_window=self._context_window,
                    output_reserve_tokens=self._output_reserve_tokens,
                    context_margin_tokens=self._context_margin_tokens,
                    count_tokens=self._count_tokens,
                )
                await self._insert_sources(
                    db,
                    user_id=user_id,
                    message_id=assistant_id,
                    current=plan.sources,
                )
                await db.execute(
                    update(Message)
                    .where(Message.user_id == user_id, Message.id == assistant_id)
                    .values(
                        model=self._llm.model,
                        prompt_version=PROMPT_VERSION,
                        input_tokens=plan.input_tokens,
                    )
                )
                await db.commit()
            except ChatContextExceeded as exc:
                await db.rollback()
                await self._fail_message(db, user_id=user_id, message_id=assistant_id)
                return self._error_prepared(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    error=ChatStreamError(code=exc.code, message=exc.message),
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001 - no half-written prompt reaches the model
                logger.error("chat.prepare.prompt_failed error=%s", type(exc).__name__)
                await db.rollback()
                await self._fail_message(db, user_id=user_id, message_id=assistant_id)
                return self._error_prepared(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=assistant_id,
                    error=ChatStreamError(
                        code="internal_error",
                        message="No se pudo preparar la respuesta.",
                    ),
                    model=model,
                )

            return PreparedStream(
                user_id=user_id,
                conversation_id=conversation_id,
                assistant_message_id=assistant_id,
                messages=plan.messages,
                sources=plan.sources,
                input_tokens=plan.input_tokens,
                model=self._llm.model,
            )

    async def stream_events(
        self, prepared: PreparedStream, *, last_event_id: int = 0
    ) -> AsyncGenerator[str, None]:
        """Yield the SSE frames for a prepared turn, including replay and cancellation."""
        # The stream task must bind the tenant itself: short-lived sessions opened from
        # the factory do not share the request's SQL transaction, only this context.
        bind_user(prepared.user_id)
        if prepared.replay is not None:
            async for event in self._replay_events(prepared, last_event_id):
                yield event
            return
        # A new turn cannot resume another message's event ids. `Last-Event-ID` is only
        # meaningful when `prepare` found the persisted assistant row and built a replay.
        offset = 0
        yield self._snapshot_event(prepared.assistant_message_id, offset, "streaming")
        if prepared.error is not None:
            yield self._error_event(prepared.error, offset + 1)
            return
        if prepared.abstention_text is not None:
            text = prepared.abstention_text
            yield self._delta_event(text, offset + len(text))
            yield self._done_event(
                prepared,
                status="completed",
                event_id=offset + len(text) + 1,
            )
            return
        events = self._live_events(prepared)
        try:
            async for event in events:
                yield event
        finally:
            # Closing the outer generator on client disconnect must also close the live
            # generator: otherwise its `finally` (stop producer + persist partial) is only
            # scheduled for later and the message can stay `streaming`.
            await events.aclose()


    async def _owned_conversation(
        self, db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation:
        # FOR UPDATE serializes `prepare` with `DELETE /chat/conversations/{id}`: a delete
        # that committed while this transaction waited re-evaluates `deleted_at IS NULL` and
        # the turn becomes 404 instead of inserting messages after the purge.
        conversation = await db.scalar(
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if conversation is None:
            raise ChatConversationNotFound("Conversación no encontrada.")
        return conversation

    async def _existing_assistant(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        client_message_id: str,
    ) -> Message | None:
        row = await db.scalar(
            select(Message).where(
                Message.user_id == user_id,
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.client_message_id == client_message_id,
            )
        )
        return row

    async def _replay_prepared(self, db: AsyncSession, message: Message) -> PreparedStream:
        del db
        citations = parse_citations(message.citas)
        return PreparedStream(
            user_id=message.user_id,
            conversation_id=message.conversation_id,
            assistant_message_id=message.id,
            input_tokens=message.input_tokens or 0,
            model=message.model,
            prompt_version=message.prompt_version or PROMPT_VERSION,
            replay=ReplayState(
                message_id=message.id,
                content=message.content,
                status=message.status,
                citations=citations,
                created_at=message.created_at,
                tool_calls=tuple(parse_tool_calls(message.tool_calls)),
            ),
        )

    async def _load_history(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        current_ids: tuple[uuid.UUID, uuid.UUID],
    ) -> tuple[HistoryTurn, ...]:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(
                        Message.user_id == user_id,
                        Message.conversation_id == conversation_id,
                        Message.id.notin_(current_ids),
                        Message.status == "completed",
                        Message.role.in_(("user", "assistant")),
                    )
                    .order_by(
                        Message.created_at.desc(),
                        message_role_rank().desc(),
                        Message.id.desc(),
                    )
                    .limit(self._history_max_messages)
                )
            )
            .scalars()
            .all()
        )
        rows = list(reversed(list(rows)))
        history = [
            HistoryMessage(
                id=row.id,
                role=row.role,
                content=row.content,
                status=row.status,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return tuple(group_history(history))

    async def _insert_sources(
        self,
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        message_id: uuid.UUID,
        current: Sequence[SourceItem],
    ) -> None:
        rows: list[MessageSource] = []
        seen: set[tuple[object, ...]] = set()
        for source in current:
            chunk = source.chunk
            if chunk.transcript_id is None or chunk.transcript_version is None:
                continue
            key = (
                "transcript",
                chunk.audio_id,
                chunk.transcript_id,
                chunk.transcript_version,
                chunk.chunk_id,
                chunk.segment_id,
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                MessageSource(
                    user_id=user_id,
                    message_id=message_id,
                    kind="transcript",
                    audio_id=chunk.audio_id,
                    transcript_id=chunk.transcript_id,
                    transcript_version=chunk.transcript_version,
                    chunk_id=chunk.chunk_id,
                    segment_id=chunk.segment_id,
                )
            )
        if rows:
            db.add_all(rows)
            await db.flush()

    async def _fail_message(
        self, db: AsyncSession, *, user_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        await db.execute(
            update(Message)
            .where(
                Message.user_id == user_id,
                Message.id == message_id,
                Message.status == "streaming",
            )
            .values(status="failed")
        )
        await db.commit()

    def _error_prepared(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
        error: ChatStreamError,
        model: str | None,
    ) -> PreparedStream:
        return PreparedStream(
            user_id=user_id,
            conversation_id=conversation_id,
            assistant_message_id=message_id,
            model=model,
            error=error,
        )


async def cancel_message(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
) -> str:
    """Idempotent cancel: only a live assistant message transitions to `cancelled`."""
    conversation = await db.scalar(
        select(Conversation.id).where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise ChatConversationNotFound("Conversación no encontrada.")
    status = await db.scalar(
        select(Message.status).where(
            Message.user_id == user_id,
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
        )
    )
    if status is None:
        raise ChatMessageNotFound("Mensaje no encontrado.")
    if status == "cancelled":
        signal_cancel(message_id)
        return "cancelled"
    if status != "streaming":
        raise ChatInvalidTransition("El mensaje ya terminó.")
    result = await db.execute(
        update(Message)
        .where(
            Message.user_id == user_id,
            Message.id == message_id,
            Message.status == "streaming",
        )
        .values(status="cancelled")
        .returning(Message.status)
    )
    actual = result.scalar_one_or_none()
    await db.commit()
    if actual is None:
        actual = await db.scalar(
            select(Message.status).where(
                Message.user_id == user_id,
                Message.id == message_id,
            )
        )
    if actual == "cancelled":
        signal_cancel(message_id)
        return "cancelled"
    raise ChatInvalidTransition("El mensaje ya terminó.")


def _map_search_error(exc: Exception) -> ChatStreamError:
    if isinstance(exc, EmbeddingRateLimited):
        return ChatStreamError(
            code="rate_limited",
            message="El proveedor de embeddings rechazó la petición por cuota.",
        )
    if isinstance(exc, EmbeddingInputTooLong):
        return ChatStreamError(
            code="context_exceeded",
            message="La pregunta excede el contexto del proveedor de embeddings.",
        )
    if isinstance(exc, (EmbeddingNotConfigured, EmbeddingProviderError, SearchNotConfigured)):
        return ChatStreamError(
            code="provider_unavailable",
            message="El proveedor de embeddings no está disponible.",
        )
    if isinstance(
        exc,
        (
            EmbeddingDimensionMismatch,
            SearchProviderMismatch,
            SearchHnswUnsupported,
            SearchVectorRequired,
            SearchInvalidQuery,
            EmbeddingError,
        ),
    ):
        return ChatStreamError(
            code="internal_error",
            message="No se pudo consultar el índice de la clase.",
        )
    logger.error("chat.prepare.search_failed error=%s", type(exc).__name__)
    return ChatStreamError(
        code="internal_error",
        message="No se pudo consultar el índice de la clase.",
    )


