"""Request/response schemas for the streaming chat endpoints (L2.5)."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


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
