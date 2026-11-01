"""U-S3-AN-01 (A3.1): the M4 `tasks` schema contract, without PostgreSQL.

The ticket freezes one coherent table set for the S3 task workflow. These checks read the
SQLAlchemy metadata, so they catch the cheap regressions (a status vocabulary gaining
`scheduled`, an inbox index mixing `date` and `timestamptz`, a missing tenant half in a
foreign key) in `python-unit`. The same constraints are exercised against the real database
by `tests/integration/test_s3_tasks_schema.py`; that case is the proof that PostgreSQL
rejects the invalid rows.

The vocabularies are not redefined here: the task types and the date states must equal the
frozen §M4 contract of L3.2 (`app/schemas/analysis.py`).
"""

import re
from typing import Any, cast, get_args

import pytest
from sqlalchemy import DateTime
from sqlalchemy.schema import CheckConstraint, Table, UniqueConstraint

from app.models.task import (
    ACTIVITY_TYPES,
    CALENDAR_SYNC_STATUSES,
    DATE_STATUSES,
    REVISION_STATUSES,
    REVISION_TYPES,
    TASK_ORIGINS,
    TASK_STATUSES,
    Task,
    TaskRevision,
    TaskSource,
)
from app.schemas.analysis import ActivityType, DateStatus

TASKS = cast(Table, Task.__table__)
SOURCES = cast(Table, TaskSource.__table__)
REVISIONS = cast(Table, TaskRevision.__table__)


def _check(table: Table, name: str) -> str:
    """The SQL text of one model CHECK, normalized to single spaces."""
    expected = f"ck_{table.name}_{name}"
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == expected:
            return " ".join(str(constraint.sqltext).split())
    raise AssertionError(f"CHECK {expected!r} not found on {table.name}")


def _vocabulary(table: Table, name: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", _check(table, name)))


def _partial_indexes(table: Table) -> dict[str, tuple[tuple[str, ...], str]]:
    found: dict[str, tuple[tuple[str, ...], str]] = {}
    for index in table.indexes:
        where = index.dialect_options["postgresql"].get("where")
        if where is None:
            continue
        found[str(index.name)] = (
            tuple(column.name for column in index.columns),
            " ".join(str(where).split()),
        )
    return found


def _index(table: Table, name: str) -> tuple[tuple[str, ...], str | None]:
    for index in table.indexes:
        if index.name == name:
            where = index.dialect_options["postgresql"].get("where")
            return (
                tuple(column.name for column in index.columns),
                None if where is None else " ".join(str(where).split()),
            )
    raise AssertionError(f"index {name!r} not found on {table.name}")


# --- Task lifecycle and Calendar delivery are separate ---------------------------------


def test_task_status_vocabulary_is_closed_and_never_scheduled() -> None:
    assert _vocabulary(TASKS, "status") == set(TASK_STATUSES)
    assert "scheduled" not in TASK_STATUSES
    assert set(TASK_STATUSES) == {
        "pending",
        "needs_review",
        "confirmed",
        "dismissed",
        "completed",
    }


def test_calendar_sync_status_is_a_separate_column_with_its_own_vocabulary() -> None:
    assert _vocabulary(TASKS, "calendar_sync_status") == set(CALENDAR_SYNC_STATUSES)
    assert set(CALENDAR_SYNC_STATUSES) == {
        "not_requested",
        "pending",
        "synced",
        "failed",
        "blocked",
    }
    # Syncing is a remote fact, never a task lifecycle state: `synced` only exists on the
    # sync column, and both vocabularies are checked separately.
    assert "synced" not in TASK_STATUSES
    assert TASKS.c.status is not TASKS.c.calendar_sync_status


def test_task_origins_and_types_match_the_frozen_m4_contract() -> None:
    assert _vocabulary(TASKS, "source") == set(TASK_ORIGINS) == {
        "extraction",
        "chat",
        "manual",
    }
    assert _vocabulary(TASKS, "tipo") == set(ACTIVITY_TYPES) == set(get_args(ActivityType))


def test_date_status_matches_the_frozen_m4_contract() -> None:
    assert _vocabulary(TASKS, "date_status") == set(DATE_STATUSES) == {
        state.value for state in DateStatus
    }


# --- Exclusive due date / instant ------------------------------------------------------


def test_resolved_dates_are_exclusive_and_unresolved_states_carry_no_date() -> None:
    sql = _check(TASKS, "date_contract")
    # resolved + all-day -> due_date only; resolved + timed -> due_at only.
    assert "date_status = 'resolved' AND all_day" in sql
    assert "due_date IS NOT NULL AND due_at IS NULL" in sql
    assert "date_status = 'resolved' AND NOT all_day" in sql
    assert "due_at IS NOT NULL AND due_date IS NULL" in sql
    # ambiguous/missing never carry an invented date.
    assert "date_status IN ('ambiguous', 'missing')" in sql
    assert "due_date IS NULL AND due_at IS NULL" in sql


def test_confidence_is_optional_and_bounded_to_the_unit_interval() -> None:
    assert _check(TASKS, "confidence_range") == (
        "confidence_score >= 0 AND confidence_score <= 1"
    )
    assert TASKS.c.confidence_score.nullable is True


def test_task_and_due_versions_start_at_one() -> None:
    assert _check(TASKS, "version") == "version >= 1"
    assert _check(TASKS, "due_version") == "due_version >= 1"
    assert cast(Any, TASKS.c.version.server_default).arg == "1"
    assert cast(Any, TASKS.c.due_version.server_default).arg == "1"


# --- Calendar block --------------------------------------------------------------------


def test_calendar_block_is_empty_or_one_pair_with_a_later_end() -> None:
    sql = _check(TASKS, "calendar_block")
    assert (
        "event_start_at IS NULL AND event_end_at IS NULL "
        "AND event_start_date IS NULL AND event_end_date_exclusive IS NULL"
    ) in sql
    assert "event_start_at IS NOT NULL AND event_end_at IS NOT NULL" in sql
    assert "event_start_date IS NOT NULL AND event_end_date_exclusive IS NOT NULL" in sql
    assert "event_start_at < event_end_at" in sql
    assert "event_start_date < event_end_date_exclusive" in sql
    # The instant and all-day pairs are mutually exclusive.
    assert sql.count("event_start_at IS NOT NULL AND event_end_at IS NOT NULL") == 1
    assert sql.count("event_start_date IS NOT NULL AND event_end_date_exclusive IS NOT NULL") == 1


def test_calendar_etag_is_the_nullable_write_baseline_from_j36() -> None:
    assert TASKS.c.calendar_etag.nullable is True
    assert TASKS.c.calendar_id.nullable is True
    assert TASKS.c.calendar_event_id.nullable is True


# --- Inbox indexes ---------------------------------------------------------------------


def test_inbox_indexes_use_one_due_representation_each() -> None:
    assert _partial_indexes(TASKS) == {
        "tasks_inbox_due_at": (
            ("user_id", "status", "due_at"),
            "due_at IS NOT NULL",
        ),
        "tasks_inbox_due_date": (
            ("user_id", "status", "due_date"),
            "due_date IS NOT NULL",
        ),
    }
    # A `COALESCE(date::timestamptz)` expression would depend on the session timezone and
    # would not use the `date` index at all.
    for _columns, where in _partial_indexes(TASKS).values():
        assert "COALESCE" not in where.upper()
        assert "::" not in where


# --- Tenant-aware foreign keys ----------------------------------------------------------


def _foreign_keys(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.column_keys)
        for constraint in table.foreign_key_constraints
    }


def test_tasks_pin_audio_and_subject_to_the_same_tenant() -> None:
    assert ("user_id", "audio_id") in _foreign_keys(TASKS)
    assert ("user_id", "subject_id") in _foreign_keys(TASKS)
    # Nullable: a manual/chat proposal has no class.
    assert TASKS.c.audio_id.nullable is True
    assert TASKS.c.subject_id.nullable is True


def test_task_sources_pin_task_audio_transcript_and_segment_to_the_tenant() -> None:
    assert _foreign_keys(SOURCES) == {
        ("user_id", "task_id"),
        ("user_id", "audio_id"),
        ("user_id", "transcript_id"),
        ("user_id", "segment_id"),
    }
    assert _check(SOURCES, "char_range") == "char_start >= 0 AND char_start < char_end"
    # The natural key allows one row per span and class; a manual task has no rows.
    assert [column.name for column in SOURCES.primary_key.columns] == [
        "user_id",
        "task_id",
        "segment_id",
        "char_start",
        "char_end",
    ]


def test_task_revisions_pin_the_task_and_the_run_to_the_tenant() -> None:
    assert _foreign_keys(REVISIONS) == {
        ("user_id", "task_id"),
        ("user_id", "run_id"),
        ("user_id",),
    }


# --- Pending revision instead of mutating a confirmed task -----------------------------


def test_revisions_keep_proposed_fields_and_pending_confirmation() -> None:
    assert REVISIONS.c.proposed_fields.nullable is False
    assert REVISIONS.c.proposed_fields.type.__class__.__name__ == "JSONB"
    assert _check(REVISIONS, "proposed_fields") == "jsonb_typeof(proposed_fields) = 'object'"
    assert cast(Any, REVISIONS.c.status.server_default).arg == "pending_confirmation"
    assert _vocabulary(REVISIONS, "status") == set(REVISION_STATUSES) == {
        "pending_confirmation",
        "activated",
        "discarded",
    }
    assert _vocabulary(REVISIONS, "revision_type") == set(REVISION_TYPES) == {
        "reanalysis",
        "edit_of_confirmed",
    }


def test_revision_base_version_is_a_positive_task_version() -> None:
    assert _check(REVISIONS, "base_task_version") == "base_task_version >= 1"
    assert REVISIONS.c.base_task_version.nullable is False


def test_an_activated_revision_records_its_promotion_time() -> None:
    assert _check(REVISIONS, "activated_at") == (
        "(status = 'activated') = (activated_at IS NOT NULL)"
    )


def test_revisions_are_unique_per_base_version_type_and_run() -> None:
    unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in REVISIONS.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "task_id",
        "base_task_version",
        "revision_type",
        "run_id",
    ) in unique_keys


@pytest.mark.parametrize(
    ("table", "name"),
    [("tasks", TASKS), ("task_sources", SOURCES), ("task_revisions", REVISIONS)],
)
def test_every_task_table_is_tenant_owned(table: str, name: Table) -> None:
    assert "user_id" in name.c
    assert name.c.user_id.nullable is False
    # Each row is reachable only through a tenant-pinned parent (task, class, transcript
    # or run), so a row can never cross tenants.
    assert any(
        len(constraint.column_keys) > 1 and constraint.column_keys[0] == "user_id"
        for constraint in name.foreign_key_constraints
    )


def test_tasks_expose_a_tenant_unique_key_for_composite_references() -> None:
    assert _index(TASKS, "tasks_inbox_due_at")[1] == "due_at IS NOT NULL"
    unique_keys: set[tuple[str, ...]] = {
        tuple(constraint.columns.keys())
        for constraint in TASKS.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("user_id", "id") in unique_keys


def test_task_sources_do_not_duplicate_the_evidence_contract() -> None:
    # The literal quote + task_version live in A3.2's `task_evidence`; this table only
    # persists provenance, so it must not grow a second evidence shape.
    names: set[str] = set(TASKS.c.keys()) | set(SOURCES.c.keys()) | set(REVISIONS.c.keys())
    assert "quote" not in names
    assert "task_version" not in SOURCES.c
    assert "task_version" not in REVISIONS.c


def test_column_types_keep_instants_aware_and_days_as_dates() -> None:
    for column in ("due_at", "event_start_at", "event_end_at"):
        assert cast(DateTime, TASKS.c[column].type).timezone is True
    for column in ("due_date", "event_start_date", "event_end_date_exclusive"):
        assert TASKS.c[column].type.__class__.__name__ == "Date"
    assert TASKS.c.timezone.nullable is False


def test_no_task_column_stores_audio_or_transcript_payloads() -> None:
    forbidden = {"audio", "audio_bytes", "storage_key", "transcript_text", "text"}
    for table in (TASKS, SOURCES, REVISIONS):
        assert forbidden.isdisjoint(table.c.keys())
