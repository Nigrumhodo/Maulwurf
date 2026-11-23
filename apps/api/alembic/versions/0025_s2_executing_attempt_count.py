"""Count only executing ingestion attempts toward the global capacity cap.

Idle `awaiting_upload` reservations hold no tmpfs or ASR resources and must not block other
users from reserving or starting a transcription. This revision updates
`public.count_active_ingestion_attempts()` so the global slot counter tracks only attempts
that have started reception/transcription (`receiving`, `transcribing`,
`transcript_committed_cleanup_pending`).

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "maulwurf_app"
FUNCTION = "public.count_active_ingestion_attempts()"
# Frozen copy of `app.models.ingestion.EXECUTING_INGESTION_STATES` at this revision;
# a unit test fails if the two drift apart.
EXECUTING_STATES = (
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
)
_PREVIOUS_ACTIVE_STATES = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
)
_EXECUTING_SQL = ", ".join(f"'{state}'" for state in EXECUTING_STATES)
_PREVIOUS_SQL = ", ".join(f"'{state}'" for state in _PREVIOUS_ACTIVE_STATES)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}
        RETURNS integer
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET app.capacity_count = 'on'
        AS $$
          SELECT count(*)::integer
          FROM public.ingestion_attempts
          WHERE status IN ({_EXECUTING_SQL})
        $$
        """  # noqa: S608 - fixed identifiers and the frozen state literals above
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION} TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}
        RETURNS integer
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET app.capacity_count = 'on'
        AS $$
          SELECT count(*)::integer
          FROM public.ingestion_attempts
          WHERE status IN ({_PREVIOUS_SQL})
        $$
        """  # noqa: S608 - fixed identifiers and the frozen state literals above
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION} TO "{APP_ROLE}"')
