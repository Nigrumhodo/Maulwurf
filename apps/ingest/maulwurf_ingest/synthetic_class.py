"""Short in-RAM synthetic class (S2.9 harness).

One known phrase is synthesized in memory and committed through the supervisor's
test double. Nothing here calls Riva, opens a browser, or treats a null published
limit as an approved maximum. The 1 h class through Caddy was not used. That demo
stays blocked on J2.8, D2.2/D2.3, and D2/D6.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from maulwurf_ingest import hardening, rls
from maulwurf_ingest.asr import riva_client
from maulwurf_ingest.audio.synthetic import DEFAULT_PHRASE_ES, generate_synthetic_utterance
from maulwurf_ingest.audio_absence import report_from_tree
from maulwurf_ingest.capabilities import spike_capabilities
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.supervisor import SupervisorConfig, run_attempt

_BLOCKED = (
    "1 h Caddy demo blocked: S2.10, S2.11, J2.8, D2.2/D2.3, D2/D6"
)


@dataclass(frozen=True)
class HarnessReport:
    transcript_text: str
    timestamp_precision: str | None
    segment_times_null: bool
    published_limits_validated: bool
    published_max_upload_bytes: object
    published_timestamp_precision: object
    cleanup_verified: bool
    workdir_absent: bool
    audio_row_has_no_bytes: bool
    riva_constructed: bool

    @property
    def passed(self) -> bool:
        return (
            bool(self.transcript_text)
            and self.timestamp_precision == "none"
            and self.segment_times_null
            and self.published_limits_validated is False
            and self.published_max_upload_bytes is None
            and self.published_timestamp_precision is None
            and self.cleanup_verified
            and self.workdir_absent
            and self.audio_row_has_no_bytes
            and not self.riva_constructed
        )

    def public_json(self) -> dict[str, object]:
        """Checks only. The spoken text stays with the caller, not in this document."""
        return {
            "timestamp_precision": self.timestamp_precision,
            "segment_times_null": self.segment_times_null,
            "published_limits_validated": self.published_limits_validated,
            "published_max_upload_bytes": self.published_max_upload_bytes,
            "published_timestamp_precision": self.published_timestamp_precision,
            "cleanup_verified": self.cleanup_verified,
            "workdir_absent": self.workdir_absent,
            "audio_row_has_no_bytes": self.audio_row_has_no_bytes,
            "riva_constructed": self.riva_constructed,
            "demo_1h_caddy": _BLOCKED,
        }


async def _seed(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    async with pool.acquire() as conn, conn.transaction():
        await rls.bind_transaction(conn, user_id)
        seeded = await conn.fetchval(
            "INSERT INTO users (id, google_sub, email) VALUES ($1, $2, $3) RETURNING id",
            user_id, f"sub-{uuid.uuid4().hex}", "harness@example.test",
        )
        subject_id = await conn.fetchval(
            "INSERT INTO subjects (user_id, name) VALUES ($1, 'Cálculo') RETURNING id",
            user_id,
        )
        audio_id = await conn.fetchval(
            """INSERT INTO audios (user_id, subject_id, class_date, class_timezone, language_code)
               VALUES ($1, $2, DATE '2026-09-21', 'America/Bogota', 'es') RETURNING id""",
            user_id, subject_id,
        )
        attempt_id = await conn.fetchval(
            """INSERT INTO ingestion_attempts
                 (user_id, audio_id, privacy_notice_version, cloud_processing_accepted_at,
                  third_party_voice_acknowledged_at, declared_providers, status,
                  owner_instance, fencing_token, upload_expires_at)
               VALUES ($1, $2, 'v1', now(), now(), '{"asr": "nvidia-riva"}',
                       'awaiting_upload', 'api:unassigned', 1, now() + interval '15 minutes')
               RETURNING id""",
            user_id, audio_id,
        )
    if not isinstance(seeded, uuid.UUID) or not isinstance(attempt_id, uuid.UUID):
        raise RuntimeError("seed")
    return user_id, attempt_id


def _published() -> tuple[bool, object, object]:
    caps = spike_capabilities()
    validated = caps["limits_validated"] is True
    asr = caps["asr"]
    precision = asr.get("timestamp_precision") if isinstance(asr, dict) else "missing"
    return validated, caps["max_upload_bytes"], precision


async def run_synthetic_class(pool: asyncpg.Pool, *, tmp_dir: str) -> HarnessReport:
    """Synthesize one phrase in RAM and commit it with the supervisor test double."""
    utterance = generate_synthetic_utterance(text=DEFAULT_PHRASE_ES, seed=11)
    user_id, attempt_id = await _seed(pool)
    constructed = False
    original_init = riva_client.NvidiaRivaTranscriptionService.__init__

    def trap(self: object, *_args: object, **_kwargs: object) -> None:
        nonlocal constructed
        constructed = True
        raise RuntimeError("riva_client")

    riva_client.NvidiaRivaTranscriptionService.__init__ = trap  # type: ignore[method-assign]
    try:
        await run_attempt(
            pool, user_id=user_id, attempt_id=attempt_id,
            audio=iter_bytes(utterance.wav_bytes),
            config=SupervisorConfig(
                instance="ingest:e2e", tmp_dir=tmp_dir, stub_transcript=utterance.expected_text,
            ),
        )
    finally:
        riva_client.NvidiaRivaTranscriptionService.__init__ = original_init  # type: ignore[method-assign]

    root = Path(tmp_dir)
    stored = await rls.fetchrow(pool, user_id,
        """
        SELECT t.text, t.timestamp_precision, a.sha256, a.original_bytes, t.id AS transcript_id,
               ia.cleanup_status
          FROM ingestion_attempts ia
          JOIN audios a ON a.user_id = ia.user_id AND a.id = ia.audio_id
          LEFT JOIN transcripts t
            ON t.audio_id = a.id AND t.version = a.active_transcript_version
         WHERE ia.id = $1
        """,
        attempt_id,
    )
    times_null = False
    text = ""
    precision: str | None = None
    no_bytes = False
    verified = False
    if stored is not None:
        if isinstance(stored["text"], str):
            text = stored["text"]
        if isinstance(stored["timestamp_precision"], str):
            precision = stored["timestamp_precision"]
        no_bytes = stored["sha256"] is None and stored["original_bytes"] is None
        verified = stored["cleanup_status"] == "verified"
        transcript_id = stored["transcript_id"]
        if isinstance(transcript_id, uuid.UUID):
            segments = await rls.fetch(
                pool, user_id,
                "SELECT t_start, t_end FROM segments WHERE transcript_id = $1", transcript_id,
            )
            times_null = bool(segments) and all(
                row["t_start"] is None and row["t_end"] is None for row in segments
            )
    absent = report_from_tree(root)
    limits_validated, max_bytes, published_precision = _published()
    return HarnessReport(
        transcript_text=text,
        timestamp_precision=precision,
        segment_times_null=times_null,
        published_limits_validated=limits_validated,
        published_max_upload_bytes=max_bytes,
        published_timestamp_precision=published_precision,
        cleanup_verified=verified,
        workdir_absent=list(root.iterdir()) == [] and absent.clear,
        audio_row_has_no_bytes=no_bytes,
        riva_constructed=constructed,
    )


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def make_tmpfs() -> Path | None:
    """A private directory on a tmpfs. ``None`` when this host has nowhere ephemeral to write."""
    try:
        mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    except OSError:
        return None
    for candidate in (tempfile.gettempdir(), "/dev/shm"):  # noqa: S108
        mount = hardening.mount_for(mounts, candidate)
        if mount is not None and mount.fstype == "tmpfs" and os.access(candidate, os.W_OK):
            return Path(tempfile.mkdtemp(prefix="mw-e2e-", dir=candidate))
    return None


async def _run_cli(url: str, tmp_dir: Path) -> HarnessReport:
    pool = await asyncpg.create_pool(_plain_dsn(url), min_size=1, max_size=1)
    if pool is None:
        raise RuntimeError("pool")
    try:
        return await run_synthetic_class(pool, tmp_dir=str(tmp_dir))
    finally:
        await pool.close()


def main() -> int:
    url = os.environ.get("MAULWURF_DATABASE_URL")
    if not url:
        print("MAULWURF_DATABASE_URL is required", file=sys.stderr)
        return 2
    tmp_dir = make_tmpfs()
    if tmp_dir is None:
        print("no writable tmpfs", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(_run_cli(url, tmp_dir))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    json.dump(report.public_json(), sys.stdout)
    sys.stdout.write("\n")
    return 0 if report.passed else 1
