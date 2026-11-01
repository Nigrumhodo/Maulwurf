"""Explicit cancellation of an ingestion attempt (S2.8).

Disconnecting the upload body cancels reception. An explicit cancel, which the
API records by bumping the fencing token and clearing the lease, makes the
supervisor lose its heartbeat. Cleanup evidence can still be written: the token
no longer matches, but the row is ``cancelled`` and this instance still owns it.
That write does not publish a transcript and does not enable the outbox.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from maulwurf_ingest import cleanup, rls
from maulwurf_ingest.lease import Lease

logger = logging.getLogger(__name__)


class ReceptionInterrupted(Exception):
    """The client dropped the body before reception finished."""


async def invalidate_reception(pool: asyncpg.Pool, lease: Lease) -> bool:
    """Cancel a reception this lease still owns. One statement, token included.

    Only ``receiving`` matches. A commit that already moved the attempt on is
    left untouched, so a late disconnect cannot destroy a transcript.
    """
    status = await rls.execute(pool, lease.user_id,
        """
        UPDATE ingestion_attempts
           SET status = 'cancelled', fencing_token = fencing_token + 1,
               lease_expires_at = NULL, updated_at = now()
         WHERE id = $1 AND user_id = $2 AND fencing_token = $3
           AND status = 'receiving' AND lease_expires_at > now()
        """,
        lease.attempt_id, lease.user_id, lease.fencing_token,
    )
    return bool(status == "UPDATE 1")


async def is_cancelled(pool: asyncpg.Pool, lease: Lease) -> bool:
    status = await rls.fetchval(pool, lease.user_id,
        "SELECT status FROM ingestion_attempts WHERE id = $1 AND user_id = $2",
        lease.attempt_id, lease.user_id,
    )
    return isinstance(status, str) and status == "cancelled"


async def record_cancelled_cleanup(
    pool: asyncpg.Pool, lease: Lease, *, verified: bool, evidence: dict[str, object],
) -> bool:
    """Record cleanup after the fencing token was invalidated.

    The owner instance must still be this lease. A newer owner, or an attempt
    that is not ``cancelled``, is not credited. As in ``lease.record_cleanup``,
    ``verified`` requires proof that no descriptor, process or mount survives;
    ``audio_deleted_at`` is never stamped from a bare claim (S4.2).
    """
    if verified and not cleanup.absence_verified(evidence):
        logger.error(
            "cancel.cleanup_evidence_incomplete attempt_id=%s", lease.attempt_id,
        )
        return False
    status = await rls.execute(pool, lease.user_id,
        """
        UPDATE ingestion_attempts
           SET cleanup_status = $4::text, cleanup_evidence = $5::jsonb,
               cleanup_verified_at = CASE WHEN $4 = 'verified' THEN now() END,
               audio_deleted_at = CASE WHEN $4 = 'verified' THEN now() END,
               updated_at = now()
         WHERE id = $1 AND user_id = $2 AND status = 'cancelled'
           AND owner_instance = $3 AND cleanup_status = 'pending'
        """,
        lease.attempt_id, lease.user_id, lease.instance,
        "verified" if verified else "failed", json.dumps(evidence),
    )
    return bool(status == "UPDATE 1")
