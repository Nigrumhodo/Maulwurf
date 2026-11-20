"""J3.4 / J4.1: allow `maulwurf_jobs` tenant-bound access to `google_credentials`.

In S2 (`0005`), no worker job needed OAuth tokens yet, so `google_credentials` only carried
`tenant_isolation` for `maulwurf_app`. S3 (`sync_task_event`) and S4 (`deliver_notification`)
run under `maulwurf_jobs` and bind `app.user_id` for one claimed owner before reading or
refreshing that user's token.

This migration adds ONLY `jobs_tenant`
(`user_id = NULLIF(current_setting('app.user_id', true), '')::uuid`)
for `maulwurf_jobs` on `google_credentials` — deliberately WITHOUT `jobs_cross_tenant`, so a
worker can never scan credentials across tenants and must bind the single owner explicitly.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOBS_ROLE = "maulwurf_jobs"
SAFE_USER_SETTING = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE POLICY jobs_tenant ON "google_credentials"
          FOR ALL TO "{JOBS_ROLE}"
          USING (user_id = {SAFE_USER_SETTING})
          WITH CHECK (user_id = {SAFE_USER_SETTING})
        """
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "jobs_tenant" ON "google_credentials"')
