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
        # Only the type: messages can carry provider or database content.
        logger.warning(
            "calendar_sync.failed task_id=%s error=%s", task_id, type(exc).__name__
        )
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
            raise TaskEventBlocked(exc.code) from exc
        except GoogleTokenRejected as exc:
            # The integration is already `disconnected` inside `fresh_access_token`.
            raise TaskEventBlocked("google_invalid_grant") from exc
        except GoogleTokenTransient as exc:
            # Nothing was written yet, so ARQ can retry the job without a status change.
            raise TaskEventTransientError(exc.code) from exc

    async def on_invalid_grant() -> None:
        async with sessions() as db:
            await disconnect_google_credential(db, snapshot.user_id)

    async def refresh_access_token() -> str:
        """Reactive path: Google rejected a token that still looked fresh."""
        async with sessions() as db:
            current = await _load_credential(db, snapshot.user_id)
            if current is None or current.status != "connected":
                raise GoogleCalendarUnauthorized("integration is not connected")
            try:
                return await fresh_access_token(
                    db, current, transport=transports.token, force=True
                )
            except GoogleTokenRejected as exc:
                raise GoogleCalendarInvalidGrant(
                    "Google revocó el acceso; la integración queda desconectada."
                ) from exc
            except GoogleTokenUnavailable as exc:
                raise GoogleCalendarUnauthorized(
                    "La integración necesita reconectarse."
                ) from exc
            except GoogleTokenTransient as exc:
                raise GoogleCalendarTransientError(
                    "No se pudo renovar el token de Google."
                ) from exc

    service = GoogleCalendarService(
        access_token=access_token,
        on_invalid_grant=on_invalid_grant,
        transport=transports.calendar,
    )
    return GoogleCalendarGateway(service, refresh_access_token=refresh_access_token)
