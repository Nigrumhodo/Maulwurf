"""I-S2-SG-08/09: a dead post-commit owner is recovered without retranscribing."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import asyncpg
import pytest
from test_commit import _commit, _draft, _ready  # noqa: E402
from test_supervisor import PROVEN_CLEANUP, _seed, tmpfs_dir  # noqa: E402

from maulwurf_ingest import admission, cleanup, persistence
from maulwurf_ingest import lease as leases
from maulwurf_ingest.lease import reclaim_committed
from maulwurf_ingest.persistence import CommitOutcome, enable_text_outbox
from maulwurf_ingest.reconciler import (
    ReconcileAction,
    ReconcileResult,
    reconcile_committed,
    sweep_stranded,
)
from maulwurf_ingest.supervisor import sweep_once

# Re-export the tmpfs fixture so the directory case can collect it.
_ = (tmpfs_dir,)

pytestmark = pytest.mark.integration

_INSTANCE = "ingest:reconcile"


@pytest.fixture(autouse=True)
def _reset_admission() -> Iterator[None]:
    admission.reset()
    yield
    admission.reset()


async def _expire(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt_id,
    )


async def _attempt_row(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """SELECT status, cleanup_status, fencing_token
             FROM ingestion_attempts WHERE id = $1""",
        attempt_id,
    )
    assert row is not None
    return row


async def _outbox(pool: asyncpg.Pool, audio_id: uuid.UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT enabled, blocked_reason FROM outbox_events WHERE resource_id = $1",
        audio_id,
    )
    assert row is not None
    return row


async def _transcripts(pool: asyncpg.Pool, audio_id: uuid.UUID) -> int:
    count = await pool.fetchval(
        "SELECT count(*) FROM transcripts WHERE audio_id = $1", audio_id,
    )
    assert isinstance(count, int)
    return count


async def test_expired_or_stale_token_does_not_publish(pool: asyncpg.Pool) -> None:
    """I-S2-SG-09: an expired owner and a stale token cannot enable the outbox."""
    _attempt, owned, audio_id = await _ready(pool)
    published = await _commit(pool, owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    assert published.version is not None
    await _expire(pool, owned.attempt_id)

    assert await enable_text_outbox(
        pool, owned, audio_id=audio_id, version=published.version,
    ) is False
    late = await _commit(pool, owned, _draft())
    assert late.outcome is CommitOutcome.NOT_OWNER

    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() + interval '1 minute'"
        " WHERE id = $1",
        owned.attempt_id,
    )
    stale = replace(owned, fencing_token=owned.fencing_token + 9)
    assert await enable_text_outbox(
        pool, stale, audio_id=audio_id, version=published.version,
    ) is False
    assert (await _outbox(pool, audio_id))["enabled"] is False

    await _expire(pool, owned.attempt_id)
    await pool.execute(
        """UPDATE ingestion_attempts
              SET cleanup_status = 'verified', cleanup_verified_at = now(),
                  audio_deleted_at = now()
            WHERE id = $1""",
        owned.attempt_id,
    )
    reclaimed = await reclaim_committed(
        pool, user_id=owned.user_id, attempt_id=owned.attempt_id, instance=_INSTANCE,
        ttl=owned.ttl,
    )
    assert reclaimed is not None
    assert reclaimed.fencing_token == owned.fencing_token + 1
    assert await enable_text_outbox(
        pool, owned, audio_id=audio_id, version=published.version,
    ) is False
    assert await enable_text_outbox(
        pool, reclaimed, audio_id=audio_id, version=published.version,
    ) is True
    assert await _transcripts(pool, audio_id) == 1
    row = await _attempt_row(pool, owned.attempt_id)
    assert row["status"] == "succeeded"
    assert (await _outbox(pool, audio_id))["enabled"] is True


async def test_verified_cleanup_enables_the_outbox_without_a_directory(
    pool: asyncpg.Pool,
) -> None:
    """I-S2-SG-08: cleanup already verified, process died before enable."""
    attempt, _owned, audio_id = await _ready(pool)
    published = await _commit(pool, _owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    await pool.execute(
        """UPDATE ingestion_attempts
              SET cleanup_status = 'verified', cleanup_verified_at = now(),
                  audio_deleted_at = now(),
                  lease_expires_at = now() - interval '1 second'
            WHERE id = $1""",
        attempt.attempt_id,
    )

    results = await reconcile_committed(pool, instance=_INSTANCE, tmp_dir=None)
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.ENABLED)]
    row = await _attempt_row(pool, attempt.attempt_id)
    assert row["status"] == "succeeded"
    assert row["cleanup_status"] == "verified"
    assert await _transcripts(pool, audio_id) == 1
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is True
    assert event["blocked_reason"] is None


async def test_missing_directory_stays_pending_and_does_not_retranscribe(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-S2-SG-08: no local files and no verification. The text stays unpublished."""
    attempt, _owned, audio_id = await _ready(pool)
    assert (await _commit(pool, _owned, _draft())).outcome is CommitOutcome.COMMITTED
    token = (await _attempt_row(pool, attempt.attempt_id))["fencing_token"]
    await _expire(pool, attempt.attempt_id)

    async def trap(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retranscribe")

    monkeypatch.setattr(persistence, "commit_transcript", trap)
    results = await reconcile_committed(pool, instance=_INSTANCE)
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.HELD)]
    row = await _attempt_row(pool, attempt.attempt_id)
    assert row["status"] == "transcript_committed_cleanup_pending"
    assert row["cleanup_status"] == "pending"
    assert row["fencing_token"] == token
    assert await _transcripts(pool, audio_id) == 1
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is False
    assert event["blocked_reason"] == "cleanup_pending"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Open contract question (I-S2-JF-05d): no safe resolution exists without a change to "
        "ESPECIFICACION M3/S1.B6. An absent directory in this namespace does not prove the "
        "previous tmpfs was destroyed, so the attempt stays HELD and the outbox stays disabled."
    ),
)
async def test_held_attempt_is_enabled_by_a_new_owner_after_verified_cleanup(
    pool: asyncpg.Pool,
) -> None:
    """Desired end state if the contract allowed it. Strict xfail: HELD is correct today."""
    attempt, owned, audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    await _expire(pool, attempt.attempt_id)

    results = await reconcile_committed(pool, instance=_INSTANCE)
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.ENABLED)]
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is True

async def test_failed_cleanup_is_not_retried(pool: asyncpg.Pool) -> None:
    attempt, owned, audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    token = owned.fencing_token
    await pool.execute(
        """UPDATE ingestion_attempts
              SET cleanup_status = 'failed',
                  lease_expires_at = now() - interval '1 second'
            WHERE id = $1""",
        attempt.attempt_id,
    )

    results = await reconcile_committed(pool, instance=_INSTANCE)
    assert attempt.attempt_id not in {item.attempt_id for item in results}

    row = await _attempt_row(pool, attempt.attempt_id)
    assert row["status"] == "transcript_committed_cleanup_pending"
    assert row["cleanup_status"] == "failed"
    assert row["fencing_token"] == token
    assert (await _outbox(pool, audio_id))["enabled"] is False


async def test_live_lease_is_not_stolen(pool: asyncpg.Pool, tmpfs_dir: Path) -> None:
    attempt, owned, _audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    workdir = cleanup.create_workdir(str(attempt.attempt_id), str(tmpfs_dir))
    cleanup.write_private(workdir / "original", b"RIFF" + b"\x00" * 8)
    try:
        results = await reconcile_committed(
            pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir),
        )
        assert attempt.attempt_id not in {item.attempt_id for item in results}
        row = await _attempt_row(pool, attempt.attempt_id)
        assert row["fencing_token"] == owned.fencing_token
        assert row["status"] == "transcript_committed_cleanup_pending"
        assert workdir.exists()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def test_leftover_directory_is_verified_and_enabled_without_asr(
    pool: asyncpg.Pool, tmpfs_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-S2-SG-08: the process died after the commit, with audio still on tmpfs."""
    # On a multi-UID host an unreadable /proc/<pid>/fd makes cleanup fail closed by design
    # (S1.B4); the success path needs the single-UID environment used by CI (`sudo`).
    if cleanup.open_descriptors_under(tmpfs_dir)[1]:
        pytest.skip("multi-UID /proc: cleanup is fail-closed; run the ingest suite as root")
    attempt, owned, audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    workdir = cleanup.create_workdir(str(attempt.attempt_id), str(tmpfs_dir))
    cleanup.write_private(workdir / "original", b"RIFF" + b"\x00" * 8)
    await _expire(pool, attempt.attempt_id)

    async def trap(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("retranscribe")

    monkeypatch.setattr(persistence, "commit_transcript", trap)
    results = await reconcile_committed(pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir))
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.ENABLED)]
    assert not workdir.exists()
    assert await _transcripts(pool, audio_id) == 1
    row = await _attempt_row(pool, attempt.attempt_id)
    assert row["status"] == "succeeded"
    assert row["cleanup_status"] == "verified"
    assert row["fencing_token"] == owned.fencing_token + 1
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is True
    assert event["blocked_reason"] is None


async def _status_and_token(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        """SELECT status, error_code, cleanup_status, fencing_token
             FROM ingestion_attempts WHERE id = $1""",
        attempt_id,
    )
    assert row is not None
    return row


@pytest.mark.parametrize("status", ["receiving", "transcribing"])
async def test_expired_stranded_attempt_ends_requires_reupload(
    pool: asyncpg.Pool, status: str,
) -> None:
    """I-S2-SG-08: a dead owner with no committed text is ended and fenced, not retranscribed."""
    attempt, owned, audio_id = await _ready(pool)
    await pool.execute(
        "UPDATE ingestion_attempts SET status = $2 WHERE id = $1", attempt.attempt_id, status,
    )
    await _expire(pool, attempt.attempt_id)

    ended = await sweep_stranded(pool)

    assert attempt.attempt_id in ended
    row = await _status_and_token(pool, attempt.attempt_id)
    assert (row["status"], row["error_code"]) == ("requires_reupload", "technical_loss")
    assert row["fencing_token"] == owned.fencing_token + 1
    assert row["cleanup_status"] == "pending"
    assert await _transcripts(pool, audio_id) == 0


async def test_live_lease_is_not_touched(pool: asyncpg.Pool) -> None:
    attempt, owned, _audio_id = await _ready(pool)

    ended = await sweep_stranded(pool)

    assert attempt.attempt_id not in ended
    row = await _status_and_token(pool, attempt.attempt_id)
    assert (row["status"], row["fencing_token"]) == ("transcribing", owned.fencing_token)


async def test_committed_attempt_is_not_touched(pool: asyncpg.Pool) -> None:
    attempt, owned, _audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    await _expire(pool, attempt.attempt_id)

    ended = await sweep_stranded(pool)

    assert attempt.attempt_id not in ended
    row = await _status_and_token(pool, attempt.attempt_id)
    assert row["status"] == "transcript_committed_cleanup_pending"
    assert row["cleanup_status"] == "pending"
    assert row["fencing_token"] == owned.fencing_token


async def test_awaiting_upload_is_not_swept(pool: asyncpg.Pool) -> None:
    """A2.14 owns awaiting_upload; this sweep never selects it."""
    attempt = await _seed(pool)
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt.attempt_id,
    )

    ended = await sweep_stranded(pool)

    assert attempt.attempt_id not in ended
    row = await _status_and_token(pool, attempt.attempt_id)
    assert row["status"] == "awaiting_upload"


async def test_old_token_cannot_write_after_the_sweep(pool: asyncpg.Pool) -> None:
    attempt, owned, _audio_id = await _ready(pool)
    await _expire(pool, attempt.attempt_id)
    await sweep_stranded(pool)
    # Simulate a later owner that revived the row, so only the token can reject the zombie.
    await pool.execute(
        "UPDATE ingestion_attempts SET status = 'transcribing',"
        " lease_expires_at = now() + interval '1 minute' WHERE id = $1",
        attempt.attempt_id,
    )

    assert await leases.transition(
        pool, owned, from_status="transcribing", to_status="succeeded",
    ) is False
    assert await leases.heartbeat(pool, owned) is False
    assert await leases.record_cleanup(
        pool, owned, verified=True, evidence=PROVEN_CLEANUP,
    ) is False
    row = await _status_and_token(pool, attempt.attempt_id)
    assert row["status"] == "transcribing"
    assert row["cleanup_status"] == "pending"


async def test_second_sweep_leaves_the_result_unchanged(pool: asyncpg.Pool) -> None:
    attempt, owned, _audio_id = await _ready(pool)
    await _expire(pool, attempt.attempt_id)
    first = await sweep_stranded(pool)
    before = await _status_and_token(pool, attempt.attempt_id)

    second = await sweep_stranded(pool)

    assert attempt.attempt_id in first
    assert attempt.attempt_id not in second
    after = await _status_and_token(pool, attempt.attempt_id)
    assert after == before
    assert after["fencing_token"] == owned.fencing_token + 1


async def test_sweep_once_composes_post_commit_and_stranded_recovery(
    pool: asyncpg.Pool, tmpfs_dir: Path,
) -> None:
    """S4.2: the periodic pass runs the reconciler and the stranded sweep together."""
    if cleanup.open_descriptors_under(tmpfs_dir)[1]:
        pytest.skip("multi-UID /proc: cleanup is fail-closed; run the ingest suite as root")
    committed, owned, _audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    workdir = cleanup.create_workdir(str(committed.attempt_id), str(tmpfs_dir))
    cleanup.write_private(workdir / "original", b"RIFF" + b"\x00" * 8)
    await _expire(pool, committed.attempt_id)

    stranded = await _seed(pool)
    await pool.execute(
        "UPDATE ingestion_attempts SET status = 'transcribing' WHERE id = $1",
        stranded.attempt_id,
    )
    await _expire(pool, stranded.attempt_id)

    result = await sweep_once(pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir))

    assert committed.attempt_id in {item.attempt_id for item in result.committed}
    assert stranded.attempt_id in result.stranded
    assert not workdir.exists()
    assert (await _attempt_row(pool, stranded.attempt_id))["status"] == "requires_reupload"


async def test_cleanup_exception_is_recorded_failed_and_halts_admission(
    pool: asyncpg.Pool, tmpfs_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4.2: an exception during recovery cleanup is not a silent retry."""
    if cleanup.open_descriptors_under(tmpfs_dir)[1]:
        pytest.skip("multi-UID /proc: cleanup is fail-closed; run the ingest suite as root")
    attempt, owned, audio_id = await _ready(pool)
    assert (await _commit(pool, owned, _draft())).outcome is CommitOutcome.COMMITTED
    workdir = cleanup.create_workdir(str(attempt.attempt_id), str(tmpfs_dir))
    cleanup.write_private(workdir / "original", b"RIFF" + b"\x00" * 8)
    await _expire(pool, attempt.attempt_id)

    def boom(*_args: object, **_kwargs: object) -> cleanup.CleanupEvidence:
        raise OSError("rmtree")

    monkeypatch.setattr(cleanup, "destroy_and_verify", boom)
    try:
        results = await reconcile_committed(pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir))

        mine = [item for item in results if item.attempt_id == attempt.attempt_id]
        assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.CLEANUP_FAILED)]
        row = await _attempt_row(pool, attempt.attempt_id)
        assert row["cleanup_status"] == "failed"
        assert (await _outbox(pool, audio_id))["enabled"] is False
        assert admission.uploads_admitted() is False
        assert admission.alerts() == (
            admission.Alert("cleanup_failed", attempt.attempt_id),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
