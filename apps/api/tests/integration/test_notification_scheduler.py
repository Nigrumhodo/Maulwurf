"""I-S4-JF-04 (J4.2): single scheduler role, atomic claim and the 15-minute target.

The suite runs against PostgreSQL 16 and the real migrations. It proves the runtime contract
the M8 scheduler needs, without Gmail:

- the role is elected through the `scheduler_leases` row; a live holder blocks a second
  scheduler, only an expired row can be taken over, and the fence grows on takeover;
- due `pending` occurrences move to `sending` exactly once, under a bounded delivery lease;
- two workers claim disjoint rows even when one holds its locks (`SKIP LOCKED`);
- an expired `sending` lease becomes `delivery_unknown` and never returns to `pending`;
- the claim is ordered and bounded, and the elapsed time from `scheduled_at` stays inside
  the 15-minute target for a normal minute tick;
- the scheduler runs as the cross-tenant `maulwurf_jobs` role (A2.17 RLS).

Delivery itself is J4.1: this suite stops at the claimed state the Gmail worker consumes.
No provider call, email, credential or real class audio is involved.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Notification, SchedulerLease, User
from app.services.notifications import dedupe_key
from app.services.scheduler_lease import (
    DEFAULT_LEASE_TTL,
    DELIVERY_LEASE,
    DELIVERY_SLA,
    SchedulerLeaseClaim,
    SchedulerReport,
    acquire_scheduler_lease,
    claim_due_notifications,
    run_notification_scheduler,
)
from tests.integration.actors import Actor, make_actor

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OCCURRENCE = date(2026, 11, 9)
JOBS_ROLE = "maulwurf_jobs"


def _role() -> str:
    """A unique role name per test, so integration tests never share an election row."""
    return f"notification_scheduler_test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def committed_db(
    migrated_database_url: str,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    """Real sessions with real commits, unlike the rollback-only `db_session` fixture."""
    engine = create_async_engine(migrated_database_url)
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_scheduler_leases(migrated_database_url: str) -> AsyncIterator[None]:
    """The election table is global; no test may inherit another test's live lease."""
    yield
    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM scheduler_leases"))
    finally:
        await engine.dispose()


@pytest.fixture
async def actor(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> AsyncIterator[Actor]:
    _engine, factory = committed_db
    async with factory() as db:
        created = await make_actor(db)
        await db.commit()
    try:
        yield created
    finally:
        async with factory() as db:
            await db.execute(delete(User).where(User.id == created.user_id))
            await db.commit()


def _notification(
    actor: Actor,
    *,
    scheduled_at: datetime = NOW,
    status: str = "pending",
    sequence: int = 0,
    occurrence: date = OCCURRENCE,
    attempts: int = 0,
    lease_expires_at: datetime | None = None,
) -> Notification:
    """A task-less audited occurrence; `sequence` keeps each row's identity unique."""
    return Notification(
        user_id=actor.user_id,
        task_id=None,
        type="test",
        occurrence=occurrence,
        due_version=None,
        scheduled_at=scheduled_at,
        dedupe_key=dedupe_key(
            user_id=actor.user_id,
            type="test",
            task_id=None,
            due_version=None,
            occurrence=occurrence,
            occurrence_seq=sequence,
        ),
        occurrence_seq=sequence,
        status=status,
        attempts=attempts,
        lease_expires_at=lease_expires_at,
        # The A4.1 CHECKs require a `sent` row to prove its delivery.
        sent_at=NOW if status == "sent" else None,
        provider_message_id=f"test-{sequence}" if status == "sent" else None,
    )


async def _store(
    factory: async_sessionmaker[AsyncSession], *rows: Notification
) -> list[uuid.UUID]:
    async with factory() as db:
        db.add_all(rows)
        await db.commit()
        return [row.id for row in rows]


async def _get(
    factory: async_sessionmaker[AsyncSession], *ids: uuid.UUID
) -> dict[uuid.UUID, Notification]:
    async with factory() as db:
        rows = (
            await db.scalars(select(Notification).where(Notification.id.in_(ids)))
        ).all()
        return {row.id: row for row in rows}


async def _run(
    factory: async_sessionmaker[AsyncSession],
    *,
    role: str,
    holder: str,
    now: datetime = NOW,
) -> SchedulerReport:
    async with factory() as db:
        report = await run_notification_scheduler(db, now=now, holder=holder, role=role)
        await db.commit()
        return report


# --- Election --------------------------------------------------------------------------


async def test_a_live_holder_blocks_a_second_scheduler_and_renews_in_place(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = committed_db
    role = _role()

    async with factory() as db:
        first = await acquire_scheduler_lease(
            db, holder="host-a", role=role, now=NOW
        )
        await db.commit()
    assert isinstance(first, SchedulerLeaseClaim)
    assert first.fencing_token == 1
    assert first.expires_at == NOW + DEFAULT_LEASE_TTL

    async with factory() as db:
        blocked = await acquire_scheduler_lease(
            db, holder="host-b", role=role, now=NOW + timedelta(seconds=1)
        )
        await db.commit()
    assert blocked is None

    async with factory() as db:
        renewed = await acquire_scheduler_lease(
            db, holder="host-a", role=role, now=NOW + timedelta(seconds=30)
        )
        await db.commit()
    assert isinstance(renewed, SchedulerLeaseClaim)
    assert renewed.fencing_token == 1
    assert renewed.expires_at == NOW + timedelta(seconds=30) + DEFAULT_LEASE_TTL


async def test_a_live_lease_stops_the_scheduler_tick_before_any_claim(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    actor: Actor,
) -> None:
    """The elected role is the only one that reaches the claim, not just the acquire."""
    _engine, factory = committed_db
    due = _notification(actor, scheduled_at=NOW - timedelta(minutes=1), sequence=0)
    (notification_id,) = await _store(factory, due)
    role = _role()

    async with factory() as db:
        lease = await acquire_scheduler_lease(db, holder="host-a", role=role, now=NOW)
        await db.commit()
    assert isinstance(lease, SchedulerLeaseClaim)

    blocked = await _run(factory, role=role, holder="host-b")
    assert blocked.active is False
    assert (blocked.claimed, blocked.expired, blocked.lease) == (0, 0, None)
    stored = await _get(factory, notification_id)
    assert stored[notification_id].status == "pending"

    elected = await _run(
        factory, role=role, holder="host-a", now=NOW + timedelta(seconds=1)
    )
    assert elected.active is True
    assert elected.claimed == 1
    stored = await _get(factory, notification_id)
    assert stored[notification_id].status == "sending"


async def test_only_one_candidate_takes_over_an_expired_lease(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _engine, factory = committed_db
    role = _role()
    async with factory() as db:
        db.add(
            SchedulerLease(
                name=role,
                holder="dead-host",
                fencing_token=7,
                lease_expires_at=NOW - timedelta(seconds=1),
                heartbeat_at=NOW - timedelta(seconds=2),
            )
        )
        await db.commit()

    acquired_a = asyncio.Event()
    release = asyncio.Event()

    async def candidate(holder: str) -> SchedulerLeaseClaim | None:
        async with factory() as db:
            lease = await acquire_scheduler_lease(
                db, holder=holder, role=role, now=NOW
            )
            if lease is not None:
                acquired_a.set()
            await release.wait()
            await db.commit()
            return lease

    first = asyncio.create_task(candidate("host-a"))
    await asyncio.wait_for(acquired_a.wait(), timeout=10)
    second = asyncio.create_task(candidate("host-b"))
    # Give the second candidate time to reach the row lock while the winner still holds it.
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)

    winners = [lease for lease in results if lease is not None]
    assert len(winners) == 1
    assert winners[0].fencing_token == 8
    assert results.count(None) == 1


# --- Atomic claim ----------------------------------------------------------------------


async def test_the_elected_scheduler_claims_a_due_row_once(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    actor: Actor,
) -> None:
    _engine, factory = committed_db
    due = _notification(actor, scheduled_at=NOW - timedelta(minutes=1), sequence=0)
    future = _notification(actor, scheduled_at=NOW + timedelta(minutes=5), sequence=1)
    cancelled = _notification(actor, status="cancelled", sequence=2)
    sent = _notification(actor, status="sent", sequence=3)
    in_flight = _notification(
        actor,
        status="sending",
        sequence=4,
        attempts=1,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    ids = await _store(factory, due, future, cancelled, sent, in_flight)
    role = _role()

    report = await _run(factory, role=role, holder="host-a")
    assert report.active is True
    assert (report.claimed, report.expired) == (1, 0)

    stored = await _get(factory, *ids)
    claimed = stored[due.id]
    assert claimed.status == "sending"
    assert claimed.attempts == 1
    assert claimed.lease_expires_at == NOW + DELIVERY_LEASE
    assert stored[future.id].status == "pending"
    assert stored[cancelled.id].status == "cancelled"
    assert stored[sent.id].status == "sent"
    assert stored[in_flight.id].status == "sending"
    assert stored[in_flight.id].lease_expires_at == NOW + timedelta(minutes=2)

    # A minute tick later the same due row is not delivered twice.
    again = await _run(
        factory, role=role, holder="host-a", now=NOW + timedelta(seconds=60)
    )
    assert again.active is True
    assert again.claimed == 0

    # The minute cadence leaves the normal lateness well inside the 15-minute target.
    assert NOW - claimed.scheduled_at <= DELIVERY_SLA


async def test_two_workers_claim_disjoint_due_rows_with_skip_locked(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    actor: Actor,
) -> None:
    _engine, factory = committed_db
    rows = [
        _notification(
            actor,
            scheduled_at=NOW - timedelta(minutes=index),
            sequence=index,
        )
        for index in range(6)
    ]
    await _store(factory, *rows)

    first_claimed = asyncio.Event()
    second_claimed = asyncio.Event()
    release = asyncio.Event()

    async def worker(limit: int, claimed_event: asyncio.Event) -> list[uuid.UUID]:
        async with factory() as db:
            claimed = await claim_due_notifications(db, now=NOW, limit=limit)
            claimed_event.set()
            await release.wait()
            await db.commit()
            return [row.id for row in claimed]

    first = asyncio.create_task(worker(2, first_claimed))
    await asyncio.wait_for(first_claimed.wait(), timeout=10)
    second = asyncio.create_task(worker(2, second_claimed))
    # The first worker still holds its row locks; the second must skip them instead of
    # waiting, and must not claim the same ids.
    await asyncio.wait_for(second_claimed.wait(), timeout=10)
    release.set()
    first_ids, second_ids = await asyncio.gather(first, second)

    assert len(first_ids) == 2
    assert len(second_ids) == 2
    assert set(first_ids).isdisjoint(second_ids)

    async with factory() as db:
        status_rows = (
            await db.execute(
                select(Notification.id, Notification.status).where(
                    Notification.id.in_([row.id for row in rows])
                )
            )
        ).all()
    statuses: dict[uuid.UUID, str] = {
        notification_id: status for notification_id, status in status_rows
    }
    assert list(statuses.values()).count("sending") == 4
    assert list(statuses.values()).count("pending") == 2


async def test_the_claim_is_ordered_by_due_instant_and_bounded(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    actor: Actor,
) -> None:
    _engine, factory = committed_db
    rows = [
        _notification(
            actor,
            scheduled_at=NOW - timedelta(minutes=3 - index),
            sequence=index,
        )
        for index in range(3)
    ]
    ids = await _store(factory, *rows)

    async with factory() as db:
        first = await claim_due_notifications(db, now=NOW, limit=2)
        await db.commit()
    assert [row.scheduled_at for row in first] == sorted(
        row.scheduled_at for row in first
    )
    first_ids = {row.id for row in first}
    assert first_ids == {ids[0], ids[1]}

    async with factory() as db:
        second = await claim_due_notifications(db, now=NOW, limit=2)
        await db.commit()
    assert [row.id for row in second] == [ids[2]]

    async with factory() as db:
        third = await claim_due_notifications(db, now=NOW, limit=2)
        await db.commit()
    assert third == []


# --- Leases ----------------------------------------------------------------------------


async def test_an_expired_sending_lease_becomes_uncertain_and_is_never_retried(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    actor: Actor,
) -> None:
    _engine, factory = committed_db
    expired = _notification(
        actor,
        status="sending",
        sequence=0,
        attempts=1,
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    live = _notification(
        actor,
        status="sending",
        sequence=1,
        attempts=1,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    due = _notification(actor, scheduled_at=NOW - timedelta(minutes=1), sequence=2)
    ids = await _store(factory, expired, live, due)
    role = _role()

    report = await _run(factory, role=role, holder="host-a")
    assert (report.claimed, report.expired) == (1, 1)

    stored = await _get(factory, *ids)
    uncertain = stored[expired.id]
    assert uncertain.status == "delivery_unknown"
    assert uncertain.last_error_code == "lease_expired"
    assert uncertain.attempts == 1
    assert uncertain.sent_at is None
    assert stored[live.id].status == "sending"
    assert stored[due.id].status == "sending"

    again = await _run(
        factory, role=role, holder="host-a", now=NOW + timedelta(seconds=60)
    )
    assert again.expired == 0
    assert again.claimed == 0

    stored = await _get(factory, expired.id)
    assert stored[expired.id].status == "delivery_unknown"


# --- Runtime role ----------------------------------------------------------------------


async def test_the_scheduler_claims_under_the_cross_tenant_jobs_role(
    committed_db: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
    migrated_database_url: str,
    actor: Actor,
) -> None:
    """A2.17: the cron binds `app.cross_tenant`, and the jobs role owns the lease table."""
    _engine, factory = committed_db
    due = _notification(actor, scheduled_at=NOW - timedelta(minutes=1), sequence=0)
    (notification_id,) = await _store(factory, due)
    role = _role()

    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'SET LOCAL ROLE "{JOBS_ROLE}"'))
                await connection.execute(
                    text("SELECT set_config('app.cross_tenant', 'on', true)")
                )
                session = AsyncSession(
                    bind=connection, join_transaction_mode="create_savepoint"
                )
                try:
                    report = await run_notification_scheduler(
                        session, now=NOW, holder="host-a", role=role
                    )
                    await session.flush()
                finally:
                    await session.close()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()

    assert report.active is True
    assert report.claimed == 1
    stored = await _get(factory, notification_id)
    # The role transaction was rolled back; the committed row is still the owner's pending
    # row. What matters is that the jobs role could elect and claim it under RLS.
    assert stored[notification_id].status == "pending"
