"""Idempotent `sync_task_event(task_id, task_version)` orchestration (J3.4, I-S3-JF-03).

The job must leave **exactly one** Calendar event when a confirmation is delivered twice, a
response is lost, or a retry follows a timeout. The protocol is:

- **Deterministic event ID.** ``maul`` + lower-case base32hex of the 16 task UUID bytes (30
  characters, all in Google's ``[a-v0-9]`` alphabet). The same task always targets the same
  event, so a duplicate insert collides instead of creating a second row.
- **Timeout → query before retrying.** A transient insert failure (timeout, 5xx, 429) never
  proves the write failed. The engine ``GET``s the deterministic ID first; a matching event
  is adopted, and only an absent event is retried.
- **409 → identity check and refresh.** A conflict may be our own earlier insert or a foreign
  event that happens to occupy the ID. Only an event whose private ``maulwurfTaskId`` matches
  this task is reconciled; anything else is left untouched and the task is marked ``failed``.
  When the identity matches but the stored body belongs to an older version, the reconcile
  reloads the task and **patches** the event only if this delivery is still current, so a
  stale job can never overwrite the content of a newer version.
- **Obsolete versions are discarded.** A job whose ``task_version`` is no longer the stored
  version makes no Google call, and the status update is a compare-and-set on the same
  version, so a concurrent edit can never be overwritten by an old delivery.
- **Blocked vs failed.** A missing calendar, a disconnected Google integration or an
  ``invalid_grant`` leaves the task ``blocked`` (J3.3 reconnects it); other terminal failures
  leave it ``failed``. Transient exhaustion raises so ARQ schedules a bounded retry.

The module is storage- and provider-agnostic: `TaskEventStore` and `CalendarGateway` are
ports implemented by `task_event_store` and `calendar_gateway`.
"""

import asyncio
import base64
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol

from app.services.google_calendar import (
    GoogleCalendarConflict,
    GoogleCalendarError,
    GoogleCalendarInvalidGrant,
    GoogleCalendarTransientError,
    GoogleCalendarUnauthorized,
)

logger = logging.getLogger(__name__)

# Prefix keeps Maulwurf events recognizable; base32hex only uses characters allowed by Google.
EVENT_ID_PREFIX = "maul"
TASK_ID_PROPERTY = "maulwurfTaskId"
USER_ID_PROPERTY = "maulwurfUserId"
# One delivery tries the insert twice; a second failure raises and ARQ retries the job.
INSERT_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 0.25


class TaskEventBlocked(Exception):
    """The integration or the task cannot be written to Calendar right now (not retryable)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TaskEventTransientError(Exception):
    """A bounded insert/lookup budget was exhausted; ARQ may retry the job."""


class SyncOutcome(StrEnum):
    SYNCED = "synced"
    RECONCILED = "reconciled"
    STALE_VERSION = "stale_version"
    TASK_NOT_FOUND = "task_not_found"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskSnapshot:
    """The task projection J3.4 needs. Event content is filled by the caller's builder."""

    task_id: uuid.UUID
    user_id: uuid.UUID
    version: int
    calendar_sync_status: str
    calendar_id: str | None
    calendar_event_id: str | None
    title: str
    detail: str | None
    timezone: str
    all_day: bool
    event_start_at: datetime | None
    event_end_at: datetime | None
    event_start_date: date | None
    event_end_date_exclusive: date | None

    def has_event_block(self) -> bool:
        if self.all_day:
            return self.event_start_date is not None and self.event_end_date_exclusive is not None
        return self.event_start_at is not None and self.event_end_at is not None


class TaskEventStore(Protocol):
    async def load(self, task_id: uuid.UUID) -> TaskSnapshot | None: ...

    async def mark_synced(
        self, task_id: uuid.UUID, version: int, event_id: str
    ) -> bool: ...

    async def mark_failed(self, task_id: uuid.UUID, version: int) -> bool: ...

    async def mark_blocked(self, task_id: uuid.UUID, version: int) -> bool: ...


class CalendarGateway(Protocol):
    async def insert_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    async def get_event(
        self, calendar_id: str, event_id: str
    ) -> Mapping[str, Any] | None: ...

    async def update_event(
        self, calendar_id: str, event_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


GatewayFactory = Callable[[TaskSnapshot], Awaitable[CalendarGateway]]
BodyBuilder = Callable[[TaskSnapshot], Mapping[str, Any]]
Sleeper = Callable[[float], Awaitable[None]]


def deterministic_event_id(task_id: uuid.UUID) -> str:
    """Stable Google event ID derived only from the task UUID.

    Base32hex is case-insensitive but Google stores lower case; stripping the ``=`` padding
    yields 26 characters for 16 bytes. The result is a bijection of the UUID, so two tasks
    can never share an event ID while a repeated delivery always targets the same one.
    """
    encoded = base64.b32hexencode(task_id.bytes).decode("ascii").rstrip("=").lower()
    return f"{EVENT_ID_PREFIX}{encoded}"


def event_identity_matches(event: Mapping[str, Any], task_id: uuid.UUID) -> bool:
    """Whether an existing Google event belongs to this task (never trust the ID alone)."""
    extended = event.get("extendedProperties")
    if not isinstance(extended, Mapping):
        return False
    private = extended.get("private")
    if not isinstance(private, Mapping):
        return False
    return private.get(TASK_ID_PROPERTY) == str(task_id)


def _same_instant(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        # Google answers with the event's own offset while the body is sent in UTC.
        return datetime.fromisoformat(left) == datetime.fromisoformat(right)
    except (TypeError, ValueError):
        return left == right


def _same_block(existing: Any, desired: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping):
        return False
    if desired.get("date") is not None or existing.get("date") is not None:
        return existing.get("date") == desired.get("date")
    return _same_instant(existing.get("dateTime"), desired.get("dateTime")) and (
        existing.get("timeZone") == desired.get("timeZone")
    )


def _description(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def event_content_matches(event: Mapping[str, Any], body: Mapping[str, Any]) -> bool:
    """Whether the stored event already carries exactly the content we would send.

    Used by the 409 reconcile: an event whose private identity matches but whose body belongs
    to an older task version has to be repaired, otherwise the task would be `synced` while
    Google still shows the previous title/date/description.
    """
    if event.get("summary") != body.get("summary"):
        return False
    if _description(event.get("description")) != _description(body.get("description")):
        return False
    return _same_block(event.get("start"), body.get("start") or {}) and _same_block(
        event.get("end"), body.get("end") or {}
    )


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("event block must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_event_body(task: TaskSnapshot) -> dict[str, Any]:
    """Provisional J3.4 body: identity envelope plus the confirmed block.

    J3.5 owns the final content (``[Maulwurf]`` prefix, details and the authenticated
    segment link); it can replace this builder without touching the idempotency protocol.
    """
    body: dict[str, Any] = {
        "summary": task.title,
        "extendedProperties": {
            "private": {
                TASK_ID_PROPERTY: str(task.task_id),
                USER_ID_PROPERTY: str(task.user_id),
            }
        },
    }
    if task.detail:
        body["description"] = task.detail
    if task.all_day:
        if task.event_start_date is None or task.event_end_date_exclusive is None:
            raise ValueError("event block is missing")
        body["start"] = {"date": task.event_start_date.isoformat()}
        body["end"] = {"date": task.event_end_date_exclusive.isoformat()}
        return body
    if task.event_start_at is None or task.event_end_at is None:
        raise ValueError("event block is missing")
    body["start"] = {"dateTime": _rfc3339(task.event_start_at), "timeZone": task.timezone}
    body["end"] = {"dateTime": _rfc3339(task.event_end_at), "timeZone": task.timezone}
    return body


class TaskEventSync:
    """One idempotent task → event synchronization attempt."""

    def __init__(
        self,
        store: TaskEventStore,
        gateways: GatewayFactory,
        *,
        body_builder: BodyBuilder = build_event_body,
        insert_attempts: int = INSERT_ATTEMPTS,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        if insert_attempts < 1:
            raise ValueError("insert_attempts must be at least 1")
        self._store = store
        self._gateways = gateways
        self._body_builder = body_builder
        self._insert_attempts = insert_attempts
        self._sleeper = sleeper

    async def run(self, task_id: uuid.UUID, task_version: int) -> SyncOutcome:
        """Load the task and synchronize it (convenience for callers without tenant setup)."""
        task = await self._store.load(task_id)
        if task is None:
            return SyncOutcome.TASK_NOT_FOUND
        return await self.sync(task, expected_version=task_version)

    async def load(self, task_id: uuid.UUID) -> TaskSnapshot | None:
        return await self._store.load(task_id)

    async def sync(self, task: TaskSnapshot, *, expected_version: int) -> SyncOutcome:
        """Synchronize an already loaded task; obsolete versions never reach Google."""
        if task.version != expected_version:
            logger.info(
                "calendar_sync.stale_version task_id=%s job_version=%d task_version=%d",
                task.task_id,
                expected_version,
                task.version,
            )
            return SyncOutcome.STALE_VERSION
        calendar_id = task.calendar_id
        if not calendar_id:
            return await self._blocked(task, "calendar_missing")
        if not task.has_event_block():
            return await self._blocked(task, "event_block_missing")

        event_id = deterministic_event_id(task.task_id)
        try:
            gateway = await self._gateways(task)
        except TaskEventBlocked as exc:
            return await self._blocked(task, exc.code)

        body = self._body_builder(task)
        try:
            for attempt in range(1, self._insert_attempts + 1):
                try:
                    await gateway.insert_event(calendar_id, event_id, body)
                    return await self._settle(task, event_id, SyncOutcome.SYNCED)
                except GoogleCalendarConflict:
                    existing = await gateway.get_event(calendar_id, event_id)
                    return await self._reconcile(task, existing, event_id, gateway, calendar_id)
                except GoogleCalendarTransientError:
                    # A lost response is indistinguishable from a failed insert: look it up.
                    existing = await gateway.get_event(calendar_id, event_id)
                    if existing is not None:
                        return await self._reconcile(
                            task, existing, event_id, gateway, calendar_id
                        )
                    if attempt >= self._insert_attempts:
                        raise
                    await self._sleeper(RETRY_DELAY_SECONDS)
        except GoogleCalendarInvalidGrant:
            # GoogleCalendarService already invoked the disconnect callback (J3.2).
            return await self._blocked(task, "google_invalid_grant")
        except GoogleCalendarUnauthorized:
            # A 401 that survived the token refresh needs a reconnect (J3.3); `failed` would
            # never return to `pending` after that reconnect.
            return await self._blocked(task, "google_unauthorized")
        except GoogleCalendarTransientError as exc:
            await self._mark_failed(task)
            logger.warning(
                "calendar_sync.transient_exhausted task_id=%s error=%s",
                task.task_id,
                type(exc).__name__,
            )
            raise TaskEventTransientError("calendar_insert_transient") from exc
        except GoogleCalendarError as exc:
            logger.warning(
                "calendar_sync.failed task_id=%s code=%s", task.task_id, exc.code
            )
            await self._mark_failed(task)
            return SyncOutcome.FAILED
        # The loop either returns or raises; this keeps mypy honest if that changes.
        await self._mark_failed(task)
        raise TaskEventTransientError("calendar_insert_transient")

    async def _reconcile(
        self,
        task: TaskSnapshot,
        existing: Mapping[str, Any] | None,
        event_id: str,
        gateway: CalendarGateway,
        calendar_id: str,
    ) -> SyncOutcome:
        """Adopt an existing event only when its identity belongs to this task."""
        if existing is None:
            logger.warning("calendar_sync.conflict_without_event task_id=%s", task.task_id)
            await self._mark_failed(task)
            return SyncOutcome.FAILED
        if not event_identity_matches(existing, task.task_id):
            logger.warning("calendar_sync.identity_mismatch task_id=%s", task.task_id)
            await self._mark_failed(task)
            return SyncOutcome.FAILED
        body = self._body_builder(task)
        if not event_content_matches(existing, body):
            # A newer version may have settled while this delivery was in flight: reload
            # before writing, or the stale body would overwrite the newer event content.
            current = await self._store.load(task.task_id)
            if current is None or current.version != task.version:
                logger.info(
                    "calendar_sync.stale_before_update task_id=%s job_version=%d",
                    task.task_id,
                    task.version,
                )
                return SyncOutcome.STALE_VERSION
            await gateway.update_event(calendar_id, event_id, body)
            logger.info("calendar_sync.event_refreshed task_id=%s", task.task_id)
            if await self._repair_newer_version(task, body, event_id, gateway, calendar_id):
                return SyncOutcome.STALE_VERSION
        return await self._settle(task, event_id, SyncOutcome.RECONCILED)

    async def _repair_newer_version(
        self,
        task: TaskSnapshot,
        sent: Mapping[str, Any],
        event_id: str,
        gateway: CalendarGateway,
        calendar_id: str,
    ) -> bool:
        """Repair the event if a newer version landed while the PATCH was in flight.

        The pre-PATCH reload closes the reported race, but an edit can still commit between
        that reload and the write. The newer version's own delivery may already have
        finished, so it cannot be assumed to repair the event: write its body here instead.
        """
        current = await self._store.load(task.task_id)
        if current is None or current.version == task.version:
            return False
        logger.info(
            "calendar_sync.stale_after_update task_id=%s job_version=%d task_version=%d",
            task.task_id,
            task.version,
            current.version,
        )
        if not current.has_event_block():
            # The newer delivery marks the task blocked; there is no writable body.
            return True
        latest = self._body_builder(current)
        if not event_content_matches(sent, latest):
            # Keep the known event (old calendar) at the newest content; a moved calendar is
            # the newer delivery's responsibility, not a reason to leave stale text here.
            await gateway.update_event(calendar_id, event_id, latest)
            logger.info("calendar_sync.event_repaired task_id=%s", task.task_id)
        return True

    async def _settle(
        self, task: TaskSnapshot, event_id: str, outcome: SyncOutcome
    ) -> SyncOutcome:
        updated = await self._store.mark_synced(task.task_id, task.version, event_id)
        if not updated:
            # Another edit won while the insert was in flight; the event is reused by the
            # current version's delivery, which has the same deterministic ID.
            logger.info("calendar_sync.stale_after_write task_id=%s", task.task_id)
            return SyncOutcome.STALE_VERSION
        return outcome

    async def _blocked(self, task: TaskSnapshot, code: str) -> SyncOutcome:
        logger.info("calendar_sync.blocked task_id=%s code=%s", task.task_id, code)
        await self._store.mark_blocked(task.task_id, task.version)
        return SyncOutcome.BLOCKED

    async def _mark_failed(self, task: TaskSnapshot) -> None:
        await self._store.mark_failed(task.task_id, task.version)
