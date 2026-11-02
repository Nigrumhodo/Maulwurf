"""J4.8 / C-S4-JF-10: provider budget usage for the internal Prometheus endpoint.

The budget is a monthly cap over a unit the application already measures:

- ``llm`` — input + output tokens persisted on assistant messages;
- ``asr`` — source audio minutes of classes whose transcript was committed in the month.
  The month is defined by ``transcripts.created_at``: an audio uploaded on the last day of
  the previous month counts when its transcript is committed in the current one, and an
  audio uploaded this month does not count until its transcript exists. The S3.5
  measurement recorded a 1.00833 overlap factor, so billed provider minutes are slightly
  higher and this value is a lower bound;
- ``google`` — recorded Google operations: sent notifications, Calendar task-event writes
  and authorized remote deletions. Manual Calendar reads and token refreshes are not
  durably recorded, so this is also a lower bound.

No price, quota or capacity number is invented here. Without a configured cap the usage
is still exposed with ``configured=0`` and nothing alarms. The window is the UTC calendar
month, which keeps the metric deterministic across hosts and matches the monthly budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as live_settings
from app.core.prometheus import Sample
from app.models import (
    Audio,
    GoogleRemoteOperation,
    Message,
    Notification,
    OutboxEvent,
    Transcript,
)

Provider = Literal["llm", "asr", "google"]
BudgetState = Literal["unconfigured", "ok", "warning", "exhausted"]

PROVIDERS: Final[tuple[Provider, ...]] = ("llm", "asr", "google")
UNITS: Final[dict[str, str]] = {
    "llm": "tokens",
    "asr": "minutes",
    "google": "operations",
}
DEFAULT_WARNING_RATIO: Final = 0.8
GOOGLE_CALENDAR_WRITE_EVENT: Final = "sync_task_event"
JOBS_ROLE: Final = "maulwurf_jobs"


@dataclass(frozen=True)
class BudgetStatus:
    """One provider budget at one instant; ``limit=None`` means not configured."""

    provider: str
    unit: str
    used: float
    limit: float | None
    warning_ratio: float

    @property
    def configured(self) -> bool:
        return self.limit is not None

    @property
    def ratio(self) -> float | None:
        if self.limit is None:
            return None
        return self.used / self.limit

    @property
    def state(self) -> BudgetState:
        limit = self.limit
        if limit is None:
            return "unconfigured"
        ratio = self.used / limit
        if ratio >= 1.0:
            return "exhausted"
        if ratio >= self.warning_ratio:
            return "warning"
        return "ok"

    @property
    def warning(self) -> bool:
        return self.state == "warning"

    @property
    def exhausted(self) -> bool:
        return self.state == "exhausted"


def monthly_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Start and exclusive end of the UTC calendar month containing ``now``."""
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone")
    utc = now.astimezone(UTC)
    start = datetime(utc.year, utc.month, 1, tzinfo=UTC)
    if utc.month == 12:
        end = datetime(utc.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(utc.year, utc.month + 1, 1, tzinfo=UTC)
    return start, end


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def evaluate_budget(
    provider: str,
    used: float,
    limit: float | None,
    *,
    warning_ratio: float = DEFAULT_WARNING_RATIO,
) -> BudgetStatus:
    """Validate one budget and derive its state; pure and independent of the database."""
    if provider not in UNITS:
        raise ValueError(f"unknown provider: {provider!r}")
    used_value = _finite(used, "used")
    if used_value < 0:
        raise ValueError("used must be >= 0")
    ratio = _finite(warning_ratio, "warning_ratio")
    if not 0.0 < ratio <= 1.0:
        raise ValueError("warning_ratio must be in (0, 1]")
    limit_value: float | None = None
    if limit is not None:
        limit_value = _finite(limit, "limit")
        if limit_value <= 0:
            raise ValueError("limit must be > 0")
    return BudgetStatus(
        provider=provider,
        unit=UNITS[provider],
        used=used_value,
        limit=limit_value,
        warning_ratio=ratio,
    )


def budget_caps(settings: Settings = live_settings) -> dict[str, float | None]:
    """Configured monthly caps, keyed by provider. Unset means no alert."""
    return {
        "llm": None if settings.budget_llm_tokens is None else float(settings.budget_llm_tokens),
        "asr": settings.budget_asr_minutes,
        "google": (
            None
            if settings.budget_google_operations is None
            else float(settings.budget_google_operations)
        ),
    }


def statuses_from_settings(
    settings: Settings = live_settings, *, used: dict[str, float] | None = None
) -> list[BudgetStatus]:
    """Budget statuses with zero usage, for startup checks and documentation examples."""
    caps = budget_caps(settings)
    measurements = used or {}
    return [
        evaluate_budget(
            provider,
            measurements.get(provider, 0.0),
            caps[provider],
            warning_ratio=settings.budget_warning_ratio,
        )
        for provider in PROVIDERS
    ]


def budget_samples(statuses: list[BudgetStatus]) -> list[Sample]:
    """Prometheus samples for the configured and unconfigured budgets."""
    samples: list[Sample] = []
    for status in statuses:
        samples.append(
            Sample(
                "maulwurf_budget_usage",
                "gauge",
                "Measured provider usage in the current UTC month.",
                status.used,
                {"provider": status.provider, "unit": status.unit},
            )
        )
        samples.append(
            Sample(
                "maulwurf_budget_configured",
                "gauge",
                "1 when a monthly cap is configured for this provider.",
                1.0 if status.configured else 0.0,
                {"provider": status.provider},
            )
        )
        limit = status.limit
        if limit is None:
            continue
        ratio = status.used / limit
        samples.extend(
            [
                Sample(
                    "maulwurf_budget_limit",
                    "gauge",
                    "Configured monthly cap for this provider.",
                    limit,
                    {"provider": status.provider, "unit": status.unit},
                ),
                Sample(
                    "maulwurf_budget_ratio",
                    "gauge",
                    "Usage divided by the configured monthly cap.",
                    ratio,
                    {"provider": status.provider},
                ),
                Sample(
                    "maulwurf_budget_warning",
                    "gauge",
                    "1 when usage reached the warning ratio but not the cap.",
                    1.0 if status.warning else 0.0,
                    {"provider": status.provider},
                ),
                Sample(
                    "maulwurf_budget_exhausted",
                    "gauge",
                    "1 when usage reached or exceeded the monthly cap.",
                    1.0 if status.exhausted else 0.0,
                    {"provider": status.provider},
                ),
            ]
        )
    return samples


async def _cross_tenant_read(db: AsyncSession) -> None:
    """The internal metrics aggregate uses the same explicit jobs-role policy as J2.5."""
    await db.execute(text(f'SET LOCAL ROLE "{JOBS_ROLE}"'))
    await db.execute(text("SELECT set_config('app.cross_tenant', 'on', true)"))


async def collect_usage(
    db: AsyncSession, *, now: datetime | None = None, cross_tenant: bool = False
) -> dict[str, float]:
    """Measured usage per provider for the UTC month containing ``now``."""
    if cross_tenant:
        await _cross_tenant_read(db)
    start, end = monthly_bounds(now or datetime.now(UTC))

    llm_tokens = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(Message.input_tokens, 0)
                    + func.coalesce(Message.output_tokens, 0)
                ),
                0,
            )
        ).where(Message.created_at >= start, Message.created_at < end)
    )

    # The month belongs to the committed transcript, not to the upload: a class uploaded
    # on the last day of the previous month still counts here when its transcript is
    # committed in the current one. Driving the read from `transcripts` also keeps each
    # class counted once when several transcript versions land in the same month, and it
    # lets the planner start from the month range instead of scanning every audio row.
    committed_audio_ids = (
        select(Transcript.audio_id)
        .where(Transcript.created_at >= start, Transcript.created_at < end)
        .distinct()
        .subquery()
    )
    asr_seconds = await db.scalar(
        select(func.coalesce(func.sum(Audio.duration_seconds), 0.0))
        .select_from(Audio)
        .join(committed_audio_ids, committed_audio_ids.c.audio_id == Audio.id)
        .where(Audio.duration_seconds.is_not(None))
    )

    sent_notifications = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.status == "sent",
            Notification.sent_at >= start,
            Notification.sent_at < end,
        )
    )
    calendar_writes = await db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.type == GOOGLE_CALENDAR_WRITE_EVENT,
            OutboxEvent.created_at >= start,
            OutboxEvent.created_at < end,
        )
    )
    remote_deletions = await db.scalar(
        select(func.count())
        .select_from(GoogleRemoteOperation)
        .where(
            GoogleRemoteOperation.created_at >= start,
            GoogleRemoteOperation.created_at < end,
        )
    )

    return {
        "llm": float(llm_tokens or 0),
        "asr": float(asr_seconds or 0.0) / 60.0,
        "google": float(
            (sent_notifications or 0) + (calendar_writes or 0) + (remote_deletions or 0)
        ),
    }


async def collect_budget_samples(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    cross_tenant: bool = False,
    settings: Settings = live_settings,
) -> list[Sample]:
    """Usage + configured caps as Prometheus samples, read in one session."""
    usage = await collect_usage(db, now=now, cross_tenant=cross_tenant)
    return budget_samples(statuses_from_settings(settings, used=usage))
