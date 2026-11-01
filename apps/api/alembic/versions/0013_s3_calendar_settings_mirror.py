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

"Blank" means `btrim(value, E' \\t\\n\\r\\f\\v') = ''`: the empty string, spaces, tabs and
any other ASCII whitespace. Plain `btrim(value)` would leave a tab untouched, so a
`'\\t'` ETag could still become a bogus conditional-write baseline. The criterion lives in
two `IMMUTABLE` helpers created here:

- `public.maulwurf_text_is_blank(text)` for scalar columns;
- `public.maulwurf_text_array_has_blank(text[])` for array elements, because a CHECK cannot
  contain a subquery or `unnest()`.

Both the CHECKs and the legacy cleanup call those helpers, so they cannot drift apart.

Legacy rows are handled explicitly, with the same whitespace-aware criterion:

- An orphan task link is unlinked.
- A blank ETag becomes NULL (unknown baseline), a blank write calendar becomes NULL (not
  connected), and blank/NULL elements are removed from the read/coverage arrays.
- An incomplete or inverted coverage window is reset to "never synced"; a complete window
  keeps its timestamps.
- A blank `calendar_id`, `event_id` or `remote_status` is **rejected** with a message naming
  the offending counts. Those values identify the remote event and its observed status: the
  migration will not invent them, and a later `ADD CONSTRAINT` would only report an opaque
  violation.

The tables force RLS for the runtime roles, which also binds a non-superuser owner. The
migration temporarily drops only FORCE (the ACCESS EXCLUSIVE lock keeps the transaction
private) so the cleanup can see every tenant's row; it restores FORCE before any new CHECK
or FK is validated. There is deliberately no `try/finally`: PostgreSQL DDL is transactional,
so a failure anywhere below rolls back the whole revision (including `NO FORCE`) and restores
the original state. A `finally` would run inside the aborted transaction and hide the real
error with `InFailedSqlTransaction`.

The downgrade removes the FK, index, CHECKs and helpers; it cannot restore an orphan link
that the upgrade cleared. Gmail/digest columns stay out of scope (S4/A4.1).

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
BLANK_TEXT_FUNCTION = "maulwurf_text_is_blank"
BLANK_ARRAY_FUNCTION = "maulwurf_text_array_has_blank"

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


def _create_blank_helpers() -> None:
    """IMMUTABLE predicates shared by the CHECKs and the cleanup.

    A CHECK cannot contain a subquery or `unnest()`, but it can call an IMMUTABLE function.
    `btrim` needs an explicit character set: its default only strips spaces, and a tab-only
    ETag must not pass as a conditional-write baseline.
    """
    op.execute(
        """
        CREATE FUNCTION public.maulwurf_text_is_blank(value text)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT value IS NOT NULL
               AND btrim(value, E' \\t\\n\\r\\f\\v') = ''
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.maulwurf_text_array_has_blank(elements text[])
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM unnest(elements) AS element
                WHERE public.maulwurf_text_is_blank(element)
            )
        $$
        """
    )


def _normalize_legacy_rows() -> None:
    """Make existing rows satisfy the new CHECKs without inventing identity or coverage."""
    for table in ("calendar_events", "settings"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
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
    op.execute(
        """
        UPDATE calendar_events
        SET etag = NULL
        WHERE public.maulwurf_text_is_blank(etag)
        """
    )
    op.execute(
        """
        UPDATE settings
        SET calendar_write_calendar_id = NULL
        WHERE public.maulwurf_text_is_blank(calendar_write_calendar_id)
        """
    )
    op.execute(
        """
        UPDATE settings
        SET calendar_read_calendar_ids = ARRAY(
                SELECT element
                FROM unnest(calendar_read_calendar_ids) AS element
                WHERE element IS NOT NULL
                  AND NOT public.maulwurf_text_is_blank(element)
            ),
            calendar_coverage_calendar_ids = ARRAY(
                SELECT element
                FROM unnest(calendar_coverage_calendar_ids) AS element
                WHERE element IS NOT NULL
                  AND NOT public.maulwurf_text_is_blank(element)
            )
        WHERE EXISTS (
                SELECT 1
                FROM unnest(calendar_read_calendar_ids) AS element
                WHERE element IS NULL
                   OR public.maulwurf_text_is_blank(element)
            )
           OR EXISTS (
                SELECT 1
                FROM unnest(calendar_coverage_calendar_ids) AS element
                WHERE element IS NULL
                   OR public.maulwurf_text_is_blank(element)
            )
        """
    )
    # An incomplete or inverted coverage window cannot be trusted: reporting it as
    # covered would present a partial mirror as complete. Reset it to "never synced".
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
    # Blank identity/status has no defined normalization: refuse it with an actionable
    # message instead of failing later on a CHECK violation.
    op.execute(
        """
        DO $$
        DECLARE
            blank_calendar_ids bigint;
            blank_event_ids bigint;
            blank_statuses bigint;
        BEGIN
            SELECT count(*) FILTER (
                       WHERE public.maulwurf_text_is_blank(calendar_id)
                   ),
                   count(*) FILTER (
                       WHERE public.maulwurf_text_is_blank(event_id)
                   ),
                   count(*) FILTER (
                       WHERE public.maulwurf_text_is_blank(remote_status)
                   )
            INTO blank_calendar_ids, blank_event_ids, blank_statuses
            FROM calendar_events;

            IF blank_calendar_ids + blank_event_ids + blank_statuses > 0 THEN
                RAISE EXCEPTION
                    '0013 cannot add the calendar_events CHECKs: % row(s) have a blank '
                    'calendar_id, % have a blank event_id and % have a blank '
                    'remote_status; repair or delete them before upgrading',
                    blank_calendar_ids, blank_event_ids, blank_statuses
                    USING HINT = 'The migration will not invent a remote identity or status.';
            END IF;
        END
        $$;
        """
    )
    for table in ("calendar_events", "settings"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def _add_settings_checks() -> None:
    op.create_check_constraint(
        "write_calendar_not_blank",
        "settings",
        "calendar_write_calendar_id IS NULL "
        f"OR NOT public.{BLANK_TEXT_FUNCTION}(calendar_write_calendar_id)",
    )
    op.create_check_constraint(
        "read_calendars_no_null",
        "settings",
        "array_position(calendar_read_calendar_ids, NULL) IS NULL",
    )
    op.create_check_constraint(
        "read_calendars_no_blank",
        "settings",
        f"NOT public.{BLANK_ARRAY_FUNCTION}(calendar_read_calendar_ids)",
    )
    op.create_check_constraint(
        "coverage_calendars_no_null",
        "settings",
        "array_position(calendar_coverage_calendar_ids, NULL) IS NULL",
    )
    op.create_check_constraint(
        "coverage_calendars_no_blank",
        "settings",
        f"NOT public.{BLANK_ARRAY_FUNCTION}(calendar_coverage_calendar_ids)",
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
        "calendar_id_not_blank",
        "calendar_events",
        f"NOT public.{BLANK_TEXT_FUNCTION}(calendar_id)",
    )
    op.create_check_constraint(
        "event_id_not_blank",
        "calendar_events",
        f"NOT public.{BLANK_TEXT_FUNCTION}(event_id)",
    )
    op.create_check_constraint(
        "remote_status_not_blank",
        "calendar_events",
        f"NOT public.{BLANK_TEXT_FUNCTION}(remote_status)",
    )
    op.create_check_constraint(
        "etag_not_blank",
        "calendar_events",
        f"etag IS NULL OR NOT public.{BLANK_TEXT_FUNCTION}(etag)",
    )


def upgrade() -> None:
    _create_blank_helpers()
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
    op.execute(f"DROP FUNCTION public.{BLANK_ARRAY_FUNCTION}(text[])")
    op.execute(f"DROP FUNCTION public.{BLANK_TEXT_FUNCTION}(text)")
