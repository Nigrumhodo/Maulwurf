"""G3 review: the global slot count crosses tenants without widening `maulwurf_app` (0024).

`reserve_slot` used to count `ingestion_attempts` under the API role, whose tenant policy
hides other users' rows, so the global cap silently became per-user.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionAttempt
from app.models.ingestion import ACTIVE_INGESTION_STATES
from tests.integration.actors import make_actor, make_attempt

pytestmark = pytest.mark.integration

APP_ROLE = "maulwurf_app"
FUNCTION = "public.count_active_ingestion_attempts()"
COUNT_FUNCTION = select(func.public.count_active_ingestion_attempts())
TENANT_COUNT = (
    select(func.count())
    .select_from(IngestionAttempt)
    .where(IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES))
)


async def _as_app(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(text(f'SET LOCAL ROLE "{APP_ROLE}"'))
    await db.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)}
    )


async def test_app_role_counts_every_tenant_but_reads_only_its_own(
    db_session: AsyncSession,
) -> None:
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    await make_attempt(db_session, alice)
    await make_attempt(db_session, bob)
    await make_attempt(db_session, bob)
    instance_wide = await db_session.scalar(TENANT_COUNT)  # owner session: every row
    assert instance_wide is not None and instance_wide >= 3

    await _as_app(db_session, alice.user_id)

    assert await db_session.scalar(TENANT_COUNT) == 1  # RLS still hides bob's rows
    assert await db_session.scalar(COUNT_FUNCTION) == instance_wide


async def test_function_is_definer_pinned_and_executable_only_by_the_api(
    db_session: AsyncSession,
) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, "
                "has_function_privilege('maulwurf_app', p.oid, 'EXECUTE') AS app, "
                "has_function_privilege('maulwurf_jobs', p.oid, 'EXECUTE') AS jobs, "
                "has_function_privilege('maulwurf_ingest', p.oid, 'EXECUTE') AS ingest "
                "FROM pg_proc p WHERE p.oid = CAST(:fn AS regprocedure)"
            ),
            {"fn": FUNCTION},
        )
    ).one()
    assert row.prosecdef
    assert "search_path=pg_catalog, pg_temp" in row.proconfig
    assert "app.capacity_count=on" in row.proconfig
    assert (row.app, row.jobs, row.ingest) == (True, False, False)


async def test_non_superuser_owner_counts_through_the_gated_policy(
    db_session: AsyncSession,
) -> None:
    # Staging/production owners are neither superuser nor BYPASSRLS. Rebuild that shape inside
    # the rolled-back transaction: a plain owner sees nothing unless the function's setting
    # opens the capacity policy.
    owner = f"mw_g3_owner_{uuid.uuid4().hex[:8]}"
    alice = await make_actor(db_session)
    bob = await make_actor(db_session)
    await make_attempt(db_session, alice)
    await make_attempt(db_session, bob)
    instance_wide = await db_session.scalar(TENANT_COUNT)
    await db_session.execute(text(f'CREATE ROLE "{owner}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
    await db_session.execute(text(f'GRANT SELECT ON ingestion_attempts TO "{owner}"'))
    await db_session.execute(text(f'ALTER FUNCTION {FUNCTION} OWNER TO "{owner}"'))
    await db_session.execute(
        text(f'ALTER POLICY capacity_count ON ingestion_attempts TO "{owner}"')
    )

    await db_session.execute(text(f'SET LOCAL ROLE "{owner}"'))
    assert await db_session.scalar(TENANT_COUNT) == 0  # the gate stays closed by default
    await db_session.execute(text("RESET ROLE"))

    await _as_app(db_session, alice.user_id)
    assert await db_session.scalar(COUNT_FUNCTION) == instance_wide
