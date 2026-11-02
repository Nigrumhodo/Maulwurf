"""Streaming chat routes (L2.5, S2.md §L2.5 and DISENO §3.10) and M7 conversation CRUD (A2.12).

The POST authenticates and checks ownership on a short session, persists the turn and closes
that session before the `StreamingResponse` starts. The generator then uses the injected
session factory for checkpoints, cancellation polls and replay reads, so a slow model cannot
hold a request-scoped database connection.

A2.12 adds the conversation surface around that endpoint: create with a coherent mode scope,
tenant listing, paginated history and the tombstone that purges messages and sources.
"""

import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.background import BackgroundTask

from app.core.config import settings
from app.core.deps import (
    ChatSessions,
    CurrentSession,
    DbSession,
    MutationSession,
    resolve_mutation_session,
)
from app.core.errors import ApiError, not_found, validation_failed
from app.core.providers import EmbeddingProviderDep, LLMProviderDep
from app.core.redis import RedisDep
from app.models import Conversation
from app.schemas.chat import (
    CancelMessageOut,
    ConversationCreateIn,
    ConversationListOut,
    ConversationOut,
    MessageHistoryOut,
    MessageOut,
    SendMessageIn,
)
from app.services import conversations
from app.services.conversations import DEFAULT_PAGE_SIZE, MAX_CURSOR_LENGTH, MAX_PAGE_SIZE
from app.services.rag import (
    ChatContextExceeded,
    ChatConversationNotFound,
    ChatInvalidTransition,
    ChatMessageNotFound,
    RAGService,
    cancel_message,
    signal_cancel,
)
from app.services.rate_limiter import (
    MAX_STREAMS_PER_USER,
    acquire_stream,
    release_stream,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}


class _Slot:
    """Idempotent lease for one per-user concurrent stream slot."""

    def __init__(self, redis: Redis, user_id: uuid.UUID) -> None:
        self._redis = redis
        self.user_id = user_id
        self.released = False

    async def release(self) -> None:
        """Release the slot once, even when the generator and background both call this."""
        if self.released:
            return
        # Mark before awaiting: two tasks on the same event loop cannot both pass the check.
        self.released = True
        try:
            await release_stream(self._redis, self.user_id)
        except RedisError:
            # Allow the other exit path (or a later retry) to try again; the key TTL is the
            # final cleanup if Redis never recovers.
            self.released = False
            logger.warning("chat.stream_release_failed user=%s", self.user_id)


async def _release_stream_when_done(
    events: AsyncGenerator[str, None], slot: _Slot
) -> AsyncGenerator[str, None]:
    """Forward SSE frames, close the producer and release the slot on every exit path."""
    try:
        async for event in events:
            yield event
    finally:
        try:
            await events.aclose()
        finally:
            await slot.release()


def _last_event_id(raw: str | None) -> int:
    if raw is None:
        return 0
    try:
        value = int(raw.strip())
    except ValueError:
        return 0
    return max(0, value)


def _service(
    factory: ChatSessions,
    embeddings: EmbeddingProviderDep,
    llm: LLMProviderDep,
) -> RAGService:
    return RAGService(
        session_factory=factory,
        embedding_provider=embeddings,
        llm_provider=llm,
        output_reserve_tokens=settings.llm_output_reserve_tokens,
        context_margin_tokens=settings.llm_context_margin_tokens,
    )


def _conversation_out(
    conversation: Conversation, last_message_at: datetime | None
) -> ConversationOut:
    return ConversationOut(
        id=str(conversation.id),
        mode=conversation.mode,
        subject_id=str(conversation.subject_id) if conversation.subject_id else None,
        audio_id=str(conversation.audio_id) if conversation.audio_id else None,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        last_message_at=last_message_at,
    )


@router.post(
    "/chat/conversations",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Conversación creada con su alcance coherente"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        404: {"description": "Materia o clase inexistente o ajena (not_found)"},
        422: {"description": "chat_mode_invalid: modo o combinación de alcance inválidos"},
    },
)
async def create_conversation(
    body: ConversationCreateIn, session: MutationSession, db: DbSession
) -> ConversationOut:
    """Create a conversation scoped to all notes, one subject or one class."""
    conversation = await conversations.create_conversation(
        db,
        session.user_id,
        mode=body.mode,
        subject_id=body.subject_id,
        audio_id=body.audio_id,
        title=body.title,
    )
    out = _conversation_out(conversation, None)
    await db.commit()
    return out


@router.get(
    "/chat/conversations",
    responses={
        200: {"description": "Página de conversaciones activas, más recientes primero"},
        401: {"description": "Sin sesión (auth_required)"},
        422: {"description": "validation_failed: limit o cursor inválidos"},
    },
)
async def list_conversations(
    session: CurrentSession,
    db: DbSession,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> ConversationListOut:
    """List the authenticated user's conversations; other tenants are never visible.

    At most `limit` conversations are returned. `next_cursor` walks towards older activity
    and `null` means the listing is complete.
    """
    response.headers["Cache-Control"] = "no-store"
    rows, next_cursor = await conversations.list_conversations(
        db, session.user_id, limit=limit, cursor=cursor
    )
    return ConversationListOut(
        items=[_conversation_out(row.conversation, row.last_message_at) for row in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/chat/conversations/{conversation_id}/messages",
    responses={
        200: {"description": "Página de historial en orden cronológico y cursor a la anterior"},
        401: {"description": "Sin sesión (auth_required)"},
        404: {"description": "Conversación inexistente o ajena (not_found)"},
        422: {"description": "validation_failed: limit o cursor inválidos"},
    },
)
async def list_messages(
    conversation_id: uuid.UUID,
    session: CurrentSession,
    db: DbSession,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> MessageHistoryOut:
    """Paginated conversation history.

    A page contains the newest messages that fit `limit`, returned oldest-first. The opaque
    `next_cursor` walks towards older messages; `null` means the history is complete.
    """
    response.headers["Cache-Control"] = "no-store"
    rows, next_cursor = await conversations.page_messages(
        db,
        session.user_id,
        conversation_id,
        limit=limit,
        cursor=cursor,
    )
    return MessageHistoryOut(
        items=[
            MessageOut(
                id=str(row.id),
                role=row.role,
                content=row.content,
                status=row.status,
                client_message_id=row.client_message_id,
                citas=list(row.citas or []),
                created_at=row.created_at,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


@router.delete(
    "/chat/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Conversación marcada como borrada y contenido purgado"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        404: {"description": "Conversación inexistente, ajena o ya borrada (not_found)"},
    },
)
async def delete_conversation(
    conversation_id: uuid.UUID, session: MutationSession, db: DbSession
) -> Response:
    """Tombstone the conversation and purge its messages and message sources."""
    cancelled = await conversations.delete_conversation(db, session.user_id, conversation_id)
    await db.commit()
    # The transaction already cancelled these turns; the wake-up is only sent once the purge
    # is durable, so a rollback can never stop a stream whose rows still exist.
    for message_id in cancelled:
        signal_cancel(message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chat/conversations/{conversation_id}/messages",
    responses={
        200: {
            "description": ("text/event-stream: snapshot, delta, citation, ping and one done|error")
        },
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        404: {"description": "Conversación inexistente o ajena (not_found)"},
        422: {"description": "validation_failed: contenido o client_message_id inválidos"},
        429: {
            "description": (
                f"rate_limited: más de {MAX_STREAMS_PER_USER} streams simultáneos "
                "para el mismo usuario"
            )
        },
    },
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    request: Request,
    factory: ChatSessions,
    embeddings: EmbeddingProviderDep,
    llm: LLMProviderDep,
    redis: RedisDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> Response:
    """Persist one turn and stream its answer over SSE.

    Auth and CSRF run on a short request session. The stream slot is acquired next, before
    any embedding or LLM work, and released on every exit path. The database request session
    is closed before `RAGService.prepare` starts, so embedding I/O never holds a connection.
    """
    slot: _Slot | None = None
    try:
        async with factory() as db:
            session = await resolve_mutation_session(db, request)
            user_id = session.user_id
        await acquire_stream(redis, user_id)
        slot = _Slot(redis, user_id)

        service = _service(factory, embeddings, llm)
        try:
            prepared = await service.prepare(
                user_id=user_id,
                conversation_id=conversation_id,
                content=body.content,
                client_message_id=body.client_message_id,
            )
        except ChatConversationNotFound as exc:
            raise not_found() from exc
        except ChatContextExceeded as exc:
            raise validation_failed({"content": exc.message}) from exc

        events = _release_stream_when_done(
            service.stream_events(prepared, last_event_id=_last_event_id(last_event_id)),
            slot,
        )
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers=SSE_HEADERS,
            # A disconnect before the generator's first iteration skips its `finally`;
            # the response background releases the same idempotent lease.
            background=BackgroundTask(slot.release),
        )
    except BaseException:
        if slot is not None:
            await slot.release()
        raise


@router.post(
    "/chat/conversations/{conversation_id}/messages/{message_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Mensaje en streaming marcado como cancelled (idempotente)"},
        401: {"description": "Sin sesión (auth_required)"},
        403: {"description": "Origin/CSRF inválidos (csrf_invalid)"},
        404: {"description": "Conversación o mensaje inexistente o ajeno (not_found)"},
        409: {"description": "El mensaje ya terminó (invalid_transition)"},
    },
)
async def cancel(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    session: MutationSession,
    db: DbSession,
) -> CancelMessageOut:
    """Signal the live stream; the DB transition is authoritative and idempotent."""
    try:
        state = await cancel_message(
            db,
            user_id=session.user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    except (ChatConversationNotFound, ChatMessageNotFound) as exc:
        raise not_found() from exc
    except ChatInvalidTransition as exc:
        raise ApiError(409, "invalid_transition", exc.message) from exc
    return CancelMessageOut(message_id=str(message_id), status=state)
