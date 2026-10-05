"""I-S2-AN-05/I-S2-AN-11: restricciones persistentes que habilitan dedupe y chat."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Audio,
    Conversation,
    Message,
    MessageSource,
    VariantConfirmationToken,
)
from tests.integration.actors import make_actor, make_audio, make_subject

pytestmark = pytest.mark.integration


async def test_i_s2_an_05_variant_token_cannot_cross_tenants_or_be_consumed_without_reservation(
    db_session: AsyncSession,
) -> None:
    """El token de variante queda ligado al tenant y su consumo exige la segunda reserva."""
    alice, bob = await make_actor(db_session), await make_actor(db_session)
    canonical = await make_audio(db_session, alice)
    token = VariantConfirmationToken(
        user_id=alice.user_id,
        token_hash="hash-" + uuid.uuid4().hex,
        canonical_audio_id=canonical.id,
        content_sha256="a" * 64,
        subject_id=canonical.subject_id,
        class_date=canonical.class_date,
        class_timezone=canonical.class_timezone,
        language_code=canonical.language_code,
        metadata_digest="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(token)
    await db_session.flush()

    async with db_session.begin_nested():
        token.user_id = bob.user_id
        with pytest.raises(IntegrityError):
            await db_session.flush()
    await db_session.refresh(token)

    async with db_session.begin_nested():
        token.consumed_at = datetime.now(UTC)
        with pytest.raises(IntegrityError):
            await db_session.flush()


async def test_i_s2_an_05_dedupe_unique_identity_is_scoped_to_live_audio(
    db_session: AsyncSession,
) -> None:
    owner = await make_actor(db_session)
    subject = await make_subject(db_session, owner)
    fields = dict(
        user_id=owner.user_id,
        subject_id=subject.id,
        sha256="c" * 64,
        class_date=date(2026, 9, 21),
        class_timezone="America/Bogota",
        language_code="es",
    )
    db_session.add(Audio(**fields))
    await db_session.flush()
    async with db_session.begin_nested():
        db_session.add(Audio(**fields))
        with pytest.raises(IntegrityError):
            await db_session.flush()


async def test_i_s2_an_11_chat_scope_idempotency_and_cascade_sources(
    db_session: AsyncSession,
) -> None:
    owner = await make_actor(db_session)
    conversation = Conversation(user_id=owner.user_id, mode="all")
    db_session.add(conversation)
    await db_session.flush()
    first = Message(
        user_id=owner.user_id, conversation_id=conversation.id, role="user", client_message_id="c1"
    )
    db_session.add(first)
    await db_session.flush()
    source = MessageSource(user_id=owner.user_id, message_id=first.id, kind="general")
    db_session.add(source)
    await db_session.flush()
    async with db_session.begin_nested():
        db_session.add(
            Message(
                user_id=owner.user_id,
                conversation_id=conversation.id,
                role="user",
                client_message_id="c1",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    await db_session.delete(conversation)
    await db_session.flush()
    assert await db_session.get(MessageSource, source.id) is None

    foreign_subject = await make_subject(db_session, await make_actor(db_session))
    async with db_session.begin_nested():
        db_session.add(
            Conversation(user_id=owner.user_id, mode="subject", subject_id=foreign_subject.id)
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
