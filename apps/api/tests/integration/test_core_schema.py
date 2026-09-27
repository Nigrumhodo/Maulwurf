"""A1.5: esquema núcleo contra PostgreSQL real (U-S1-AN-03 en BD, base de I-S1-AN-05)."""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models import Audio, GoogleCredential, IngestionAttempt, OutboxEvent, Subject, User
from app.services.tenant import get_owned, owned_by

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


async def _user(session: AsyncSession, sub: str) -> User:
    user = User(google_sub=sub, email=f"{sub}@example.test")
    session.add(user)
    await session.flush()
    return user


async def _subject(session: AsyncSession, user: User, name: str = "Cálculo") -> Subject:
    subject = Subject(user_id=user.id, name=name)
    session.add(subject)
    await session.flush()
    return subject


async def _expect_integrity_error(session: AsyncSession, row: object) -> None:
    async with session.begin_nested():
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_google_tokens_are_stored_encrypted(db_session: AsyncSession) -> None:
    user = await _user(db_session, "sub-crypto")
    token = "ya29.plaintext-must-not-reach-db"
    blob, version = crypto.encrypt(token, user_id=user.id, field="access_token")
    db_session.add(GoogleCredential(user_id=user.id, access_token_enc=blob, key_version=version))
    await db_session.flush()

    raw = await db_session.scalar(
        text("SELECT encode(access_token_enc, 'escape') FROM google_credentials WHERE user_id=:u"),
        {"u": user.id},
    )

    assert raw is not None and token not in raw
    stored = await db_session.get(GoogleCredential, user.id)
    assert stored is not None
    assert crypto.decrypt(
        stored.access_token_enc, key_version=stored.key_version, user_id=user.id,
        field="access_token",
    ) == token


async def test_subject_names_are_unique_per_user_case_insensitive(db_session: AsyncSession) -> None:
    alice = await _user(db_session, "sub-alice")
    bob = await _user(db_session, "sub-bob")
    await _subject(db_session, alice, "Física")
    await _subject(db_session, bob, "Física")  # otro usuario: permitido

    await _expect_integrity_error(db_session, Subject(user_id=alice.id, name="FÍSICA"))


async def test_audio_cannot_reference_another_users_subject(db_session: AsyncSession) -> None:
    alice = await _user(db_session, "sub-a")
    bob = await _user(db_session, "sub-b")
    alice_subject = await _subject(db_session, alice)

    await _expect_integrity_error(
        db_session,
        Audio(
            user_id=bob.id, subject_id=alice_subject.id, class_date=date(2026, 9, 21),
            class_timezone="America/Bogota", language_code="es",
        ),
    )


async def test_tenant_scope_hides_other_users_rows(db_session: AsyncSession) -> None:
    alice = await _user(db_session, "sub-scope-a")
    bob = await _user(db_session, "sub-scope-b")
    alice_subject = await _subject(db_session, alice)

    assert await get_owned(db_session, Subject, bob.id, alice_subject.id) is None
    assert await get_owned(db_session, Subject, alice.id, alice_subject.id) is alice_subject
    bob_rows = (await db_session.scalars(owned_by(Subject, bob.id))).all()
    assert bob_rows == []


def _event(user: User, type_: str, *, enabled: bool, blocked: str | None) -> OutboxEvent:
    return OutboxEvent(
        user_id=user.id, type=type_, resource_type="audio", resource_id=uuid.uuid4(),
        resource_version=1, enabled=enabled, blocked_reason=blocked,
    )


@pytest.mark.parametrize(
    ("type_", "enabled", "blocked"),
    [
        ("index_requested", False, "cleanup_pending"),
        ("index_requested", True, None),
        ("analyze_requested", False, "cleanup_pending"),
        ("sync_task_event", True, None),
    ],
)
async def test_outbox_accepts_valid_gate_states(
    db_session: AsyncSession, type_: str, enabled: bool, blocked: str | None
) -> None:
    user = await _user(db_session, f"sub-ok-{type_}-{enabled}")
    db_session.add(_event(user, type_, enabled=enabled, blocked=blocked))
    await db_session.flush()


@pytest.mark.parametrize(
    ("type_", "enabled", "blocked"),
    [
        ("index_requested", False, None),  # bloqueado sin motivo
        ("index_requested", True, "cleanup_pending"),  # habilitado con motivo
        ("sync_task_event", False, "cleanup_pending"),  # Calendar no depende del cleanup
        ("notify", False, None),
    ],
)
async def test_outbox_rejects_invalid_gate_states(
    db_session: AsyncSession, type_: str, enabled: bool, blocked: str | None
) -> None:
    user = await _user(db_session, f"sub-bad-{type_}-{enabled}-{blocked}")

    await _expect_integrity_error(db_session, _event(user, type_, enabled=enabled, blocked=blocked))


async def test_only_one_active_attempt_per_audio(db_session: AsyncSession) -> None:
    user = await _user(db_session, "sub-attempts")
    subject = await _subject(db_session, user)
    audio = Audio(
        user_id=user.id, subject_id=subject.id, class_date=date(2026, 9, 21),
        class_timezone="America/Bogota", language_code="es",
    )
    db_session.add(audio)
    await db_session.flush()

    def attempt(status: str, fencing: int) -> IngestionAttempt:
        return IngestionAttempt(
            user_id=user.id, audio_id=audio.id, privacy_notice_version="v1",
            cloud_processing_accepted_at=NOW, third_party_voice_acknowledged_at=NOW,
            declared_providers={"asr": "nvidia-riva"}, status=status,
            owner_instance="ingest-test", fencing_token=fencing,
            lease_expires_at=NOW + timedelta(minutes=1),
        )

    db_session.add(attempt("cancelled", 1))
    db_session.add(attempt("awaiting_upload", 2))
    await db_session.flush()

    await _expect_integrity_error(db_session, attempt("receiving", 3))
    stored = (await db_session.scalars(select(IngestionAttempt.cleanup_status))).all()
    assert set(stored) == {"pending"}
