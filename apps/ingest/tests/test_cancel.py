"""I-S2-SG-10: cancellation matrix on the ingestion supervisor.

Closing a tab is covered next to the SSE stream (``test_progress_sse``). Cancel
after the commit is the API's 409 (``test_delete_audio``); the case here is the
same conditional update, which must not match a committed attempt.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from test_commit import _commit, _draft, _ready  # noqa: E402
from test_supervisor import (  # noqa: E402
    SYNTHETIC_TEXT,
    _config,
    _row,
    _seed,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest.cancel import ReceptionInterrupted
from maulwurf_ingest.persistence import CommitOutcome
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.supervisor import Outcome, run_attempt

# Re-export fixtures so this module collects them.
_ = (audio, tmpfs_dir)

pytestmark = pytest.mark.integration

_NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
_PRE_COMMIT = ["awaiting_upload", "receiving", "transcribing"]


async def _transcripts(pool: asyncpg.Pool, attempt_id: object) -> int:
    count = await pool.fetchval(
        """SELECT count(*) FROM transcripts
            WHERE audio_id = (SELECT audio_id FROM ingestion_attempts WHERE id = $1)""",
        attempt_id,
    )
    assert isinstance(count, int)
    return count


async def test_disconnect_during_reception_cancels_and_cleans(
    pool: asyncpg.Pool, tmpfs_dir: Path,
) -> None:
    attempt = await _seed(pool)

    async def interrupted() -> AsyncIterator[bytes]:
        yield b"RIFF"
        raise ReceptionInterrupted

    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=interrupted(),
        config=_config(tmpfs_dir),
    )
    row = await _row(pool, attempt)

    assert result.outcome is Outcome.CANCELLED
    assert row["status"] == "cancelled"
    assert row["error_code"] is None
    assert row["cleanup_status"] == "verified"
    assert row["fencing_token"] == 3  # reservation, acquire, then the cancel bump
    assert await _transcripts(pool, attempt.attempt_id) == 0
    assert list(tmpfs_dir.iterdir()) == []


@_NEEDS_FFMPEG
async def test_asr_continues_under_the_lease_when_another_task_is_cancelled(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path,
) -> None:
    """After reception the supervisor does not watch the client.

    A sibling task dying is not a cancel.
    """
    attempt = await _seed(pool)
    gone = asyncio.Event()

    async def watcher() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            gone.set()
            raise

    watch = asyncio.create_task(watcher())
    seen: list[str] = []

    def on_child(_pid: int, _workdir: Path) -> None:
        async def snapshot() -> None:
            row = await _row(pool, attempt)
            seen.append(str(row["status"]))
            watch.cancel()

        asyncio.get_running_loop().create_task(snapshot())

    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=iter_bytes(audio),
        config=_config(
            tmpfs_dir, realtime=True, heartbeat_interval_s=0.2,
            lease_ttl=timedelta(seconds=30), stub_transcript=SYNTHETIC_TEXT,
        ),
        on_child=on_child,
    )
    await asyncio.gather(watch, return_exceptions=True)

    assert gone.is_set()
    assert seen == ["transcribing"]
    assert result.outcome is Outcome.SUCCEEDED
    row = await _row(pool, attempt)
    assert row["status"] == "succeeded"
    assert row["fencing_token"] == 2


@_NEEDS_FFMPEG
async def test_explicit_cancel_during_asr_cleans_and_keeps_no_transcript(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path,
) -> None:
    attempt = await _seed(pool)
    applied = asyncio.Event()

    def on_child(_pid: int, _workdir: Path) -> None:
        async def cancel_row() -> None:
            # Same write as POST .../cancel: status, token bump, lease cleared.
            status = await pool.execute(
                """
                UPDATE ingestion_attempts
                   SET status = 'cancelled', fencing_token = fencing_token + 1,
                       lease_expires_at = NULL, updated_at = now()
                 WHERE id = $1 AND user_id = $2 AND status = 'transcribing'
                """,
                attempt.attempt_id, attempt.user_id,
            )
            assert status == "UPDATE 1"
            applied.set()

        asyncio.get_running_loop().create_task(cancel_row())

    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=iter_bytes(audio),
        config=_config(
            tmpfs_dir, realtime=True, heartbeat_interval_s=0.2,
            lease_ttl=timedelta(seconds=30), stub_transcript=SYNTHETIC_TEXT,
        ),
        on_child=on_child,
    )

    assert applied.is_set()
    assert result.outcome is Outcome.CANCELLED
    row = await _row(pool, attempt)
    assert row["status"] == "cancelled"
    assert row["error_code"] is None
    assert row["cleanup_status"] == "verified"
    assert row["fencing_token"] == 3
    assert await _transcripts(pool, attempt.attempt_id) == 0
    assert list(tmpfs_dir.iterdir()) == []


async def test_cancel_after_the_commit_matches_no_row(pool: asyncpg.Pool) -> None:
    """The pre-commit statuses the cancel endpoint may write do not match a committed attempt."""
    attempt, owned, audio_id = await _ready(pool)
    published = await _commit(pool, owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    text = await pool.fetchval("SELECT text FROM transcripts WHERE audio_id = $1", audio_id)

    status = await pool.execute(
        """
        UPDATE ingestion_attempts
           SET status = 'cancelled', fencing_token = fencing_token + 1,
               lease_expires_at = NULL, updated_at = now()
         WHERE id = $1 AND user_id = $2 AND status = ANY($3::text[])
        """,
        attempt.attempt_id, attempt.user_id, _PRE_COMMIT,
    )

    assert status == "UPDATE 0"
    row = await _row(pool, attempt)
    assert row["status"] == "transcript_committed_cleanup_pending"
    assert row["fencing_token"] == owned.fencing_token
    assert await pool.fetchval(
        "SELECT text FROM transcripts WHERE audio_id = $1", audio_id,
    ) == text
