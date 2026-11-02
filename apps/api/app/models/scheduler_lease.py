"""Single-active-role election for the ARQ scheduler (J4.2).

`SchedulerSettings` may run in more than one process. The M8 contract still requires **one
active scheduling role**, so the processes elect it with a row in PostgreSQL instead of
Redis: Redis is the job queue, not the authority, and losing it must not let two schedulers
claim the same due occurrences.

The row is global (there is no tenant column): it describes infrastructure, never a user's
data. `holder` identifies the process that currently owns the role, `fencing_token` grows
every time a different holder takes over, and `lease_expires_at` bounds how long the claim
is valid. A process renews the same row on every cron tick; only an expired row may be
taken over, and every comparison uses PostgreSQL's clock. The claim copies the elected
`fencing_token` into `notifications.claimed_by_fence`, so the delivery step (J4.1) can
prove that a row still belongs to the current election before sending it. Migration `0017`
grants the table to the `maulwurf_jobs` role only, because the API never schedules
deliveries.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at


class SchedulerLease(Base):
    """One elected scheduler role, global to the deployment."""

    __tablename__ = "scheduler_leases"
    __table_args__ = (
        CheckConstraint(
            "NOT public.maulwurf_text_is_blank(name)", name="name_not_blank"
        ),
        CheckConstraint(
            "NOT public.maulwurf_text_is_blank(holder)", name="holder_not_blank"
        ),
        CheckConstraint("fencing_token >= 1", name="fencing_token"),
        # A lease that ends before its heartbeat is not a lease. This also rejects a
        # zero/negative TTL before it can make a row immediately takeable.
        CheckConstraint("lease_expires_at > heartbeat_at", name="lease_window"),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    holder: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
