"""ARQ `sync_task_event(task_id, task_version)` worker (J3.4, I-S3-JF-03).

The job receives only IDs (never task content), resolves the task under the explicit
cross-tenant worker policy, binds its owner for the status write, then delegates to
`TaskEventSync`. The Calendar access token is decrypted in-process and never logged.

Retry budget: one delivery plus `MAX_TRIES - 1` transient retries, using the same bounded
backoff as the outbox. `invalid_grant` disconnects the integration and leaves the task
`blocked` (J3.3 reconnects it); permanent errors leave it `failed` and are not raised.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Final

from arq.worker import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
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
    GoogleCalendarService,
    disconnect_google_credential,
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

# ctx keys for injecting a database or a Calendar gateway. Only tests set them.
SESSIONS_KEY: Final = "calendar_sync_session_factory"
GATEWAYS_KEY: Final = "calendar_sync_gateway_factory"

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


def _gateways(ctx: dict[str, Any], sessions: SessionFactory) -> GatewayFactory:
    injected: GatewayFactory | None = ctx.get(GATEWAYS_KEY)
    if injected is not None:
        return injected

    async def factory(snapshot: TaskSnapshot) -> CalendarGateway:
        return await _google_gateway(snapshot, sessions)

    return factory


async def _google_gateway(
    snapshot: TaskSnapshot, sessions: SessionFactory
) -> CalendarGateway:
    async with sessions() as db:
        credential = await db.get(GoogleCredential, snapshot.user_id)
        if credential is None or credential.status != "connected":
            raise TaskEventBlocked("google_not_connected")
        access_token = decrypt(
            credential.access_token_enc,
            key_version=credential.key_version,
            user_id=snapshot.user_id,
            field="google_credentials.access_token",
        )

    async def on_invalid_grant() -> None:
        async with sessions() as db:
            await disconnect_google_credential(db, snapshot.user_id)

    service = GoogleCalendarService(
        access_token=access_token, on_invalid_grant=on_invalid_grant
    )
    return GoogleCalendarGateway(service)
