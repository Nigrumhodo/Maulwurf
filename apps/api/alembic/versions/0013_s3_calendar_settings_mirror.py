"""A3.8: complete the Calendar `settings` row and the `calendar_events` mirror.

J3.3's `0006_s3_calendar_settings` created the per-user row, J3.7's
`0007_s3_calendar_mirror` added its coverage columns and the mirror, and
`0008_s3_calendar_manual_sync` added the manual-refresh stamp while A3.1 was absent. A3.1's
`tasks` now exists, so this revision closes the A3.8 contract instead of provisioning a
second table:

- `calendar_events (user_id, task_id)` references `tasks (user_id, id)`. A link can never
  point at another tenant's task. `ON DELETE SET NULL (task_id)` keeps the remote event
  mirrored when a class purge deletes its task; only the link is dropped, the row and its
  ETag/freshness survive.
- `calendar_events_by_task (user_id, task_id)` is the non-partial index the composite FK
  needs on the referencing side (I-S2-AN-17).
- `settings` rejects blank write/read/coverage calendar ids and NULL array elements. Coverage
  is either empty or a complete window: `from < to`, at least one calendar and
  `last_synced_at` present. A partially written publication can no longer be presented as
  covered.
- `calendar_events` rejects blank identity, `remote_status` and ETag values. An empty ETag
  is an unknown baseline, not a conditional-write token.

No writer had linked the mirror before this revision, but the cleanup statements make the
migration safe against direct-SQL leftovers: orphan task links are unlinked, blank ids and
array holes are normalized, and an inconsistent coverage window is reset to "never synced"
instead of being trusted. The downgrade removes the FK, index and CHECKs; it cannot restore
an orphan link that the upgrade cleared. Gmail/digest columns stay out of scope (S4/A4.1).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_calendar_events_user_id_task_id_tasks"
INDEX_NAME = "calendar_events_by_task"

SETTINGS_CHECKS = (
    "ck_settings_write_calendar_not_blank",
    "ck_settings_read_calendars_no_null",
    "ck_settings_read_calendars_no_blank",
    "ck_settings_coverage_calendars_no_null",
    "ck_settings_coverage_calendars_no_blank",
    "ck_settings_coverage_window",
)

EVENT_CHECKS = (
    "ck_calendar_events_calendar_id_not_blank",
    "ck_calendar_events_event_id_not_blank",
    "ck_calendar_events_remote_status_not_blank",
    "ck_calendar_events_etag_not_blank",
)


def _normalize_legacy_rows() -> None:
    """Make existing rows satisfy the new CHECKs without inventing coverage."""
    op.execute(
        """
        UPDATE calendar_events
        SET task_id = NULL, updated_at = now()
        WHERE task_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM tasks
              WHERE tasks.user_id = calendar_events.user_id
                AND tasks.id = calendar_events.task_id
          )
        """
    )
    op.execute("UPDATE calendar_events SET etag = NULL WHERE etag = ''")
    op.execute(
        """
        UPDATE settings
        SET calendar_write_calendar_id = NULL
        WHERE calendar_write_calendar_id IS NOT NULL
          AND btrim(calendar_write_calendar_id) = ''
        """
    )
    op.execute(
        """
        UPDATE settings
        SET calendar_read_calendar_ids =
                array_remove(array_remove(calendar_read_calendar_ids, NULL), ''),
            calendar_coverage_calendar_ids =
                array_remove(array_remove(calendar_coverage_calendar_ids, NULL), '')
        WHERE array_position(calendar_read_calendar_ids, NULL) IS NOT NULL
           OR array_position(calendar_read_calendar_ids, '') IS NOT NULL
           OR array_position(calendar_coverage_calendar_ids, NULL) IS NOT NULL
           OR array_position(calendar_coverage_calendar_ids, '') IS NOT NULL
        """
    )
    # An incomplete or inverted coverage window cannot be trusted: reporting it as covered
    # would present a partial mirror as complete. Reset it to "never synced".
    op.execute(
        """
        UPDATE settings
        SET calendar_coverage_from = NULL,
            calendar_coverage_to = NULL,
            calendar_coverage_calendar_ids = '{}',
            calendar_last_synced_at = NULL
        WHERE NOT (
            (calendar_coverage_from IS NULL
             AND calendar_coverage_to IS NULL
             AND cardinality(calendar_coverage_calendar_ids) = 0
             AND calendar_last_synced_at IS NULL)
            OR (calendar_coverage_from IS NOT NULL
                AND calendar_coverage_to IS NOT NULL
                AND calendar_coverage_from < calendar_coverage_to
                AND cardinality(calendar_coverage_calendar_ids) > 0
                AND calendar_last_synced_at IS NOT NULL)
        )
        """
    )


def _add_settings_checks() -> None:
    op.create_check_constraint(
        "write_calendar_not_blank",
        "settings",
        "calendar_write_calendar_id IS NULL "
        "OR btrim(calendar_write_calendar_id) <> ''",
    )
    op.create_check_constraint(
        "read_calendars_no_null",
        "settings",
        "array_position(calendar_read_calendar_ids, NULL) IS NULL",
    )
    op.create_check_constraint(
        "read_calendars_no_blank",
        "settings",
        "array_position(calendar_read_calendar_ids, '') IS NULL",
    )
    op.create_check_constraint(
        "coverage_calendars_no_null",
        "settings",
        "array_position(calendar_coverage_calendar_ids, NULL) IS NULL",
    )
    op.create_check_constraint(
        "coverage_calendars_no_blank",
        "settings",
        "array_position(calendar_coverage_calendar_ids, '') IS NULL",
    )
    op.create_check_constraint(
        "coverage_window",
        "settings",
        "(calendar_coverage_from IS NULL AND calendar_coverage_to IS NULL "
        "AND cardinality(calendar_coverage_calendar_ids) = 0 "
        "AND calendar_last_synced_at IS NULL) "
        "OR (calendar_coverage_from IS NOT NULL AND calendar_coverage_to IS NOT NULL "
        "AND calendar_coverage_from < calendar_coverage_to "
        "AND cardinality(calendar_coverage_calendar_ids) > 0 "
        "AND calendar_last_synced_at IS NOT NULL)",
    )


def _add_event_checks() -> None:
    op.create_check_constraint(
        "calendar_id_not_blank", "calendar_events", "calendar_id <> ''"
    )
    op.create_check_constraint("event_id_not_blank", "calendar_events", "event_id <> ''")
    op.create_check_constraint(
        "remote_status_not_blank", "calendar_events", "remote_status <> ''"
    )
    op.create_check_constraint(
        "etag_not_blank", "calendar_events", "etag IS NULL OR etag <> ''"
    )


def upgrade() -> None:
    _normalize_legacy_rows()
    _add_settings_checks()
    _add_event_checks()
    op.create_index(INDEX_NAME, "calendar_events", ["user_id", "task_id"])
    op.create_foreign_key(
        FK_NAME,
        "calendar_events",
        "tasks",
        ["user_id", "task_id"],
        ["user_id", "id"],
        ondelete="SET NULL (task_id)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(FK_NAME), "calendar_events", type_="foreignkey")
    op.drop_index(INDEX_NAME, table_name="calendar_events")
    for name in EVENT_CHECKS:
        op.drop_constraint(op.f(name), "calendar_events", type_="check")
    for name in SETTINGS_CHECKS:
        op.drop_constraint(op.f(name), "settings", type_="check")
