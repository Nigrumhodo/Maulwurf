"""U-S4-AN-02 (A4.1): the persistent dedupe identity of M8 notifications.

The identity is `(user_id, type, task_id, due_version, occurrence)` (DISENO §2.9). This
module proves the canonical builder used by the scheduler, and that the database carries
both uniqueness guarantees: the rendered `dedupe_key` is UNIQUE and the natural tuple is
`UNIQUE NULLS NOT DISTINCT`, so duplicated occurrences are impossible by construction even
if a caller formats the text differently. The real constraint behaviour is exercised against
PostgreSQL by `tests/integration/test_s4_notifications_schema.py`.
"""

import uuid
from datetime import date
from typing import cast

import pytest
from sqlalchemy.schema import Table, UniqueConstraint

from app.models.notification import Notification
from app.services.notifications import dedupe_identity, dedupe_key

NOTIFICATIONS = cast(Table, Notification.__table__)
USER = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER = uuid.UUID("22222222-2222-4222-8222-222222222222")
TASK = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTHER_TASK = uuid.UUID("44444444-4444-4444-8444-444444444444")
OCCURRENCE = date(2026, 11, 9)


def _key(**overrides: object) -> str:
    values: dict[str, object] = {
        "user_id": USER,
        "type": "reminder_t24",
        "task_id": TASK,
        "due_version": 1,
        "occurrence": OCCURRENCE,
    }
    values.update(overrides)
    return dedupe_key(**values)  # type: ignore[arg-type]


# --- Canonical identity ---------------------------------------------------------------


def test_the_key_is_deterministic_and_uses_the_frozen_components() -> None:
    assert _key() == _key()
    assert _key() == (
        f"{USER}|reminder_t24|{TASK}|1|2026-11-09"
    )
    assert dedupe_identity(
        user_id=USER,
        type="reminder_t24",
        task_id=TASK,
        due_version=1,
        occurrence=OCCURRENCE,
    ) == (str(USER), "reminder_t24", str(TASK), 1, OCCURRENCE)


@pytest.mark.parametrize(
    "override",
    [
        {"user_id": OTHER_USER},
        {"type": "reminder_t48"},
        {"task_id": OTHER_TASK},
        {"due_version": 2},
        {"occurrence": date(2026, 11, 10)},
    ],
)
def test_every_identity_component_changes_the_key(override: dict[str, object]) -> None:
    assert _key(**override) != _key()


def test_two_tenants_with_the_same_task_occurrence_never_collide() -> None:
    # Same task id and date, different owner: the key still differs because `user_id` is
    # the first component. The DB FK pins the task to the same tenant in real rows.
    assert _key() != _key(user_id=OTHER_USER)


def test_null_components_render_unambiguously() -> None:
    daily = _key(type="digest_daily", task_id=None, due_version=None)
    weekly = _key(type="digest_weekly", task_id=None, due_version=None)
    assert daily == f"{USER}|digest_daily|-|-|2026-11-09"
    assert weekly != daily
    # '-' cannot be a UUID or a positive version, so the rendering cannot alias a real row.
    assert "||" not in daily


def test_the_key_never_contains_whitespace_or_control_characters() -> None:
    value = _key()
    assert value == value.strip()
    assert not any(character.isspace() for character in value)


# --- Component rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"task_id": None},
        {"due_version": None},
        {"occurrence": None},
        {"due_version": 0},
    ],
)
def test_a_reminder_requires_its_task_generation_and_local_date(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _key(**overrides)


@pytest.mark.parametrize("type", ["digest_daily", "digest_weekly", "test"])
def test_task_less_occurrences_reject_task_components(type: str) -> None:
    with pytest.raises(ValueError):
        _key(type=type, occurrence=OCCURRENCE)
    with pytest.raises(ValueError):
        _key(type=type, task_id=None, due_version=1, occurrence=OCCURRENCE)


@pytest.mark.parametrize("type", ["digest_daily", "digest_weekly", "test"])
def test_task_less_occurrences_still_need_their_local_date(type: str) -> None:
    # `UNIQUE NULLS NOT DISTINCT` would otherwise collapse every audited digest/test of the
    # user into a single nullable row; each occurrence carries its local date.
    with pytest.raises(ValueError):
        _key(type=type, task_id=None, due_version=None, occurrence=None)
    assert _key(type=type, task_id=None, due_version=None).endswith("|2026-11-09")


def test_an_unknown_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        _key(type="reminder_t12")


# --- Database uniqueness --------------------------------------------------------------


def _unique_constraints(table: Table) -> dict[tuple[str, ...], UniqueConstraint]:
    return {
        tuple(column.name for column in constraint.columns): constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_the_persisted_dedupe_key_has_its_own_unique_constraint() -> None:
    unique = _unique_constraints(NOTIFICATIONS)
    assert unique[("dedupe_key",)].name == "uq_notifications_dedupe_key"
    assert NOTIFICATIONS.c.dedupe_key.nullable is False


def test_the_natural_occurrence_key_is_unique_with_nulls_not_distinct() -> None:
    unique = _unique_constraints(NOTIFICATIONS)
    natural = unique[("user_id", "type", "task_id", "due_version", "occurrence")]
    assert natural.name == "uq_notifications_occurrence"
    assert natural.dialect_options["postgresql"]["nulls_not_distinct"] is True
    # The natural key mirrors the builder components in this exact order.
    assert [column.name for column in natural.columns] == [
        "user_id",
        "type",
        "task_id",
        "due_version",
        "occurrence",
    ]


def test_a_duplicate_key_and_a_duplicate_tuple_are_both_rejected_by_the_database() -> None:
    # Documented as a schema contract here; the integration suite proves PostgreSQL raises.
    unique = _unique_constraints(NOTIFICATIONS)
    assert ("dedupe_key",) in unique
    assert (
        "user_id",
        "type",
        "task_id",
        "due_version",
        "occurrence",
    ) in unique


def test_the_notification_has_no_second_settings_table() -> None:
    from app.models import Base

    assert "settings" in Base.metadata.tables
    assert "notification_settings" not in Base.metadata.tables
