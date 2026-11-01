"""Request/response schemas for the M7 chat endpoints (L2.5 and A2.12)."""

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TITLE_MAX_CHARS = 200


class ConversationCreateIn(BaseModel):
    """Mode and scope of a new conversation; coherence is checked by the service."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # A plain string, not a Literal: an unknown mode must answer the M7 contract code
    # `chat_mode_invalid`, not the generic pydantic `validation_failed`.
    mode: str = Field(min_length=1, max_length=16)
    subject_id: uuid.UUID | None = None
    audio_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_CHARS)


class ConversationOut(BaseModel):
    id: str
    mode: str
    subject_id: str | None
    audio_id: str | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ConversationListOut(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    status: str
    client_message_id: str | None
    citas: list[dict[str, Any]]
    created_at: datetime


class MessageHistoryOut(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None = None


class SendMessageIn(BaseModel):
    """One chat turn; `client_message_id` is the idempotency key for retries."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    client_message_id: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def _content_must_have_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content no puede estar vacío")
        if _CONTROL_CHARS.search(value):
            raise ValueError("content contiene caracteres de control")
        return stripped

    @field_validator("client_message_id")
    @classmethod
    def _client_id_must_be_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("client_message_id no puede estar vacío")
        if _CONTROL_CHARS.search(value):
            raise ValueError("client_message_id contiene caracteres de control")
        return value


class CancelMessageOut(BaseModel):
    message_id: str
    status: str
