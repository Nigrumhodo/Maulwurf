"""ARQ `sync_task_event(task_id, task_version)` worker (J3.4, I-S3-JF-03).

The job receives only IDs (never task content), resolves the task under the explicit
cross-tenant worker policy, binds its owner for the status write, then delegates to
`TaskEventSync`. The Calendar access token is renewed in-process from the encrypted refresh
token (`services.google_tokens`) when it is expired or Google answers 401, under a row lock;
token material is never logged. A rejected refresh disconnects the integration (J3.2) and
leaves the task `blocked`, because a reconnect (J3.3) only revisits `blocked` tasks.

Retry budget: one delivery plus `MAX_TRIES - 1` transient retries, using the same bounded
backoff as the outbox. `invalid_grant` disconnects the integration and leaves the task
`blocked` (J3.3 reconnects it); permanent errors leave it `failed` and are not raised.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final

import httpx
from arq.worker import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import (
    bind_cross_tenant,
    bind_user,
    clear_user,
    reset_cross_tenant,
    session_scope,
)
from app.models import GoogleCredential
from app.services.calendar_gateway import GoogleCalendarGateway
from app.services.google_calendar import (
    GoogleCalendarInvalidGrant,
    GoogleCalendarService,
    GoogleCalendarTransientError,
    GoogleCalendarUnauthorized,
    disconnect_google_credential,
)
from app.services.google_tokens import (
    GoogleTokenRejected,
    GoogleTokenTransient,
    GoogleTokenUnavailable,
    fresh_access_token,
)
from app.services.outbox_retry import DEFAULT_RETRY
from app.services.task_event_store import SqlTaskEventStore
from app.services.task_event_sync import (
    CalendarGateway,
    SyncOutcome,
    TaskEventBlocked,
    TaskEventSync,
    TaskEventTransientError,
    TaskSnapshot,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
GatewayFactory = Callable[[TaskSnapshot], Awaitable[CalendarGateway]]

# ctx keys for injecting a database, a Calendar gateway or the Google HTTP transports. Only
# tests set them. Calendar uses the blocking client; the token endpoint is called with httpx
# async, so each side takes its own transport type.
SESSIONS_KEY: Final = "calendar_sync_session_factory"
GATEWAYS_KEY: Final = "calendar_sync_gateway_factory"
CALENDAR_TRANSPORT_KEY: Final = "calendar_sync_calendar_transport"
TOKEN_TRANSPORT_KEY: Final = "calendar_sync_token_transport"  # noqa: S105 - ctx key

# One delivery plus three transient retries, aligned with the outbox/index budgets.
MAX_TRIES: Final = 4
JOB_TIMEOUT_S: Final = 120


async def sync_task_event(
    ctx: dict[str, Any], task_id: str, task_version: int | None
) -> None:
    """ARQ entry point. Receives IDs only; never raises for a permanent failure."""
    job_try = int(ctx.get("job_try", 1))
    try:
        parsed_id = uuid.UUID(task_id)
    except (TypeError, ValueError):
        logger.warning("calendar_sync.invalid_task_id")
        return
    if not isinstance(task_version, int) or isinstance(task_version, bool):
        logger.warning("calendar_sync.invalid_task_version task_id=%s", task_id)
        return

    sessions = _sessions(ctx)
    store = SqlTaskEventStore(sessions)
    engine = TaskEventSync(store, _gateways(ctx, sessions))
    try:
        task = await _load_cross_tenant(store, parsed_id)
        if task is None:
            logger.info("calendar_sync.%s task_id=%s", SyncOutcome.TASK_NOT_FOUND, task_id)
            return
        bind_user(task.user_id)
        outcome = await engine.sync(task, expected_version=task_version)
        logger.info(
            "calendar_sync.%s task_id=%s version=%d", outcome, task_id, task_version
        )
    except TaskEventTransientError as exc:
        retries_left = job_try < MAX_TRIES
        if retries_left:
            # The task is already `failed`; the next delivery can still succeed.
            delay = DEFAULT_RETRY.delay(job_try).total_seconds()
            logger.warning(
                "calendar_sync.retry_scheduled task_id=%s attempt=%d",
                task_id,
                job_try,
            )
            raise Retry(defer=delay) from exc
        logger.warning(
            "calendar_sync.failed task_id=%s attempt=%d error=%s",
            task_id,
            job_try,
            type(exc).__name__,
        )
    except Exception as exc:
        # Only the type: messages can carry provider or database content. Re-raise so ARQ
        # applies the function's bounded `max_tries`: a transient database failure must not
        # drop the delivery silently, and a permanent one still consumes that budget.
        logger.warning(
            "calendar_sync.unexpected task_id=%s error=%s", task_id, type(exc).__name__
        )
        raise
    finally:
        # The ARQ worker may reuse the context: never leave a tenant behind.
        clear_user()


async def _load_cross_tenant(
    store: SqlTaskEventStore, task_id: uuid.UUID
) -> TaskSnapshot | None:
    token = bind_cross_tenant()
    try:
        return await store.load(task_id)
    finally:
        reset_cross_tenant(token)


def _sessions(ctx: dict[str, Any]) -> SessionFactory:
    factory: SessionFactory = ctx.get(SESSIONS_KEY, session_scope)
    return factory


@dataclass(frozen=True)
class Transports:
    calendar: httpx.BaseTransport | None = None
    token: httpx.AsyncBaseTransport | None = None


def _gateways(ctx: dict[str, Any], sessions: SessionFactory) -> GatewayFactory:
    injected: GatewayFactory | None = ctx.get(GATEWAYS_KEY)
    if injected is not None:
        return injected

    transports = Transports(
        calendar=ctx.get(CALENDAR_TRANSPORT_KEY),
        token=ctx.get(TOKEN_TRANSPORT_KEY),
    )

    async def factory(snapshot: TaskSnapshot) -> CalendarGateway:
        return await _google_gateway(snapshot, sessions, transports)

    return factory


async def _load_credential(
    db: AsyncSession, user_id: uuid.UUID
) -> GoogleCredential | None:
    """Read the credential with a row lock so two deliveries cannot refresh concurrently."""
    result = await db.execute(
        select(GoogleCredential)
        .where(GoogleCredential.user_id == user_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _google_gateway(
    snapshot: TaskSnapshot,
    sessions: SessionFactory,
    transports: Transports,
) -> CalendarGateway:
    access_token: str | None = None
    blocked_code: str | None = None
    transient_code: str | None = None
    async with sessions() as db:
        credential = await _load_credential(db, snapshot.user_id)
        if credential is None or credential.status != "connected":
            raise TaskEventBlocked("google_not_connected")
        try:
            access_token = await fresh_access_token(
                db, credential, transport=transports.token
            )
        except GoogleTokenUnavailable as exc:
            # Missing refresh token or OAuth client: J3.3's reconnect is the way out.
            blocked_code = exc.code
        except GoogleTokenRejected:
            # `fresh_access_token` already marked the credential `disconnected`; swallow the
            # error inside the scope so `session_scope` commits instead of rolling the
            # disconnect back, then surface it after leaving the transaction.
            blocked_code = "google_invalid_grant"
        except GoogleTokenTransient as exc:
            # Nothing was written yet, so ARQ can retry the job without a status change.
            transient_code = exc.code
    if blocked_code is not None:
        raise TaskEventBlocked(blocked_code)
    if transient_code is not None:
        raise TaskEventTransientError(transient_code)
    if access_token is None:  # pragma: no cover - every path above returns or raises
        raise TaskEventTransientError("google_token_missing")

    async def on_invalid_grant() -> None:
        async with sessions() as db:
            await disconnect_google_credential(db, snapshot.user_id)

    async def refresh_access_token() -> str:
        """Reactive path: Google rejected a token that still looked fresh."""
        refreshed: str | None = None
        rejected = False
        unavailable = False
        transient = False
        async with sessions() as db:
            current = await _load_credential(db, snapshot.user_id)
            if current is None or current.status != "connected":
                raise GoogleCalendarUnauthorized("integration is not connected")
            try:
                refreshed = await fresh_access_token(
                    db, current, transport=transports.token, force=True
                )
            except GoogleTokenRejected:
                # Same commit-vs-rollback rule as the initial refresh above.
                rejected = True
            except GoogleTokenUnavailable:
                unavailable = True
            except GoogleTokenTransient:
                transient = True
        if rejected:
            raise GoogleCalendarInvalidGrant(
                "Google revocó el acceso; la integración queda desconectada."
            )
        if unavailable:
            raise GoogleCalendarUnauthorized(
                "La integración necesita reconectarse."
            )
        if transient or refreshed is None:
            raise GoogleCalendarTransientError("No se pudo renovar el token de Google.")
        return refreshed

    service = GoogleCalendarService(
        access_token=access_token,
        on_invalid_grant=on_invalid_grant,
        transport=transports.calendar,
    )
    return GoogleCalendarGateway(service, refresh_access_token=refresh_access_token)
