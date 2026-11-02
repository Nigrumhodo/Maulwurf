"""Account deletion service (A4.4, I-S4-AN-04).

The operation is intentionally observable and resumable:

1. `create_account_deletion` applies the tombstone in one transaction: it blocks the account
   (sessions, pending jobs, active ingestion attempts) and creates the residual
   `account_deletions` row. The initiating session is kept in restricted mode; every other
   session is revoked. Calling it twice returns the same operation id.
2. `execute_account_deletion` claims the operation atomically (`pending -> running`, with a
   bounded lease) and runs the authorized Google deletions **while the encrypted credential
   still exists**, records each remote id/status/redacted error, and only then purges the
   tenant data, the profile and the tokens. A failure in one remote operation never cancels
   the purge: the operation ends `completed_with_remote_failures` so the user can see why.
   A caller that loses the claim is a no-op; an exception in the purge releases the claim so
   `POST /me/deletion/{id}/resume` can run it again. Running it again after completion is a
   no-op, and already-succeeded remote operations are not sent to Google twice.
3. `purge_expired_residuals` deletes expired `account_deletions` and
   `google_remote_operations` rows in bounded batches. It is the retention promise, not just
   a read filter: the scheduled scheduler job runs it as the cross-tenant jobs role.

No profile, transcript text, prompt or token is copied to the residual rows. The redacted
error summary is a comma-separated list of closed provider codes.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import quote

import httpx
from sqlalchemy import (
    CursorResult,
    Result,
    and_,
    delete,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models import (
    AccountDeletion,
    Audio,
    CalendarEvent,
    CalendarSettings,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    GoogleCredential,
    GoogleRemoteOperation,
    IndexGeneration,
    IngestionAttempt,
    Message,
    MessageSource,
    OutboxEvent,
    ProcessingRun,
    Segment,
    Session,
    Subject,
    Task,
    TaskRevision,
    TaskSource,
    Transcript,
    User,
    VariantConfirmationToken,
)
from app.models.ingestion import ACTIVE_INGESTION_STATES
from app.services.google_calendar import (
    GoogleCalendarError,
    GoogleCalendarInvalidGrant,
    GoogleCalendarNotFound,
    GoogleCalendarService,
)
from app.services.google_tokens import GoogleTokenError, fresh_access_token

logger = logging.getLogger(__name__)

# Residual retention. D8 (A4.5) approved a 30-day maximum for encrypted text/metadata
# backups, so this window is no longer provisional and must stay equal to the backup
# retention in `scripts/ops/backup_restore.sh`. The status route answers 404 after it, and
# the scheduled purge deletes both residual tables once it passes.
RESIDUAL_RETENTION_DAYS = 30
# A live execution keeps this claim; a crash releases it only after the lease, never while
# another caller could still be running the same remote deletes.
EXECUTION_LEASE = timedelta(hours=1)
PURGE_BATCH_SIZE = 500
MAX_ERROR_SUMMARY = 250


@dataclass(frozen=True)
class RemoteTarget:
    kind: str
    calendar_id: str
    event_id: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def _rowcount(result: Result[Any]) -> int:
    # A DML returns `CursorResult` at runtime; the claim/close decisions depend on the rows
    # actually changed, never on the object's in-memory state.
    return int(cast("CursorResult[Any]", result).rowcount or 0)


def _remote_targets(
    task_rows: list[tuple[str | None, str | None]],
    mirror_rows: list[tuple[str, str]],
    write_calendar_id: str | None,
) -> list[RemoteTarget]:
    """Normalize the durable identifiers without ever calling Google.

    Tasks are the authoritative list of events Maulwurf created (J3.4); the mirror adds
    `source='maulwurf'` rows in case a task link was already removed. Blank identities are
    ignored, and the (calendar, event) pair is deduplicated.
    """
    events: set[tuple[str, str]] = set()
    for calendar_id, event_id in task_rows:
        if calendar_id and calendar_id.strip() and event_id and event_id.strip():
            events.add((calendar_id, event_id))
    for calendar_id, event_id in mirror_rows:
        if calendar_id and calendar_id.strip() and event_id and event_id.strip():
            events.add((calendar_id, event_id))

    targets = [
        RemoteTarget("delete_event", calendar_id, event_id)
        for calendar_id, event_id in sorted(events)
    ]
    if write_calendar_id and write_calendar_id.strip():
        # The dedicated secondary calendar is app-owned, so deleting it is the authorized
        # cleanup of our own resource; user-selected calendars are never touched.
        targets.append(RemoteTarget("delete_calendar", write_calendar_id, None))
    return targets


async def create_account_deletion(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    delete_events: bool,
) -> AccountDeletion:
    """Apply the tombstone and return the operation (idempotent per former user)."""
    now = _now()
    user = await db.get(User, user_id)
    if user is None:
        raise not_found()
    if user.status == "active":
        user.status = "deleting"
        user.deleted_at = now

    # Only the initiator survives; it stays in restricted mode through the deletion link.
    await db.execute(
        update(Session)
        .where(
            Session.user_id == user_id,
            Session.id != session_id,
            Session.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    # Block durable work immediately. The purge deletes the rows later; until then no
    # dispatcher may publish one and no late ingestion attempt may commit.
    await db.execute(
        update(OutboxEvent)
        .where(OutboxEvent.user_id == user_id, OutboxEvent.status == "pending")
        .values(status="skipped")
    )
    await db.execute(
        update(ProcessingRun)
        .where(
            ProcessingRun.user_id == user_id,
            ProcessingRun.status.in_(("pending", "running")),
        )
        .values(status="cancelled", finished_at=now)
    )
    await db.execute(
        update(IngestionAttempt)
        .where(
            IngestionAttempt.user_id == user_id,
            IngestionAttempt.status.in_(ACTIVE_INGESTION_STATES),
        )
        .values(
            status="cancelled",
            fencing_token=IngestionAttempt.fencing_token + 1,
            lease_expires_at=None,
        )
    )

    expires_at = now + timedelta(days=RESIDUAL_RETENTION_DAYS)
    inserted = await db.scalar(
        pg_insert(AccountDeletion)
        .values(
            former_user_id=user_id,
            status="pending",
            delete_events=delete_events,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=["former_user_id"])
        .returning(AccountDeletion.id)
    )
    deletion_id = inserted or await db.scalar(
        select(AccountDeletion.id).where(AccountDeletion.former_user_id == user_id)
    )
    if deletion_id is None:  # pragma: no cover - the unique insert guarantees a row
        raise RuntimeError("account deletion could not be created")

    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.user_id == user_id)
        .values(deletion_operation_id=deletion_id)
    )
    deletion = await db.get(AccountDeletion, deletion_id)
    if deletion is None:  # pragma: no cover - the insert above returned this id
        raise RuntimeError("account deletion disappeared")
    return deletion


async def execute_account_deletion(
    db: AsyncSession,
    deletion_id: uuid.UUID,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Claim, run the remote step and purge, then close the operation; safe to call twice.

    Only the caller that wins the atomic `pending -> running` claim performs the remote
    deletes and the purge. Any other caller returns the current status, so a double click or
    a concurrent retry cannot duplicate Google requests or corrupt `attempts`.
    """
    claim = await _claim_operation(db, deletion_id)
    if claim is None:
        return await _current_status(db, deletion_id)
    former_user_id, delete_events, claimed_at, expires_at = claim

    try:
        if delete_events:
            await _execute_remote(
                db,
                user_id=former_user_id,
                transport=transport,
                expires_at=expires_at,
            )

        await _purge_user_data(db, user_id=former_user_id, deletion_id=deletion_id)
        error_codes = (
            await db.scalars(
                select(GoogleRemoteOperation.error_code)
                .where(
                    GoogleRemoteOperation.user_id == former_user_id,
                    GoogleRemoteOperation.status == "failed",
                    GoogleRemoteOperation.error_code.is_not(None),
                )
                .distinct()
            )
        ).all()
        errors = sorted({str(code) for code in error_codes if code})
        status = "completed_with_remote_failures" if errors else "completed"
        closed = await db.execute(
            update(AccountDeletion)
            .where(
                AccountDeletion.id == deletion_id,
                AccountDeletion.status == "running",
                # Fencing: never close a lease that another caller has since reclaimed.
                AccountDeletion.claimed_at == claimed_at,
            )
            .values(
                status=status,
                completed_at=_now(),
                claimed_at=None,
                error_summary=", ".join(errors)[:MAX_ERROR_SUMMARY] if errors else None,
            )
        )
        await db.commit()
        if _rowcount(closed) != 1:
            return await _current_status(db, deletion_id)
        return status
    except Exception:
        # The 202 contract already accepted the deletion. Release the claim so the restricted
        # session can resume immediately; if this release fails, the lease allows a retry.
        await _release_claim(db, deletion_id, claimed_at=claimed_at)
        raise


async def _claim_operation(
    db: AsyncSession, deletion_id: uuid.UUID
) -> tuple[uuid.UUID, bool, datetime, datetime] | None:
    """Atomically take ownership of a pending or lease-expired operation.

    The `UPDATE ... RETURNING` both serializes concurrent callers and releases the row lock
    at the commit, before any network call. `claimed_at` is the fence the caller must present
    when closing or releasing the operation.
    """
    now = _now()
    claim = (
        await db.execute(
            update(AccountDeletion)
            .where(
                AccountDeletion.id == deletion_id,
                AccountDeletion.expires_at > now,
                or_(
                    AccountDeletion.status == "pending",
                    and_(
                        AccountDeletion.status == "running",
                        or_(
                            AccountDeletion.claimed_at.is_(None),
                            AccountDeletion.claimed_at <= now - EXECUTION_LEASE,
                        ),
                    ),
                ),
            )
            .values(
                status="running",
                claimed_at=now,
                attempts=AccountDeletion.attempts + 1,
            )
            .returning(
                AccountDeletion.former_user_id,
                AccountDeletion.delete_events,
                AccountDeletion.claimed_at,
                AccountDeletion.expires_at,
            )
        )
    ).one_or_none()
    # The lock is held until commit; drop it before the remote step so other callers can
    # observe `running` instead of blocking on the row.
    await db.commit()
    if claim is None:
        return None
    return (
        claim.former_user_id,
        claim.delete_events,
        claim.claimed_at,
        claim.expires_at,
    )


async def _current_status(db: AsyncSession, deletion_id: uuid.UUID) -> str:
    status = await db.scalar(
        select(AccountDeletion.status).where(AccountDeletion.id == deletion_id)
    )
    if status is None:
        raise not_found()
    return str(status)


async def _release_claim(
    db: AsyncSession, deletion_id: uuid.UUID, *, claimed_at: datetime
) -> None:
    """Best-effort `running -> pending`, fenced by the caller's claim; then re-raise."""
    try:
        await db.rollback()
    except Exception as exc:
        logger.warning(
            "account_deletion.rollback_failed deletion_id=%s error=%s",
            deletion_id,
            type(exc).__name__,
        )
    try:
        await db.execute(
            update(AccountDeletion)
            .where(
                AccountDeletion.id == deletion_id,
                AccountDeletion.status == "running",
                AccountDeletion.claimed_at == claimed_at,
            )
            .values(status="pending", claimed_at=None)
        )
        await db.commit()
    except Exception as exc:
        # The lease still allows a later resume; do not mask the original failure.
        logger.warning(
            "account_deletion.claim_release_failed deletion_id=%s error=%s",
            deletion_id,
            type(exc).__name__,
        )
        await db.rollback()


async def purge_expired_residuals(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = PURGE_BATCH_SIZE,
) -> dict[str, int]:
    """Delete expired residual rows in bounded batches; idempotent.

    Both tables are purged: `account_deletions` (former opaque id, status, expiry) and
    `google_remote_operations` (remote calendar/event ids). The caller owns the transaction;
    the scheduler runs it hourly as `maulwurf_jobs` with `app.cross_tenant = on`, because
    forced RLS otherwise limits the job to one tenant.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    clock = now or _now()

    expired_operations = (
        select(GoogleRemoteOperation.id)
        .where(GoogleRemoteOperation.expires_at <= clock)
        .order_by(GoogleRemoteOperation.expires_at, GoogleRemoteOperation.id)
        .limit(limit)
    )
    operations = await db.execute(
        delete(GoogleRemoteOperation).where(
            GoogleRemoteOperation.id.in_(expired_operations)
        )
    )

    expired_deletions = (
        select(AccountDeletion.id)
        .where(AccountDeletion.expires_at <= clock)
        .order_by(AccountDeletion.expires_at, AccountDeletion.id)
        .limit(limit)
    )
    deletions = await db.execute(
        delete(AccountDeletion).where(AccountDeletion.id.in_(expired_deletions))
    )

    return {
        "google_remote_operations": _rowcount(operations),
        "account_deletions": _rowcount(deletions),
    }


async def get_account_deletion(
    db: AsyncSession,
    *,
    deletion_id: uuid.UUID,
    user_id: uuid.UUID,
    session_deletion_id: uuid.UUID | None,
) -> AccountDeletion:
    """Own operation only; a restricted session may read exactly its own operation."""
    operation = await db.scalar(
        select(AccountDeletion).where(
            AccountDeletion.id == deletion_id,
            AccountDeletion.former_user_id == user_id,
        )
    )
    if (
        operation is None
        or operation.expires_at <= _now()
        or (session_deletion_id is not None and session_deletion_id != deletion_id)
    ):
        raise not_found()
    return operation


async def _execute_remote(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transport: httpx.BaseTransport | None,
    expires_at: datetime,
) -> None:
    """Delete the authorized Google resources; record every outcome in the residual table."""
    task_rows = [
        (row[0], row[1])
        for row in (
            await db.execute(
                select(Task.calendar_id, Task.calendar_event_id).where(
                    Task.user_id == user_id, Task.calendar_event_id.is_not(None)
                )
            )
        ).all()
    ]
    mirror_rows = [
        (row[0], row[1])
        for row in (
            await db.execute(
                select(CalendarEvent.calendar_id, CalendarEvent.event_id).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.source == "maulwurf",
                )
            )
        ).all()
    ]
    write_calendar_id = await db.scalar(
        select(CalendarSettings.calendar_write_calendar_id).where(
            CalendarSettings.user_id == user_id
        )
    )
    targets = _remote_targets(task_rows, mirror_rows, write_calendar_id)

    credential = await db.scalar(
        select(GoogleCredential).where(GoogleCredential.user_id == user_id)
    )
    access_token: str | None = None
    credential_error: str | None = None
    if credential is None or credential.status != "connected":
        credential_error = "google_not_connected"
    else:
        try:
            access_token = await fresh_access_token(db, credential)
        except GoogleTokenError as exc:
            credential_error = exc.code
        except Exception:
            # A rotated/missing encryption key must fail closed and stay observable; the
            # exception text can contain crypto diagnostics, so only the code is persisted.
            credential_error = "credential_unreadable"

    if not targets:
        await db.commit()
        return

    operations: list[GoogleRemoteOperation] = []
    for target in targets:
        operation = await _ensure_operation(
            db,
            user_id=user_id,
            kind=target.kind,
            calendar_id=target.calendar_id,
            event_id=target.event_id,
            expires_at=expires_at,
        )
        operations.append(operation)
    operation_ids = [operation.id for operation in operations]
    # Commit the pending rows before the network: a crash after a remote delete leaves an
    # observable pending operation for the next run instead of losing the trace.
    await db.commit()

    # The caller's session may expire ORM objects on commit (the test fixture does). Re-read
    # the rows by id instead of touching expired attributes outside an await.
    operations = list(
        (
            await db.scalars(
                select(GoogleRemoteOperation).where(
                    GoogleRemoteOperation.id.in_(operation_ids)
                )
            )
        ).all()
    )

    if credential_error is not None:
        for operation in operations:
            if operation.status == "succeeded":
                continue
            operation.status = "failed"
            operation.error_code = credential_error
            operation.finished_at = _now()
        await db.commit()
        return

    if access_token is None:
        # `fresh_access_token` either returns a token or raises; this is a fail-closed guard
        # for a future adapter change, never a path that calls Google without a token.
        for operation in operations:
            if operation.status == "succeeded":
                continue
            operation.status = "failed"
            operation.error_code = "credential_unreadable"
            operation.finished_at = _now()
        await db.commit()
        return

    service = GoogleCalendarService(access_token=access_token, transport=transport)
    invalid_grant = False
    for operation in operations:
        # A resumed operation must not repeat an already-acknowledged remote DELETE.
        if operation.status == "succeeded":
            continue
        if invalid_grant:
            operation.status = "failed"
            operation.error_code = "google_calendar_invalid_grant"
            operation.finished_at = _now()
            continue
        operation.attempts += 1
        try:
            if operation.kind == "delete_event":
                path = (
                    f"calendars/{quote(operation.calendar_id or '', safe='')}"
                    f"/events/{quote(operation.event_id or '', safe='')}"
                )
                await service.request("DELETE", path, params={"sendUpdates": "none"})
            else:
                path = f"calendars/{quote(operation.calendar_id or '', safe='')}"
                await service.request("DELETE", path)
        except GoogleCalendarNotFound:
            # Already absent is the desired end state; DELETE is idempotent.
            operation.status = "succeeded"
        except GoogleCalendarInvalidGrant as exc:
            operation.status = "failed"
            operation.error_code = exc.code
            invalid_grant = True
        except GoogleCalendarError as exc:
            operation.status = "failed"
            operation.error_code = exc.code
        except Exception:
            operation.status = "failed"
            operation.error_code = "google_request_failed"
        else:
            operation.status = "succeeded"
        operation.finished_at = _now()
    await db.commit()


async def _ensure_operation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    calendar_id: str,
    event_id: str | None,
    expires_at: datetime,
) -> GoogleRemoteOperation:
    inserted = await db.scalar(
        pg_insert(GoogleRemoteOperation)
        .values(
            user_id=user_id,
            kind=kind,
            calendar_id=calendar_id,
            event_id=event_id,
            authorized=True,
            status="pending",
            # Same window as the operation that authorized this remote id.
            expires_at=expires_at,
        )
        # A concurrent retry may have created the same identity row; the unique constraint
        # with NULLS NOT DISTINCT makes "insert or reuse" atomic instead of a race.
        .on_conflict_do_nothing()
        .returning(GoogleRemoteOperation.id)
    )
    operation_id = inserted or await db.scalar(
        select(GoogleRemoteOperation.id).where(
            GoogleRemoteOperation.user_id == user_id,
            GoogleRemoteOperation.kind == kind,
            GoogleRemoteOperation.calendar_id == calendar_id,
            GoogleRemoteOperation.event_id == event_id,
        )
    )
    if operation_id is None:  # pragma: no cover - the unique insert guarantees a row
        raise RuntimeError("google remote operation could not be created")
    operation = await db.get(GoogleRemoteOperation, operation_id)
    if operation is None:  # pragma: no cover - the insert above returned this id
        raise RuntimeError("google remote operation disappeared")
    return operation


async def _purge_user_data(
    db: AsyncSession, *, user_id: uuid.UUID, deletion_id: uuid.UUID
) -> None:
    """Delete derived data in FK order, then scrub the profile and remove the tokens."""
    for model in (
        MessageSource,
        Message,
        Conversation,
        TaskRevision,
        TaskSource,
        CalendarEvent,
        Task,
        ChunkSegment,
        Embedding,
        Chunk,
        Segment,
        Transcript,
        ProcessingRun,
        VariantConfirmationToken,
        IngestionAttempt,
        Audio,
        IndexGeneration,
        Subject,
        OutboxEvent,
        CalendarSettings,
        GoogleCredential,
    ):
        await db.execute(delete(model).where(model.user_id == user_id))

    # Keep only the restricted initiator; every other session (including revoked ones) is
    # deleted so no session hash outlives the account.
    await db.execute(
        delete(Session).where(
            Session.user_id == user_id,
            or_(
                Session.deletion_operation_id.is_(None),
                Session.deletion_operation_id != deletion_id,
            ),
        )
    )

    # The soft-deleted user row is the tombstone the restricted session still needs. It
    # keeps no profile: the identity and email become opaque, unique placeholders so a new
    # Google login creates a fresh account instead of reviving this one.
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            status="deleted",
            deleted_at=_now(),
            email=f"deleted-{user_id}@invalid",
            google_sub=f"deleted:{user_id}",
            email_verified=False,
            display_name=None,
            timezone="UTC",
        )
    )
