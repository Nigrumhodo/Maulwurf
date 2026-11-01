"""A3.1 / U-S3-AN-01 against PostgreSQL: the M4 task CHECKs are real.

The unit counterpart (`tests/unit/test_task_schema.py`) reads the SQLAlchemy metadata; this
module proves that PostgreSQL, after `alembic upgrade head`, rejects the rows the contract
forbids: invented dates, out-of-range confidence, `scheduled` as a task status, a mixed
Calendar block, a non-positive base version and any cross-tenant reference.

No audio is uploaded, no provider is called and no Google credential is involved.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Segment, Task, TaskRevision, TaskSource
from tests.integration.actors import (
    Actor,
    make_actor,
    make_audio,
    make_processing_run,
    make_subject,
    make_task,
    make_transcript,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
DUE_DAY = date(2026, 11, 9)


def _task(actor: Actor, **overrides: Any) -> Task:
    """An unresolved manual task unless the test overrides the date/block/status."""
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "source": "extraction",
        "tipo": "examen",
        "titulo": "Examen parcial",
        "timezone": "America/Bogota",
        "all_day": True,
        "date_status": "missing",
    }
    values.update(overrides)
    return Task(**values)


async def _expect_integrity_error(session: AsyncSession, row: object) -> None:
    async with session.begin_nested():
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.flush()


async def _created_task(db: AsyncSession, actor: Actor, **overrides: Any) -> Task:
    task = _task(actor, **overrides)
    db.add(task)
    await db.flush()
    return task


# --- Date contract ---------------------------------------------------------------------


async def test_a_resolved_all_day_task_persists_only_its_day(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await _created_task(
        db_session, actor, date_status="resolved", due_date=DUE_DAY, all_day=True
    )

    stored = await db_session.get(Task, task.id)
    assert stored is not None
    assert stored.due_date == DUE_DAY
    assert stored.due_at is None
    assert stored.all_day is True


async def test_a_resolved_timed_task_persists_only_its_instant(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await _created_task(
        db_session, actor, date_status="resolved", due_at=NOW, all_day=False
    )

    stored = await db_session.get(Task, task.id)
    assert stored is not None
    assert stored.due_at == NOW
    assert stored.due_date is None


@pytest.mark.parametrize(
    "overrides",
    [
        # resolved + all-day cannot also carry an instant
        {"date_status": "resolved", "all_day": True, "due_date": DUE_DAY, "due_at": NOW},
        # resolved + timed cannot also carry a day
        {"date_status": "resolved", "all_day": False, "due_date": DUE_DAY},
        # resolved + timed needs the instant
        {"date_status": "resolved", "all_day": True, "due_at": NOW},
        # ambiguous/missing never invent a date
        {"date_status": "ambiguous", "due_date": DUE_DAY},
        {"date_status": "missing", "due_at": NOW},
    ],
)
async def test_incoherent_dates_are_rejected(
    db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(db_session, _task(actor, **overrides))


async def test_no_date_is_invented_for_an_unresolved_task(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    for status in ("ambiguous", "missing"):
        task = await _created_task(db_session, actor, date_status=status)
        assert task.due_date is None and task.due_at is None


# --- Confidence ------------------------------------------------------------------------


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0, None])
async def test_confidence_accepts_the_unit_interval_and_null(
    db_session: AsyncSession, confidence: float | None
) -> None:
    actor = await make_actor(db_session)
    task = await _created_task(db_session, actor, confidence_score=confidence)
    assert task.confidence_score == confidence


@pytest.mark.parametrize(
    "confidence", [-0.01, 1.01, float("inf"), float("-inf"), float("nan")]
)
async def test_confidence_outside_the_unit_interval_is_rejected(
    db_session: AsyncSession, confidence: float
) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(
        db_session, _task(actor, confidence_score=confidence)
    )


# --- Status vocabularies ---------------------------------------------------------------


async def test_scheduled_is_never_a_task_status(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(db_session, _task(actor, status="scheduled"))
    await _expect_integrity_error(
        db_session, _task(actor, calendar_sync_status="scheduled")
    )


async def test_confirmed_and_blocked_coexist_as_separate_columns(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await _created_task(
        db_session, actor, status="confirmed", calendar_sync_status="blocked"
    )

    stored = await db_session.get(Task, task.id)
    assert stored is not None
    assert stored.status == "confirmed"
    assert stored.calendar_sync_status == "blocked"


@pytest.mark.parametrize(
    "tipo", ["examen", "tarea", "entrega", "lectura", "recordatorio", "recomendacion", "proyecto"]
)
async def test_every_contract_activity_type_is_accepted(
    db_session: AsyncSession, tipo: str
) -> None:
    actor = await make_actor(db_session)
    assert (await _created_task(db_session, actor, tipo=tipo)).tipo == tipo


# --- Calendar block --------------------------------------------------------------------


async def test_the_calendar_block_accepts_each_pair_separately(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    timed = await _created_task(
        db_session, actor, event_start_at=NOW, event_end_at=NOW + timedelta(hours=1)
    )
    all_day = await _created_task(
        db_session,
        actor,
        event_start_date=DUE_DAY,
        event_end_date_exclusive=DUE_DAY + timedelta(days=1),
    )

    assert timed.event_start_at == NOW and timed.event_start_date is None
    assert all_day.event_start_date == DUE_DAY and all_day.event_start_at is None


@pytest.mark.parametrize(
    "overrides",
    [
        # both pairs at once
        {
            "event_start_at": NOW,
            "event_end_at": NOW + timedelta(hours=1),
            "event_start_date": DUE_DAY,
            "event_end_date_exclusive": DUE_DAY + timedelta(days=1),
        },
        # half an instant pair
        {"event_start_at": NOW},
        {"event_end_at": NOW + timedelta(hours=1)},
        # instant end is not after the start
        {"event_start_at": NOW, "event_end_at": NOW},
        {"event_start_at": NOW, "event_end_at": NOW - timedelta(minutes=1)},
        # half an all-day pair
        {"event_start_date": DUE_DAY},
        # all-day end is not after the start (exclusive end must be later)
        {"event_start_date": DUE_DAY, "event_end_date_exclusive": DUE_DAY},
        {
            "event_start_date": DUE_DAY,
            "event_end_date_exclusive": DUE_DAY - timedelta(days=1),
        },
    ],
)
async def test_an_incoherent_calendar_block_is_rejected(
    db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(db_session, _task(actor, **overrides))


# --- Versions --------------------------------------------------------------------------


@pytest.mark.parametrize("overrides", [{"version": 0}, {"due_version": 0}])
async def test_versions_are_positive(
    db_session: AsyncSession, overrides: dict[str, Any]
) -> None:
    actor = await make_actor(db_session)
    await _expect_integrity_error(db_session, _task(actor, **overrides))


async def test_a_new_task_starts_at_version_one(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await _created_task(db_session, actor)
    assert (task.version, task.due_version) == (1, 1)


# --- Tenant-aware references -----------------------------------------------------------


async def test_a_task_cannot_reference_another_users_audio_or_subject(
    db_session: AsyncSession,
) -> None:
    alice, bob = await make_actor(db_session), await make_actor(db_session)
    audio = await make_audio(db_session, alice)
    subject = await make_subject(db_session, alice)

    await _expect_integrity_error(db_session, _task(bob, audio_id=audio.id))
    await _expect_integrity_error(db_session, _task(bob, subject_id=subject.id))


async def test_a_task_source_pins_task_class_transcript_and_segment(
    db_session: AsyncSession,
) -> None:
    alice, bob = await make_actor(db_session), await make_actor(db_session)
    task = await make_task(db_session, alice)
    transcript = await make_transcript(db_session, alice)
    segment = Segment(
        user_id=alice.user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=transcript.text,
        char_start=0,
        char_end=transcript.char_count,
    )
    db_session.add(segment)
    await db_session.flush()

    source = TaskSource(
        user_id=alice.user_id,
        task_id=task.id,
        audio_id=transcript.audio_id,
        transcript_id=transcript.id,
        transcript_version=transcript.version,
        segment_id=segment.id,
        char_start=0,
        char_end=5,
    )
    db_session.add(source)
    await db_session.flush()
    assert source.task_id == task.id

    # A foreign tenant cannot attach a source to Alice's task.
    await _expect_integrity_error(
        db_session,
        TaskSource(
            user_id=bob.user_id,
            task_id=task.id,
            audio_id=transcript.audio_id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            segment_id=segment.id,
            char_start=0,
            char_end=5,
        ),
    )
    # An empty span is not evidence.
    await _expect_integrity_error(
        db_session,
        TaskSource(
            user_id=alice.user_id,
            task_id=task.id,
            audio_id=transcript.audio_id,
            transcript_id=transcript.id,
            transcript_version=transcript.version,
            segment_id=segment.id,
            char_start=5,
            char_end=5,
        ),
    )


async def test_a_manual_task_has_zero_sources(db_session: AsyncSession) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM task_sources WHERE task_id = :task"), {"task": task.id}
        )
    ) == 0


# --- Pending revisions -----------------------------------------------------------------


def _revision(actor: Actor, task: Task, **overrides: Any) -> TaskRevision:
    values: dict[str, Any] = {
        "user_id": actor.user_id,
        "task_id": task.id,
        "base_task_version": task.version,
        "revision_type": "reanalysis",
        "proposed_fields": {"titulo": "Examen parcial — temas 1 a 4"},
    }
    values.update(overrides)
    return TaskRevision(**values)


async def test_a_revision_starts_pending_with_its_proposed_fields(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    revision = _revision(actor, task)
    db_session.add(revision)
    await db_session.flush()

    assert revision.status == "pending_confirmation"
    assert revision.proposed_fields == {"titulo": "Examen parcial — temas 1 a 4"}
    assert revision.activated_at is None
    # The active task was not mutated by the proposal.
    stored = await db_session.get(Task, task.id)
    assert stored is not None
    assert stored.titulo == task.titulo


async def test_a_revision_requires_proposed_fields_and_a_positive_base_version(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)

    await _expect_integrity_error(
        db_session, _revision(actor, task, proposed_fields=None)
    )
    await _expect_integrity_error(db_session, _revision(actor, task, base_task_version=0))


@pytest.mark.parametrize("status", ["activated", "discarded", "pending_confirmation"])
async def test_promotion_time_is_coherent_with_the_revision_state(
    db_session: AsyncSession, status: str
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    # Activated with a time is valid; every other state must not claim a promotion.
    row = _revision(actor, task, status=status, activated_at=NOW if status == "activated" else None)
    db_session.add(row)
    await db_session.flush()
    assert row.status == status

    await _expect_integrity_error(
        db_session,
        _revision(
            actor,
            task,
            status=status,
            activated_at=None if status == "activated" else NOW,
        ),
    )


async def test_a_revision_cannot_belong_to_another_users_task(
    db_session: AsyncSession,
) -> None:
    alice, bob = await make_actor(db_session), await make_actor(db_session)
    task = await make_task(db_session, alice)
    await _expect_integrity_error(db_session, _revision(bob, task))


async def test_a_repeated_reanalysis_run_cannot_duplicate_the_revision(
    db_session: AsyncSession,
) -> None:
    actor = await make_actor(db_session)
    task = await make_task(db_session, actor)
    run = await make_processing_run(db_session, actor)

    db_session.add(_revision(actor, task, run_id=run.id))
    await db_session.flush()
    await _expect_integrity_error(db_session, _revision(actor, task, run_id=run.id))

    # `edit_of_confirmed` has no run: two successive edits at the same base version are
    # legitimate pending proposals (PostgreSQL's default NULL-distinct unique semantics).
    db_session.add(_revision(actor, task, revision_type="edit_of_confirmed"))
    await db_session.flush()
    db_session.add(_revision(actor, task, revision_type="edit_of_confirmed"))
    await db_session.flush()


# --- Inbox indexes ---------------------------------------------------------------------


async def test_the_inbox_indexes_are_partial_over_one_due_representation_each(
    db_session: AsyncSession,
) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'tasks' "
                "AND indexname LIKE 'tasks_inbox_%'"
            )
        )
    ).all()
    definitions = {str(name): str(definition) for name, definition in rows}

    assert set(definitions) == {"tasks_inbox_due_at", "tasks_inbox_due_date"}
    assert "(user_id, status, due_at) WHERE (due_at IS NOT NULL)" in definitions[
        "tasks_inbox_due_at"
    ]
    assert "(user_id, status, due_date) WHERE (due_date IS NOT NULL)" in definitions[
        "tasks_inbox_due_date"
    ]
    for definition in definitions.values():
        assert "COALESCE" not in definition


async def test_the_calendar_etag_column_is_nullable(db_session: AsyncSession) -> None:
    nullable = await db_session.scalar(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'tasks' "
            "AND column_name = 'calendar_etag'"
        )
    )
    assert nullable == "YES"
