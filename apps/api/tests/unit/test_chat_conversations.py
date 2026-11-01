"""U support for A2.12: conversation scope coherence and the opaque history cursor.

The HTTP boundary and the purge against PostgreSQL are proven by
`tests/integration/test_chat_conversations.py` (I-S2-AN-11). This module keeps the pure
decisions fast: which `mode`/`subject_id`/`audio_id` combinations are valid and how the
keyset cursor is encoded, rejected and bound to its conversation.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.errors import ApiError
from app.services.conversations import (
    MAX_CURSOR_LENGTH,
    HistoryCursor,
    decode_cursor,
    encode_cursor,
    validate_scope,
)

SUBJECT_ID = uuid.uuid4()
AUDIO_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()
MESSAGE_ID = uuid.uuid4()
CREATED_AT = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)


# --- Scope coherence (mode + subject_id/audio_id) ---------------------------------------


def test_all_mode_requires_no_scope() -> None:
    assert validate_scope("all", None, None) == ("all", None, None)


@pytest.mark.parametrize(
    ("subject_id", "audio_id"),
    [(SUBJECT_ID, None), (None, AUDIO_ID), (SUBJECT_ID, AUDIO_ID)],
)
def test_all_mode_rejects_any_scope(
    subject_id: uuid.UUID | None, audio_id: uuid.UUID | None
) -> None:
    with pytest.raises(ApiError) as excinfo:
        validate_scope("all", subject_id, audio_id)

    assert (excinfo.value.status_code, excinfo.value.code) == (422, "chat_mode_invalid")


def test_subject_mode_requires_exactly_a_subject() -> None:
    assert validate_scope("subject", SUBJECT_ID, None) == ("subject", SUBJECT_ID, None)

    with pytest.raises(ApiError) as missing:
        validate_scope("subject", None, None)
    assert missing.value.code == "chat_mode_invalid"
    assert missing.value.details["fields"] == {"subject_id": "requerido para el modo 'subject'"}

    with pytest.raises(ApiError) as both:
        validate_scope("subject", SUBJECT_ID, AUDIO_ID)
    assert both.value.code == "chat_mode_invalid"


def test_class_mode_requires_exactly_a_class() -> None:
    assert validate_scope("class", None, AUDIO_ID) == ("class", None, AUDIO_ID)

    with pytest.raises(ApiError) as missing:
        validate_scope("class", None, None)
    assert missing.value.code == "chat_mode_invalid"
    assert missing.value.details["fields"] == {"audio_id": "requerido para el modo 'class'"}

    with pytest.raises(ApiError) as both:
        validate_scope("class", SUBJECT_ID, AUDIO_ID)
    assert both.value.code == "chat_mode_invalid"


def test_unknown_mode_is_a_chat_mode_invalid() -> None:
    with pytest.raises(ApiError) as excinfo:
        validate_scope("everything", None, None)

    assert (excinfo.value.status_code, excinfo.value.code) == (422, "chat_mode_invalid")
    assert excinfo.value.details["allowed_modes"] == ["all", "subject", "class"]


# --- Opaque keyset cursor ---------------------------------------------------------------


def test_cursor_round_trips_the_conversation_and_position() -> None:
    cursor = HistoryCursor(
        conversation_id=CONVERSATION_ID, created_at=CREATED_AT, message_id=MESSAGE_ID
    )

    assert decode_cursor(encode_cursor(cursor), CONVERSATION_ID) == cursor


def test_cursor_accepts_a_non_utc_offset_and_keeps_the_instant() -> None:
    bogota = CREATED_AT.astimezone(timezone(timedelta(hours=-5)))
    cursor = HistoryCursor(
        conversation_id=CONVERSATION_ID, created_at=bogota, message_id=MESSAGE_ID
    )

    decoded = decode_cursor(encode_cursor(cursor), CONVERSATION_ID)

    assert decoded.created_at == CREATED_AT
    assert decoded.message_id == MESSAGE_ID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64!",
        base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode(),
        base64.urlsafe_b64encode(b'{"v":1}').rstrip(b"=").decode(),
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "c": str(CONVERSATION_ID),
                    "t": "2026-11-01T12:30:00",
                    "m": str(MESSAGE_ID),
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode(),
        "x" * (MAX_CURSOR_LENGTH + 1),
    ],
)
def test_malformed_cursor_is_a_typed_422(value: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        decode_cursor(value, CONVERSATION_ID)

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "validation_failed"
    assert excinfo.value.details == {"fields": {"cursor": "cursor inválido o malformado"}}


def test_cursor_from_another_conversation_is_rejected() -> None:
    cursor = HistoryCursor(
        conversation_id=CONVERSATION_ID, created_at=CREATED_AT, message_id=MESSAGE_ID
    )
    encoded = encode_cursor(cursor)

    with pytest.raises(ApiError) as excinfo:
        decode_cursor(encoded, uuid.uuid4())

    assert excinfo.value.code == "validation_failed"
