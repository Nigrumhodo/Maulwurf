"""A2.14: cross-tenant count of active ingestion attempts for the global slot cap.

`reserve_slot` enforces `active_slots` for the whole instance, but the API runs as
`maulwurf_app`, whose `tenant_isolation` policy on `ingestion_attempts` (`0005`) only shows
the caller's rows: a plain `count(*)` turns the global cap into a per-user one.

This revision adds one narrow `SECURITY DEFINER` function that returns only the integer count
of active attempts, so the API never gains row access to other tenants. It is owned by the
migration role, pins `search_path`, and `EXECUTE` is granted to `maulwurf_app` alone.

`FORCE ROW LEVEL SECURITY` also applies to the owner, so a non-superuser, non-`BYPASSRLS`
migration owner (staging/production, RUNBOOK §5.1) would count zero rows. A `SELECT` policy
for the owner, gated by a setting that only the function sets for its own duration, keeps
the count correct there without letting ordinary owner sessions see tenant rows.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "maulwurf_app"
FUNCTION = "public.count_active_ingestion_attempts()"
POLICY = "capacity_count"
# Frozen copy of `app.models.ingestion.ACTIVE_INGESTION_STATES` at this revision; a unit
# test fails if the two drift apart.
ACTIVE_STATES = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
)
_STATES_SQL = ", ".join(f"'{state}'" for state in ACTIVE_STATES)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE POLICY {POLICY} ON public.ingestion_attempts
          FOR SELECT TO CURRENT_USER
          USING (NULLIF(current_setting('app.capacity_count', true), '') = 'on')
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {FUNCTION}
        RETURNS integer
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET app.capacity_count = 'on'
        AS $$
          SELECT count(*)::integer
          FROM public.ingestion_attempts
          WHERE status IN ({_STATES_SQL})
        $$
        """  # noqa: S608 - fixed identifiers and the frozen state literals above
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION} FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION {FUNCTION} TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON public.ingestion_attempts")
