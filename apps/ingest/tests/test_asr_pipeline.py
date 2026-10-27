"""I-S2-SG-13: the pipeline calls the Riva client through a test double."""

from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    _config,
    _row,
    _seed,
    tmpfs_dir,
)

from maulwurf_ingest.asr.scripted import BLOCKED_NAME
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.supervisor import Outcome, run_attempt

_ = tmpfs_dir
pytestmark = pytest.mark.integration


def _silence() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * 16_000)
    return out.getvalue()


def _script(*, text: str, block: bool) -> str:
    return json.dumps({"text": text, "block": block, "words": []})


async def _transcripts(pool: asyncpg.Pool, attempt_id: object) -> int:
    count = await pool.fetchval(
        """SELECT count(*) FROM transcripts
            WHERE audio_id = (SELECT audio_id FROM ingestion_attempts WHERE id = $1)""",
        attempt_id,
    )
    assert isinstance(count, int)
    return count


async def test_scripted_text_reaches_the_commit(
    pool: asyncpg.Pool, tmpfs_dir: Path,
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        audio=iter_bytes(_silence()),
        config=_config(tmpfs_dir, scripted_transport=_script(text="Hoy repasamos", block=False)),
    )
    row = await _row(pool, attempt)
    stored = await pool.fetchval(
        """SELECT text FROM transcripts
            WHERE audio_id = (SELECT audio_id FROM ingestion_attempts WHERE id = $1)""",
        attempt.attempt_id,
    )
    assert result.fragments == 1
    assert stored == "Hoy repasamos"
    assert await _transcripts(pool, attempt.attempt_id) == 1
    if result.evidence.get("unreadable_processes") == 0:
        assert result.outcome is Outcome.SUCCEEDED
        assert row["status"] == "succeeded"
    else:
        assert result.outcome is Outcome.CLEANUP_FAILED
        assert row["status"] == "transcript_committed_cleanup_pending"


async def test_silence_is_rejected_and_never_succeeded(
    pool: asyncpg.Pool, tmpfs_dir: Path,
) -> None:
    attempt = await _seed(pool)
    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        audio=iter_bytes(_silence()),
        config=_config(tmpfs_dir, scripted_transport=_script(text="   ", block=False)),
    )
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.REJECTED
    assert (row["status"], row["error_code"]) == ("rejected", "no_usable_text")
    assert await _transcripts(pool, attempt.attempt_id) == 0


async def test_lost_lease_cancels_the_rpc(pool: asyncpg.Pool, tmpfs_dir: Path) -> None:
    attempt = await _seed(pool)

    def on_child(_pid: int, workdir: Path) -> None:
        async def expire() -> None:
            marker = workdir / BLOCKED_NAME
            deadline = asyncio.get_running_loop().time() + 20
            while not marker.exists():
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.02)
            await pool.execute(
                "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
                " WHERE id = $1",
                attempt.attempt_id,
            )

        asyncio.get_running_loop().create_task(expire())

    result = await run_attempt(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        audio=iter_bytes(_silence()),
        config=_config(
            tmpfs_dir, heartbeat_interval_s=0.2, asr_timeout_s=30,
            scripted_transport=_script(text="Hoy", block=True),
        ),
        on_child=on_child,
    )
    row = await _row(pool, attempt)
    assert result.child_error == "rpc_cancelled"
    assert result.outcome is Outcome.LEASE_LOST
    assert row["status"] != "succeeded"
    assert await _transcripts(pool, attempt.attempt_id) == 0
