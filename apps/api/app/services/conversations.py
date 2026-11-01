"""Tenant-scoped conversation CRUD and paginated history (A2.12, M7/DISENO §3.10).

The tables and the streaming turn endpoint are A2.1/L2.5; this module only adds the missing
HTTP surface: create with a coherent mode scope, tenant listing, keyset history and the
tombstone that purges content and sources. Every query filters `user_id` (ADR-0006), so a
foreign conversation behaves exactly like a missing one.
"""

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Result, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, not_found, validation_failed
from app.models import Audio, Conversation, Message, MessageSource
from app.services.subjects import get_active
from app.services.tenant import get_owned

CHAT_MODES = ("all", "subject", "class")
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
CURSOR_VERSION = 1
MAX_CURSOR_LENGTH = 512
_CURSOR_INVALID = "cursor inválido o malformado"
_CURSOR_STALE = "el cursor pertenece a otra conversación"


@dataclass(frozen=True)
class HistoryCursor:
    """Position of the oldest message already returned to the client."""

    conversation_id: uuid.UUID
    created_at: datetime
    message_id: uuid.UUID


@dataclass(frozen=True)
class ConversationListItem:
    conversation: Conversation
    last_message_at: datetime | None


def _mode_invalid(field: str, message: str) -> ApiError:
    # `chat_mode_invalid` is the M7 contract for a mode/scope mismatch, including an unknown
    # mode. `fields` keeps the existing per-field error shape for the UI.
    return ApiError(
        422,
        "chat_mode_invalid",
        "El modo de la conversación no coincide con su alcance.",
        {"allowed_modes": list(CHAT_MODES), "fields": {field: message}},
    )


def validate_scope(
    mode: str, subject_id: uuid.UUID | None, audio_id: uuid.UUID | None
) -> tuple[str, uuid.UUID | None, uuid.UUID | None]:
    """Return the normalised scope or raise `422 chat_mode_invalid`.

    The database CHECK only enforces the minimum per mode; the API also rejects the extra
    identifier, so a conversation can never imply two different scopes.
    """
    if mode not in CHAT_MODES:
        raise _mode_invalid("mode", "debe ser 'all', 'subject' o 'class'")
    if mode == "all":
        if subject_id is not None or audio_id is not None:
            raise _mode_invalid("subject_id", "el modo 'all' no admite subject_id ni audio_id")
        return mode, None, None
    if mode == "subject":
        if subject_id is None:
            raise _mode_invalid("subject_id", "requerido para el modo 'subject'")
        if audio_id is not None:
            raise _mode_invalid("audio_id", "no admitido en el modo 'subject'")
        return mode, subject_id, None
    if audio_id is None:
        raise _mode_invalid("audio_id", "requerido para el modo 'class'")
    if subject_id is not None:
        raise _mode_invalid("subject_id", "no admitido en el modo 'class'")
    return mode, None, audio_id


def encode_cursor(cursor: HistoryCursor) -> str:
    """Opaque base64url cursor; it grants nothing: every page still filters by tenant."""
    payload = json.dumps(
        {
            "v": CURSOR_VERSION,
            "c": str(cursor.conversation_id),
            "t": cursor.created_at.astimezone(UTC).isoformat(),
            "m": str(cursor.message_id),
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def decode_cursor(value: str, conversation_id: uuid.UUID) -> HistoryCursor:
    """Validate the opaque cursor; malformed, stale or foreign positions are a typed 422."""
    if not value or len(value) > MAX_CURSOR_LENGTH:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
            raise ValueError("unsupported cursor")
        cursor_conversation = uuid.UUID(str(data["c"]))
        created_at = datetime.fromisoformat(str(data["t"]))
        message_id = uuid.UUID(str(data["m"]))
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError) as exc:
        raise validation_failed({"cursor": _CURSOR_INVALID}) from exc
    if created_at.tzinfo is None:
        raise validation_failed({"cursor": _CURSOR_INVALID})
    if cursor_conversation != conversation_id:
        raise validation_failed({"cursor": _CURSOR_STALE})
    return HistoryCursor(
        conversation_id=cursor_conversation,
        created_at=created_at.astimezone(UTC),
        message_id=message_id,
    )


async def create_conversation(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    mode: str,
    subject_id: uuid.UUID | None,
    audio_id: uuid.UUID | None,
    title: str | None,
) -> Conversation:
    """Create a conversation whose subject/class belongs to the same tenant."""
    mode, subject_id, audio_id = validate_scope(mode, subject_id, audio_id)
    # Lock the scope row so a concurrent subject/class deletion either commits before this
    # check (404) or waits for the conversation, never leaving a live conversation that
    # points at a tombstoned resource.
    if subject_id is not None:
        if await get_active(db, user_id, subject_id, for_update=True) is None:
            raise not_found()
    if audio_id is not None:
        audio = await get_owned(db, Audio, user_id, audio_id, lock="share")
        if audio is None or audio.deleted_at is not None:
            raise not_found()
    conversation = Conversation(
        user_id=user_id,
        mode=mode,
        subject_id=subject_id,
        audio_id=audio_id,
        title=title,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def get_active_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise not_found()
    return conversation


async def list_conversations(db: AsyncSession, user_id: uuid.UUID) -> list[ConversationListItem]:
    """Active conversations of the tenant, most recently used first.

    `last_message_at` comes from one grouped subquery, so the listing order does not require
    `conversations.updated_at` to be written by the streaming path (L2.5 does not touch it).
    """
    last_message = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .where(Message.user_id == user_id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Conversation, last_message.c.last_message_at)
            .outerjoin(last_message, last_message.c.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
            .order_by(
                func.coalesce(last_message.c.last_message_at, Conversation.created_at).desc(),
                Conversation.id.desc(),
            )
        )
    ).all()
    return [
        ConversationListItem(conversation=conversation, last_message_at=last_message_at)
        for conversation, last_message_at in rows
    ]


async def page_messages(
    db: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[Message], str | None]:
    """Newest page returned oldest-first; `next_cursor` walks towards older messages."""
    await get_active_conversation(db, user_id, conversation_id)
    position = decode_cursor(cursor, conversation_id) if cursor is not None else None
    query = select(Message).where(
        Message.user_id == user_id,
        Message.conversation_id == conversation_id,
    )
    if position is not None:
        query = query.where(
            or_(
                Message.created_at < position.created_at,
                and_(
                    Message.created_at == position.created_at,
                    Message.id < position.message_id,
                ),
            )
        )
    rows = list(
        (
            await db.execute(
                query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        oldest = page[-1]
        next_cursor = encode_cursor(
            HistoryCursor(
                conversation_id=conversation_id,
                created_at=oldest.created_at,
                message_id=oldest.id,
            )
        )
    return list(reversed(page)), next_cursor


async def delete_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    """Tombstone the conversation and purge its messages and sources in one transaction.

    The conditional UPDATE decides the race against a second DELETE (ADR-0006: "already
    gone" is 404). The title is content, so the tombstone clears it. Sources are removed
    before messages for verifiable counts even though the FK cascade would also delete them.
    """
    now = datetime.now(UTC)
    marked = await db.execute(
        update(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
        .execution_options(synchronize_session=False)
        .values(deleted_at=now, updated_at=now, title=None)
    )
    if _rowcount(marked) != 1:
        raise not_found()
    message_ids = select(Message.id).where(
        Message.user_id == user_id,
        Message.conversation_id == conversation_id,
    )
    await db.execute(
        delete(MessageSource)
        .where(
            MessageSource.user_id == user_id,
            MessageSource.message_id.in_(message_ids),
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Message)
        .where(
            Message.user_id == user_id,
            Message.conversation_id == conversation_id,
        )
        .execution_options(synchronize_session=False)
    )


def _rowcount(result: Result[Any]) -> int:
    # A DML returns a `CursorResult` at runtime even though `Session.execute` is typed as
    # `Result`; the purge counts are decided by affected rows, never by in-memory state.
    return int(cast("CursorResult[Any]", result).rowcount)
