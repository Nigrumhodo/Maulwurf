"""I-S2-JF-05 (J2.7): crashes injected before and after the atomic commit.

Each crash is a monkeypatch at the internal boundary the scenario names, so every run is
deterministic: no sleeps, no signals. The expected results follow S2.5-S2.7 and the
outcome table in docs/plan/S2.md (section 4): a technical failure before the commit ends
``requires_reupload`` with ``supervisor_error``, and an expired lease never marks cleanup
verified or publishes the transcript.
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    SYNTHETIC_TEXT,
    Attempt,
    _config,
    _row,
    _seed,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest import admission, cleanup, persistence, supervisor
from maulwurf_ingest import lease as leases
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.reconciler import ReconcileAction, ReconcileResult, reconcile_committed
from maulwurf_ingest.supervisor import Outcome, run_attempt

_ = (audio, tmpfs_dir)  # fixtures re-exported so this module collects them

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible"),
]

_INSTANCE = "ingest:fault"
_SUCCESSOR = "ingest:successor"
_LEASE_TTL = timedelta(seconds=30)


class _SimulatedProcessDeath(BaseException):
    """Stands in for SIGKILL: a BaseException, so no supervisor ``except`` can absorb it."""


async def _expire(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt_id,
    )


async def _audio_id(pool: asyncpg.Pool, attempt: Attempt) -> uuid.UUID:
    value = await pool.fetchval(
        "SELECT audio_id FROM ingestion_attempts WHERE id = $1", attempt.attempt_id,
    )
    assert isinstance(value, uuid.UUID)
    return value


async def _publication_state(
    pool: asyncpg.Pool, audio_id: uuid.UUID,
) -> tuple[int, int, object]:
    transcripts = await pool.fetchval(
        "SELECT count(*) FROM transcripts WHERE audio_id = $1", audio_id,
    )
    events = await pool.fetchval(
        "SELECT count(*) FROM outbox_events WHERE resource_id = $1", audio_id,
    )
    active = await pool.fetchval(
        "SELECT active_transcript_version FROM audios WHERE id = $1", audio_id,
    )
    assert isinstance(transcripts, int) and isinstance(events, int)
    return transcripts, events, active


async def _outbox_rows(pool: asyncpg.Pool, audio_id: uuid.UUID) -> list[asyncpg.Record]:
    return list(await pool.fetch(
        "SELECT type, enabled, blocked_reason, status, published_at"
        "  FROM outbox_events WHERE resource_id = $1",
        audio_id,
    ))


async def test_pre_commit_crash_publishes_nothing(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-S2-JF-05(a): a crash after the transcript and segment inserts rolls both back."""
    admission.reset()
    attempt = await _seed(pool)
    workdirs: list[Path] = []

    def crash_before_outbox(_raw: object) -> tuple[str, ...]:
        # Runs after the transcript and segments are inserted, before any outbox row.
        raise RuntimeError("injected crash before the outbox insert")

    monkeypatch.setattr(persistence, "_required_stages", crash_before_outbox)
    try:
        result = await run_attempt(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
            audio=iter_bytes(audio),
            config=_config(tmpfs_dir, stub_transcript=SYNTHETIC_TEXT),
            on_child=lambda _pid, workdir: workdirs.append(workdir),
        )
    finally:
        admission.reset()

    row = await _row(pool, attempt)
    transcripts, events, active = await _publication_state(pool, await _audio_id(pool, attempt))
    assert result.outcome is Outcome.SUPERVISOR_ERROR
    assert (row["status"], row["error_code"]) == ("requires_reupload", "supervisor_error")
    assert row["cleanup_status"] == "verified"
    assert (transcripts, events, active) == (0, 0, None)
    assert len(workdirs) == 1 and not workdirs[0].exists()


async def test_post_commit_death_is_recovered_without_retranscribing(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-S2-JF-05(b): the owner dies after the commit; the reconciler publishes, no ASR rerun."""
    admission.reset()
    attempt = await _seed(pool)
    commit_spy = AsyncMock(wraps=persistence.commit_transcript)
    monkeypatch.setattr(persistence, "commit_transcript", commit_spy)
    asr_workdirs: list[Path] = []

    async def die_before_cleanup(
        _pool: asyncpg.Pool,
        _lease: leases.Lease,
        _attempt_id: uuid.UUID,
        _workdir: Path,
        _job: supervisor._Job,
        keeper: supervisor._LeaseKeeper,
        _result: supervisor.RunResult,
        **_kwargs: object,
    ) -> None:
        # A dead process stops renewing the lease and never reaches cleanup.
        await keeper.stop()
        raise _SimulatedProcessDeath

    monkeypatch.setattr(supervisor, "_close_attempt", die_before_cleanup)
    with pytest.raises(_SimulatedProcessDeath):
        await run_attempt(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
            audio=iter_bytes(audio),
            config=_config(tmpfs_dir, stub_transcript=SYNTHETIC_TEXT),
            on_child=lambda _pid, workdir: asr_workdirs.append(workdir),
        )
    audio_id = await _audio_id(pool, attempt)

    row = await _row(pool, attempt)
    assert (row["status"], row["cleanup_status"]) == (
        "transcript_committed_cleanup_pending", "pending",
    )
    assert [event["enabled"] for event in await _outbox_rows(pool, audio_id)] == [False]
    assert len(asr_workdirs) == 1 and asr_workdirs[0].exists()

    await _expire(pool, attempt.attempt_id)
    results = await reconcile_committed(pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir))
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.ENABLED)]
    assert not asr_workdirs[0].exists()
    row = await _row(pool, attempt)
    assert (row["status"], row["cleanup_status"]) == ("succeeded", "verified")
    transcripts, events, _active = await _publication_state(pool, audio_id)
    assert (transcripts, events) == (1, 1)
    assert [(e["enabled"], e["blocked_reason"]) for e in await _outbox_rows(pool, audio_id)] == [
        (True, None),
    ]
    assert commit_spy.await_count == 1
    assert len(asr_workdirs) == 1


async def test_lease_lost_during_cleanup_is_fenced_and_stays_held(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-S2-JF-05(c): another owner reclaims the attempt while cleanup runs; the old token loses.

    The heartbeat is parked for the whole test so only the reclaim changes the lease.
    """
    admission.reset()
    attempt = await _seed(pool)
    entered = threading.Event()
    release = threading.Event()
    real_destroy = cleanup.destroy_and_verify

    def held_destroy(
        workdir: Path, pgid: int | None, *, starttime: int | None = None, settle_s: float = 5.0,
    ) -> cleanup.CleanupEvidence:
        entered.set()
        assert release.wait(timeout=20)
        return real_destroy(workdir, pgid, starttime=starttime, settle_s=settle_s)

    monkeypatch.setattr(cleanup, "destroy_and_verify", held_destroy)
    task = asyncio.create_task(run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=iter_bytes(audio),
        config=_config(tmpfs_dir, stub_transcript=SYNTHETIC_TEXT, heartbeat_interval_s=60),
    ))
    successor: leases.Lease | None = None
    try:
        assert await asyncio.to_thread(entered.wait, 60)
        old_token = (await _row(pool, attempt))["fencing_token"]
        await _expire(pool, attempt.attempt_id)
        successor = await leases.reclaim_committed(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
            instance=_SUCCESSOR, ttl=_LEASE_TTL,
        )
        assert successor is not None and successor.fencing_token == old_token + 1
        stale = leases.Lease(
            attempt.attempt_id, attempt.user_id, "ingest:a", old_token, _LEASE_TTL,
        )
        assert await leases.record_cleanup(
            pool, stale, verified=True, evidence={"verified": True},
        ) is False
    finally:
        release.set()
        result = await task
    assert successor is not None

    row = await _row(pool, attempt)
    audio_id = await _audio_id(pool, attempt)
    assert result.outcome is Outcome.LEASE_LOST
    assert result.evidence["verified"] is True  # the old owner did delete its directory
    assert (row["status"], row["cleanup_status"]) == (
        "transcript_committed_cleanup_pending", "pending",
    )
    assert (row["owner_instance"], row["fencing_token"]) == (_SUCCESSOR, successor.fencing_token)
    assert [(e["enabled"], e["blocked_reason"]) for e in await _outbox_rows(pool, audio_id)] == [
        (False, "cleanup_pending"),
    ]

    # The successor dies too: no local directory and no verification, so the row is held.
    await _expire(pool, attempt.attempt_id)
    results = await reconcile_committed(pool, instance=_INSTANCE, tmp_dir=str(tmpfs_dir))
    mine = [item for item in results if item.attempt_id == attempt.attempt_id]

    assert mine == [ReconcileResult(attempt.attempt_id, ReconcileAction.HELD)]
    row = await _row(pool, attempt)
    assert (row["status"], row["cleanup_status"]) == (
        "transcript_committed_cleanup_pending", "pending",
    )
    assert [e["enabled"] for e in await _outbox_rows(pool, audio_id)] == [False]
    transcripts, _events, _active = await _publication_state(pool, audio_id)
    assert transcripts == 1
