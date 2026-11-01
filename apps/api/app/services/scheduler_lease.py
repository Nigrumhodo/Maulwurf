"""Single-role scheduler election and atomic notification claiming (J4.2).

The M8 contract asks for a native ARQ cron scheduler with **one active role**, elected
through a database lease, that computes due occurrences every minute and claims them
atomically. Delivery itself stays with J4.1 (Gmail) and the uncertain-delivery handling with
J4.5; this module owns the election and the hand-off state.

Election
--------
`acquire_scheduler_lease` races safely because the row is unique per role and PostgreSQL
serialises `INSERT ... ON CONFLICT DO UPDATE` on it. A caller may renew its own live lease; a
different holder only wins after `lease_expires_at` has passed. The winning holder keeps the
same `fencing_token` while it renews, and the token grows whenever the role changes hands. The
acquire and the claim run in the same transaction, so another scheduler cannot take the lease
between them and two schedulers never claim in parallel under the same elected role.

Lease expiry, the lease window, the due comparison and the delivery lease all use
PostgreSQL's transaction clock (`now()`), not the wall clock of each scheduler host. Two
hosts with a clock skew can therefore not both consider the same live lease expired: the
database is the single authority for time, as it is for the row itself.

Claiming
--------
`claim_due_notifications` selects pending rows whose `scheduled_at` is already due, locks them
with `FOR UPDATE SKIP LOCKED`, and moves them to `sending` with a bounded delivery lease.
Rows another worker already claimed are skipped instead of waited on, so several workers can
drain a backlog without blocking each other and without claiming a row twice. `dedupe_key`
(the A4.1 unique identity) still makes a duplicate occurrence impossible by construction.

An expired `sending` lease becomes `delivery_unknown` before the new batch is claimed. It is
never returned to `pending`, because the previous worker may already have reached the
provider: Gmail does not offer exactly-once delivery.

The cron runs every minute. With a minute of scan granularity and a bounded batch, the target
delivery SLA of 15 minutes (`DELIVERY_SLA`) leaves ample room for a slow tick; it is not a
second-level guarantee. `scheduled_at` is already the absolute instant computed from the
user's local time when the occurrence was created (J4.3/J4.4/J4.6), so the scheduler only
compares instants.
"""

import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import (
    CursorResult,
    DateTime,
    Interval,
    case,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import Notification, SchedulerLease
from app.services.notifications import LEASE_EXPIRED

# The role elected today. A future scheduler can add another named row without changing the
# election mechanics.
SCHEDULER_ROLE: Final = "notification_scheduler"
# Mirrors the cron cadence with a two-tick margin: one late tick does not lose the role, and
# a dead process is replaced within ~3 minutes, well inside the 15-minute SLA.
DEFAULT_LEASE_TTL: Final = timedelta(seconds=120)
# How long a claimed occurrence may stay `sending` before it is uncertain delivery.
DELIVERY_LEASE: Final = timedelta(minutes=5)
# Target send latency from the M8 contract, not second-level exactness.
DELIVERY_SLA: Final = timedelta(minutes=15)
# Bounded batch so one tick cannot monopolise the worker or hold a huge transaction.
BATCH_SIZE: Final = 100

_PROCESS_NONCE = uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class SchedulerLeaseClaim:
    """The row an elected scheduler owns for the duration of one tick."""

    name: str
    holder: str
    fencing_token: int
    expires_at: datetime


@dataclass(frozen=True)
class ClaimedNotification:
    """A due occurrence moved to `sending` by this claim."""

    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    scheduled_at: datetime
    lease_expires_at: datetime
    attempts: int


@dataclass(frozen=True)
class SchedulerReport:
    """Outcome of one scheduler tick; counts only, never a row payload."""

    active: bool
    expired: int = 0
    claimed: int = 0
    lease: SchedulerLeaseClaim | None = None


def process_holder() -> str:
    """Stable identity of this scheduler process, unique across hosts and restarts.

    The nonce separates two processes that reuse the same PID on the same host (a restart
    inside one container can). It is not a secret and is never derived from user data.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{_PROCESS_NONCE}"


def _require_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("the scheduler instant must be timezone-aware")
    return value


def _validate_ttl(ttl: timedelta) -> timedelta:
    if ttl <= timedelta(0):
        raise ValueError("the scheduler lease TTL must be positive")
    return ttl


def _validate_text(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    return value


def _instant_expression(now: datetime | None) -> ColumnElement[datetime]:
    """The tick instant: PostgreSQL's transaction clock unless a caller freezes it.

    Lease expiry and the due comparison must not depend on each host's wall clock: two
    schedulers with a clock skew could otherwise both consider the same lease expired (or
    disagree on which occurrence is due). `now()` is stable for the whole transaction, so
    the election, the expiry and the claim of one tick share one instant. The explicit
    `now` exists for deterministic tests and is rendered as a bound parameter, never
    interpolated.
    """
    if now is None:
        return func.now()
    return literal(_require_instant(now), DateTime(timezone=True))


def _interval_expression(delta: timedelta) -> ColumnElement[Any]:
    # PostgreSQL has no implicit timestamp + unknown-parameter rule under every driver;
    # the explicit CAST keeps the interval unambiguous for asyncpg.
    return sql_cast(literal(delta, Interval()), Interval())


async def acquire_scheduler_lease(
    db: AsyncSession,
    *,
    holder: str,
    role: str = SCHEDULER_ROLE,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> SchedulerLeaseClaim | None:
    """Take or renew the role; `None` means another live holder owns it.

    The update and its `WHERE` are one statement, so two candidates cannot both see an
    expired row and both increment the fence. The row lock lasts until the caller commits,
    which is exactly the window in which the claim must remain exclusive. The comparison
    and the new expiry use PostgreSQL's clock (`now()`), never the caller host's.
    """
    clock = _instant_expression(now)
    _validate_ttl(ttl)
    _validate_text(role, "role")
    _validate_text(holder, "holder")
    expires_at = clock + _interval_expression(ttl)

    statement = (
        pg_insert(SchedulerLease)
        .values(
            name=role,
            holder=holder,
            fencing_token=1,
            lease_expires_at=expires_at,
            heartbeat_at=clock,
            updated_at=clock,
        )
        .on_conflict_do_update(
            index_elements=[SchedulerLease.name],
            set_={
                "holder": holder,
                # A renewal by the same process keeps the fence; a takeover increments it
                # so a stale owner can be told apart from the current one.
                "fencing_token": case(
                    (
                        SchedulerLease.holder == holder,
                        SchedulerLease.fencing_token,
                    ),
                    else_=SchedulerLease.fencing_token + 1,
                ),
                "lease_expires_at": expires_at,
                "heartbeat_at": clock,
                "updated_at": clock,
            },
            where=or_(
                SchedulerLease.holder == holder,
                SchedulerLease.lease_expires_at <= clock,
            ),
        )
        .returning(
            SchedulerLease.name,
            SchedulerLease.holder,
            SchedulerLease.fencing_token,
            SchedulerLease.lease_expires_at,
        )
    )
    row = (await db.execute(statement)).first()
    if row is None:
        return None
    name, current_holder, fencing_token, lease_expires_at = row
    return SchedulerLeaseClaim(
        name=str(name),
        holder=str(current_holder),
        fencing_token=int(fencing_token),
        expires_at=lease_expires_at,
    )


async def expire_delivery_leases(
    db: AsyncSession, *, now: datetime | None = None
) -> int:
    """Close expired `sending` leases as `delivery_unknown`; never back to `pending`.

    Expiry is decided by PostgreSQL's clock, not by the process that runs the sweep.
    """
    clock = _instant_expression(now)
    statement = (
        update(Notification)
        .where(
            Notification.status == "sending",
            or_(
                Notification.lease_expires_at.is_(None),
                Notification.lease_expires_at <= clock,
            ),
        )
        .values(
            status="delivery_unknown",
            last_error_code=LEASE_EXPIRED,
            updated_at=clock,
        )
    )
    result = await db.execute(statement)
    return max(int(cast(CursorResult[Any], result).rowcount or 0), 0)


async def claim_due_notifications(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = BATCH_SIZE,
    delivery_lease: timedelta = DELIVERY_LEASE,
) -> list[ClaimedNotification]:
    """Atomically move due `pending` occurrences to a leased `sending` state.

    `FOR UPDATE SKIP LOCKED` is what makes multiple workers safe: each row is claimed by one
    statement, and a concurrent worker skips it instead of duplicating the send. Rows are
    taken in due order so a backlog drains fairly. Both the due comparison and the delivery
    lease use PostgreSQL's clock, so a host clock skew cannot claim a row early.
    """
    clock = _instant_expression(now)
    _validate_ttl(delivery_lease)
    if limit < 1:
        raise ValueError("the claim batch must contain at least one row")

    due = (
        select(Notification.id)
        .where(
            Notification.status == "pending",
            Notification.scheduled_at <= clock,
        )
        .order_by(Notification.scheduled_at.asc(), Notification.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
        .subquery("due")
    )
    statement = (
        update(Notification)
        .where(Notification.id == due.c.id)
        .values(
            status="sending",
            lease_expires_at=clock + _interval_expression(delivery_lease),
            attempts=Notification.attempts + 1,
            updated_at=clock,
        )
        .returning(
            Notification.id,
            Notification.user_id,
            Notification.type,
            Notification.scheduled_at,
            Notification.lease_expires_at,
            Notification.attempts,
        )
    )
    rows = (await db.execute(statement)).all()
    return [
        ClaimedNotification(
            id=notification_id,
            user_id=user_id,
            type=str(type_),
            scheduled_at=scheduled_at,
            lease_expires_at=lease_expires_at,
            attempts=int(attempts),
        )
        for notification_id, user_id, type_, scheduled_at, lease_expires_at, attempts in rows
    ]


async def run_notification_scheduler(
    db: AsyncSession,
    *,
    enabled: bool = False,
    now: datetime | None = None,
    holder: str | None = None,
    role: str = SCHEDULER_ROLE,
    ttl: timedelta = DEFAULT_LEASE_TTL,
    batch_size: int = BATCH_SIZE,
    delivery_lease: timedelta = DELIVERY_LEASE,
) -> SchedulerReport:
    """One scheduler tick: elect/renew, expire uncertain sends, claim due rows.

    The caller owns the transaction. The cron commits through `session_scope`, which keeps
    the election row locked until the claim is durable.

    Disabled by default (`MAULWURF_NOTIFICATION_SCHEDULER_ENABLED`): J4.1 owns the Gmail
    consumer of `sending`, and claiming before it exists would expire every claim into a
    false `delivery_unknown`. A disabled tick touches neither the election nor any row.

    When `now` is omitted every step uses PostgreSQL's transaction clock, so the election,
    the expiry sweep and the claim of this tick share one database instant.
    """
    if not enabled:
        return SchedulerReport(active=False)
    lease = await acquire_scheduler_lease(
        db,
        holder=holder or process_holder(),
        role=role,
        now=now,
        ttl=ttl,
    )
    if lease is None:
        return SchedulerReport(active=False)
    expired = await expire_delivery_leases(db, now=now)
    claimed = await claim_due_notifications(
        db,
        now=now,
        limit=batch_size,
        delivery_lease=delivery_lease,
    )
    return SchedulerReport(
        active=True,
        expired=expired,
        claimed=len(claimed),
        lease=lease,
    )
