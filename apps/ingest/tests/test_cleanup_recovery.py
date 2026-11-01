"""I-S4-SG-03: the S4.1 cleanup/recovery runbook executed as written.

The unit cases run without PostgreSQL. The integration cases use the real migrated
database and the same recovery code the runbook invokes:

- a persisted failed cleanup closes admission, is attested with destruction evidence,
  and the committed text returns to the outbox without retranscription;
- a host-loss committed attempt stays held until the attestation, then is enabled;
- the tool refuses a live lease, a local attempt directory, already-resolved rows, an
  attempt that cannot have received bytes, and evidence that does not cover the attempt;
- the 60 s sweeper cadence and the two entry points of the operator script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

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

from maulwurf_ingest import admission, cleanup, recovery
from maulwurf_ingest.asr_job import ORIGINAL
from maulwurf_ingest.config import settings
from maulwurf_ingest.persistence import CommitOutcome
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.recovery import (
    DestructionEvidence,
    EvidenceError,
    RecoveryError,
    ResolveAction,
    ResolveReport,
    StatusReport,
    SweepReport,
    _evidence_covers,
    collect_status,
    load_evidence,
    render_status,
    resolve_attempts,
    run_sweeper,
    sweep_once,
)
from maulwurf_ingest.supervisor import Outcome, run_attempt

# Re-export the tmpfs and audio fixtures so the directory cases can collect them.
_ = (audio, tmpfs_dir)

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_ROOT = REPO_ROOT / "apps" / "ingest"
SCRIPT = REPO_ROOT / "scripts" / "ops" / "cleanup_recover.py"
EXPECTED_HOST = "ingest-test-1"


@pytest.fixture(autouse=True)
def _reset_admission() -> Iterator[None]:
    admission.reset()
    yield
    admission.reset()


def _evidence_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "host": "ingest-test-1",
        "destroyed_at": datetime.now(UTC).isoformat(),
        "memory_destroyed": True,
        "tmpfs_mount_destroyed": True,
        "no_snapshots": True,
        "no_swap": True,
        "reviewer": "area-s-reviewer",
        "reference": "INC-2026-10-31-01",
    }
    document.update(overrides)
    return document


def _write_evidence(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "destruction.json"
    path.write_text(json.dumps(_evidence_document(**overrides)), encoding="utf-8")
    return path


def _json(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


async def _expire(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt_id,
    )


async def _audio_id(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> uuid.UUID:
    value = await pool.fetchval(
        "SELECT audio_id FROM ingestion_attempts WHERE id = $1", attempt_id,
    )
    assert isinstance(value, uuid.UUID)
    return value


async def _outbox(pool: asyncpg.Pool, audio_id: uuid.UUID) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT enabled, blocked_reason FROM outbox_events WHERE resource_id = $1",
        audio_id,
    )
    assert row is not None
    return row


async def _transcripts(pool: asyncpg.Pool, audio_id: uuid.UUID) -> int:
    value = await pool.fetchval("SELECT count(*) FROM transcripts WHERE audio_id = $1", audio_id)
    assert isinstance(value, int)
    return value


def test_evidence_requires_every_destruction_fact(tmp_path: Path) -> None:
    for key in ("memory_destroyed", "tmpfs_mount_destroyed", "no_snapshots", "no_swap"):
        path = _write_evidence(tmp_path, **{key: False})
        with pytest.raises(EvidenceError, match=key):
            load_evidence(path)
        missing = _evidence_document()
        del missing[key]
        path.write_text(json.dumps(missing), encoding="utf-8")
        with pytest.raises(EvidenceError, match=key):
            load_evidence(path)


def test_evidence_rejects_unknown_and_missing_fields(tmp_path: Path) -> None:
    extra = _write_evidence(tmp_path, notes="a transcript would be unsafe here")
    with pytest.raises(EvidenceError, match="unknown fields"):
        load_evidence(extra)
    missing = _evidence_document()
    del missing["reference"]
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(EvidenceError, match="missing required fields"):
        load_evidence(path)

def test_evidence_requires_a_timezone_and_rejects_future_instants(tmp_path: Path) -> None:
    naive = _write_evidence(tmp_path, destroyed_at="2026-10-31T12:00:00")
    with pytest.raises(EvidenceError, match="timezone"):
        load_evidence(naive)
    future = _write_evidence(
        tmp_path, destroyed_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    with pytest.raises(EvidenceError, match="future"):
        load_evidence(future)


def test_evidence_document_contains_only_fixed_facts(tmp_path: Path) -> None:
    evidence = load_evidence(_write_evidence(tmp_path))
    document = evidence.as_document()

    assert set(document) == {
        "verified", "source", "host", "destroyed_at", "memory_destroyed",
        "tmpfs_mount_destroyed", "no_snapshots", "no_swap", "reviewer", "reference",
        "evidence_sha256",
    }
    assert document["source"] == "operator_attestation"
    assert document["verified"] is True
    assert len(evidence.sha256) == 64
    assert "notes" not in document


def test_evidence_is_read_at_most_one_byte_past_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "oversize.json"
    path.write_bytes(b" " * (recovery.EVIDENCE_MAX_BYTES * 2))
    reads: list[int] = []
    real_open = Path.open

    class _TrackingHandle:
        def __init__(self, handle: Any) -> None:
            self._handle = handle

        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return cast(bytes, self._handle.read(size))

        def __enter__(self) -> _TrackingHandle:
            return self

        def __exit__(self, *args: object) -> None:
            self._handle.close()

    def tracking_open(
        self: Path, mode: str = "r", *args: Any, **kwargs: Any,
    ) -> _TrackingHandle:
        return _TrackingHandle(real_open(self, mode, *args, **kwargs))

    monkeypatch.setattr(Path, "open", tracking_open)

    with pytest.raises(EvidenceError, match="exceeds"):
        load_evidence(path)
    assert reads == [recovery.EVIDENCE_MAX_BYTES + 1]


def _coverage_evidence(destroyed_at: datetime) -> DestructionEvidence:
    return DestructionEvidence(
        host="ingest-test-1", destroyed_at=destroyed_at, reviewer="area-s-reviewer",
        reference="INC-2026-10-31-01", sha256="0" * 64,
    )


def _coverage_row(*, created_at: datetime, heartbeat_at: datetime | None) -> asyncpg.Record:
    return cast(
        asyncpg.Record,
        {"created_at": created_at, "heartbeat_at": heartbeat_at, "updated_at": created_at},
    )


def test_evidence_coverage_rejects_an_attempt_created_after_the_destruction() -> None:
    destroyed_at = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    row = _coverage_row(
        created_at=destroyed_at + timedelta(seconds=1),
        heartbeat_at=destroyed_at - timedelta(seconds=10),
    )

    assert _evidence_covers(_coverage_evidence(destroyed_at), row) is False


def test_evidence_coverage_allows_one_minute_of_heartbeat_clock_skew() -> None:
    destroyed_at = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    evidence = _coverage_evidence(destroyed_at)
    created_at = destroyed_at - timedelta(hours=1)

    inside = _coverage_row(
        created_at=created_at, heartbeat_at=destroyed_at + timedelta(seconds=59),
    )
    outside = _coverage_row(
        created_at=created_at, heartbeat_at=destroyed_at + timedelta(seconds=61),
    )

    assert _evidence_covers(evidence, inside) is True
    assert _evidence_covers(evidence, outside) is False


class _UnreachablePool:
    """The host check must refuse before any database read or write."""

    def acquire(self) -> None:
        raise AssertionError("the host check must not touch the database")


async def test_resolve_refuses_an_attestation_for_another_host(
    tmp_path: Path,
) -> None:
    attempt_id = uuid.UUID(int=21)
    evidence = load_evidence(_write_evidence(tmp_path))

    for apply in (False, True):
        report = await resolve_attempts(
            cast(asyncpg.Pool, _UnreachablePool()),
            attempt_ids=[attempt_id],
            evidence=evidence,
            expected_host="ingest-test-2",
            apply=apply,
        )

        assert report.expected_host == "ingest-test-2"
        decision = report.decisions[0]
        assert decision.action is ResolveAction.REFUSED
        assert decision.reason == "host_mismatch"


async def test_resolve_fails_closed_without_an_expected_host(tmp_path: Path) -> None:
    attempt_id = uuid.UUID(int=22)
    evidence = load_evidence(_write_evidence(tmp_path))

    for expected_host in (None, "", "   "):
        report = await resolve_attempts(
            cast(asyncpg.Pool, _UnreachablePool()),
            attempt_ids=[attempt_id],
            evidence=evidence,
            expected_host=expected_host,
            apply=True,
        )

        assert report.decisions[0].reason == "expected_host_not_configured"


async def test_resolve_takes_the_expected_host_from_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, str | None] = {}

    async def fake_resolve(
        pool: asyncpg.Pool,
        *,
        attempt_ids: tuple[uuid.UUID, ...],
        evidence: DestructionEvidence,
        expected_host: str | None,
        apply: bool,
        tmp_dir: str | None = None,
    ) -> ResolveReport:
        captured["expected_host"] = expected_host
        return ResolveReport(
            evidence=evidence,
            applied=apply,
            decisions=(),
            expected_host=expected_host,
        )

    monkeypatch.setattr(recovery, "resolve_attempts", fake_resolve)
    monkeypatch.setattr(settings, "ingest_host_id", "ingest-from-config")
    args = recovery.build_parser().parse_args([
        "resolve",
        "--attempt", str(uuid.uuid4()),
        "--evidence-file", str(_write_evidence(tmp_path)),
        "--expected-host", "ingest-from-cli",
    ])

    exit_code = await recovery._resolve_command(cast(asyncpg.Pool, None), args)

    assert exit_code == recovery.EXIT_OK
    assert captured["expected_host"] == "ingest-from-cli"


async def test_resolve_defaults_to_the_configured_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, str | None] = {}

    async def fake_resolve(
        pool: asyncpg.Pool,
        *,
        attempt_ids: tuple[uuid.UUID, ...],
        evidence: DestructionEvidence,
        expected_host: str | None,
        apply: bool,
        tmp_dir: str | None = None,
    ) -> ResolveReport:
        captured["expected_host"] = expected_host
        return ResolveReport(
            evidence=evidence,
            applied=apply,
            decisions=(),
            expected_host=expected_host,
        )

    monkeypatch.setattr(recovery, "resolve_attempts", fake_resolve)
    monkeypatch.setattr(settings, "ingest_host_id", "ingest-from-config")
    args = recovery.build_parser().parse_args([
        "resolve",
        "--attempt", str(uuid.uuid4()),
        "--evidence-file", str(_write_evidence(tmp_path)),
    ])

    assert await recovery._resolve_command(cast(asyncpg.Pool, None), args) == recovery.EXIT_OK
    assert captured["expected_host"] == "ingest-from-config"


async def test_resolve_cli_fails_closed_without_a_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "ingest_host_id", None)
    args = recovery.build_parser().parse_args([
        "resolve",
        "--attempt", str(uuid.uuid4()),
        "--evidence-file", str(_write_evidence(tmp_path)),
        "--apply",
    ])

    exit_code = await recovery._resolve_command(
        cast(asyncpg.Pool, _UnreachablePool()), args,
    )

    assert exit_code == recovery.EXIT_ATTENTION
    assert "reason=expected_host_not_configured" in capsys.readouterr().out


def test_status_text_marks_a_persisted_failure_as_closed() -> None:
    attempt_id = uuid.UUID(int=11)
    report = StatusReport(
        failed_cleanups=(attempt_id,),
        committed_pending=(),
        stranded=(),
        local_attempt_dirs=0,
        oldest_temporary_age_s=0.0,
        tmpfs_free_bytes=1024,
        tmpfs_total_bytes=2048,
        tmpfs_is_tmpfs=True,
    )
    lines = render_status(report).splitlines()

    assert report.admission_expected_open is False
    assert report.attention_required is True
    assert "admission_expected=closed" in lines
    assert "attention_required=true" in lines
    assert "failed_cleanups=1" in lines
    assert f"failed_attempt_ids={attempt_id}" in lines
    assert "committed_pending_ids=" in lines


async def test_watch_mode_waits_sixty_seconds_between_passes() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run_pass() -> SweepReport:
        return SweepReport(enabled=1)

    reports = await run_sweeper(
        interval_s=60, max_passes=3, run_pass=run_pass, sleep=fake_sleep,
    )

    assert [report.pass_number for report in reports] == [1, 2, 3]
    assert [report.enabled for report in reports] == [1, 1, 1]
    assert sleeps == [60, 60]


async def test_single_pass_mode_does_not_sleep() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run_pass() -> SweepReport:
        return SweepReport()

    reports = await run_sweeper(
        interval_s=60, max_passes=1, run_pass=run_pass, sleep=fake_sleep,
    )

    assert len(reports) == 1
    assert sleeps == []


async def test_a_cleanup_failure_stops_the_sweeper() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run_pass() -> SweepReport:
        return SweepReport(cleanup_failed=1)

    reports = await run_sweeper(
        interval_s=60,
        max_passes=None,
        run_pass=run_pass,
        sleep=fake_sleep,
        stop_on=lambda report: bool(report.cleanup_failed),
    )

    assert len(reports) == 1
    assert sleeps == []


async def test_the_sweeper_refuses_an_interval_below_sixty_seconds() -> None:
    async def run_pass() -> SweepReport:
        return SweepReport()

    with pytest.raises(RecoveryError, match="at least 60"):
        await run_sweeper(interval_s=59, max_passes=1, run_pass=run_pass)


async def test_a_single_pass_ignores_the_interval_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[str] = []

    async def fake_sweep_once(
        pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepReport:
        instances.append(instance)
        return SweepReport()

    monkeypatch.setattr(recovery, "sweep_once", fake_sweep_once)
    args = recovery.build_parser().parse_args(["sweep", "--interval", "5"])

    exit_code = await recovery._sweep_command(cast(asyncpg.Pool, None), args)

    assert exit_code == recovery.EXIT_OK
    assert instances == ["ops:cleanup-recover"]


async def test_watch_mode_rejects_an_interval_below_sixty_seconds() -> None:
    args = recovery.build_parser().parse_args(
        ["sweep", "--watch", "--interval", "5", "--max-passes", "1"]
    )

    with pytest.raises(RecoveryError, match="at least 60"):
        await recovery._sweep_command(cast(asyncpg.Pool, None), args)


def test_the_operator_script_runs_from_a_checkout() -> None:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(SCRIPT), "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status" in completed.stdout


def test_resolve_help_states_the_host_precondition() -> None:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(SCRIPT), "resolve", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--expected-host" in completed.stdout
    assert "THIS host" in completed.stdout
    assert "not evidence" in completed.stdout


def test_the_module_entry_point_runs_inside_the_image() -> None:
    # The runbook calls `python -m maulwurf_ingest.recovery` in the container.
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "maulwurf_ingest.recovery", "--help"],
        cwd=INGEST_ROOT, capture_output=True, text=True, timeout=60, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status" in completed.stdout


def test_the_operator_script_runs_from_stdin_inside_the_image() -> None:
    # The hardened image runs `python - < scripts/ops/cleanup_recover.py`; __file__ is
    # "<stdin>" and the installed package is imported from the working directory.
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-", "--help"],
        input=SCRIPT.read_text(encoding="utf-8"),
        cwd=INGEST_ROOT, capture_output=True, text=True, timeout=60, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "status" in completed.stdout


def test_main_without_a_database_url_fails_safe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(settings, "database_url", None)

    assert recovery.main(["status"]) == recovery.EXIT_ERROR
    captured = capsys.readouterr()
    assert "MAULWURF_DATABASE_URL is not set" in captured.err


def test_resolve_requires_an_evidence_file() -> None:
    with pytest.raises(SystemExit) as raised:
        recovery.main(["resolve", "--attempt", str(uuid.uuid4())])

    assert raised.value.code == 2


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
async def test_runbook_recovers_a_failed_cleanup_with_attestation(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    """I-S4-SG-03: failed cleanup -> admission halted -> attestation -> text enabled."""
    attempt = await _seed(pool)
    audio_id = await _audio_id(pool, attempt.attempt_id)
    held: list[int] = []

    def _hold(_pid: int, workdir: Path) -> None:
        held.append(os.open(workdir / ORIGINAL, os.O_RDONLY))

    try:
        result = await run_attempt(
            pool,
            user_id=attempt.user_id,
            attempt_id=attempt.attempt_id,
            audio=iter_bytes(audio),
            config=_config(tmpfs_dir, stub_transcript=SYNTHETIC_TEXT),
            on_child=_hold,
        )
    finally:
        for fd in held:
            os.close(fd)
    row = await _row(pool, attempt)
    assert result.outcome is Outcome.CLEANUP_FAILED
    assert row["cleanup_status"] == "failed"
    await _expire(pool, attempt.attempt_id)

    status = await collect_status(pool, tmp_dir=str(tmpfs_dir))
    assert attempt.attempt_id in status.failed_cleanups
    assert status.admission_expected_open is False
    assert status.attention_required is True
    assert status.local_attempt_dirs == 0

    await admission.restore_from_database(pool)
    assert admission.uploads_admitted() is False

    evidence = load_evidence(_write_evidence(tmp_path))
    dry = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=False,
        tmp_dir=str(tmpfs_dir),
    )
    assert dry.decisions[0].action is ResolveAction.WOULD_VERIFY
    assert (await _row(pool, attempt))["cleanup_status"] == "failed"

    applied = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert applied.decisions[0].action is ResolveAction.VERIFIED
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "verified"
    assert row["cleanup_verified_at"] is not None
    assert row["audio_deleted_at"] is not None
    stored = _json(row["cleanup_evidence"])
    assert isinstance(stored, dict)
    assert stored["source"] == "operator_attestation"
    assert stored["evidence_sha256"] == evidence.sha256
    assert stored["expected_host"] == EXPECTED_HOST
    assert "notes" not in stored

    repeated = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert repeated.decisions[0].reason == "already_resolved"

    # The shared test database may contain failed rows from other modules. The latch must
    # be open exactly when no persisted failure remains, not because this test assumed a
    # clean database.
    admission.reset()
    await admission.restore_from_database(pool)
    failed_count = await pool.fetchval(
        "SELECT count(*) FROM ingestion_attempts WHERE cleanup_status = 'failed'"
    )
    assert admission.uploads_admitted() is (failed_count == 0)

    swept = await sweep_once(pool, instance="ops:test", tmp_dir=str(tmpfs_dir))
    assert swept.enabled >= 1
    row = await _row(pool, attempt)
    assert row["status"] == "succeeded"
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is True and event["blocked_reason"] is None
    assert await _transcripts(pool, audio_id) == 1

    final_status = await collect_status(pool, tmp_dir=str(tmpfs_dir))
    assert attempt.attempt_id not in final_status.failed_cleanups
    assert attempt.attempt_id not in final_status.committed_pending
    assert attempt.attempt_id not in final_status.stranded


@pytest.mark.integration
async def test_host_loss_is_held_until_attested_then_enabled(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    """Host loss: no local directory is not proof. The attestation unlocks the text."""
    attempt, _owned, audio_id = await _ready(pool)
    published = await _commit(pool, _owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    await _expire(pool, attempt.attempt_id)

    first = await sweep_once(pool, instance="ops:test", tmp_dir=str(tmpfs_dir))
    assert first.held >= 1
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "pending"
    assert (await _outbox(pool, audio_id))["enabled"] is False

    evidence = load_evidence(_write_evidence(tmp_path))
    applied = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert applied.decisions[0].action is ResolveAction.VERIFIED

    second = await sweep_once(pool, instance="ops:test", tmp_dir=str(tmpfs_dir))
    assert second.enabled >= 1
    row = await _row(pool, attempt)
    assert row["status"] == "succeeded"
    assert row["cleanup_status"] == "verified"
    event = await _outbox(pool, audio_id)
    assert event["enabled"] is True
    assert await _transcripts(pool, audio_id) == 1


@pytest.mark.integration
async def test_resolve_refuses_a_live_lease_and_a_local_directory(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    attempt, _owned, _audio_id = await _ready(pool)
    evidence = load_evidence(_write_evidence(tmp_path))

    live = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert live.decisions[0].reason == "live_lease"

    await _expire(pool, attempt.attempt_id)
    workdir = cleanup.create_workdir(str(attempt.attempt_id), str(tmpfs_dir))
    try:
        local = await resolve_attempts(
            pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
            expected_host=EXPECTED_HOST, apply=True,
            tmp_dir=str(tmpfs_dir),
        )
        assert local.decisions[0].reason == "local_attempt_directory_present"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    assert (await _row(pool, attempt))["cleanup_status"] == "pending"


@pytest.mark.integration
async def test_resolve_refuses_a_host_mismatch_without_writing(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    attempt, _owned, _audio_id = await _ready(pool)
    await _expire(pool, attempt.attempt_id)
    evidence = load_evidence(_write_evidence(tmp_path))

    for apply in (False, True):
        report = await resolve_attempts(
            pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
            expected_host="ingest-other", apply=apply, tmp_dir=str(tmpfs_dir),
        )

        assert report.expected_host == "ingest-other"
        assert report.decisions[0].reason == "host_mismatch"
        row = await _row(pool, attempt)
        assert row["cleanup_status"] == "pending"
        assert row["cleanup_verified_at"] is None
        assert row["audio_deleted_at"] is None


@pytest.mark.integration
async def test_resolve_fails_closed_when_no_host_is_configured(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    attempt, _owned, _audio_id = await _ready(pool)
    await _expire(pool, attempt.attempt_id)
    evidence = load_evidence(_write_evidence(tmp_path))

    report = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=None, apply=True, tmp_dir=str(tmpfs_dir),
    )

    assert report.decisions[0].reason == "expected_host_not_configured"
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None
    assert row["audio_deleted_at"] is None


@pytest.mark.integration
async def test_resolve_refuses_an_attempt_without_bytes_and_uncovered_evidence(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    no_bytes = await _seed(pool)
    await _expire(pool, no_bytes.attempt_id)
    evidence = load_evidence(_write_evidence(tmp_path))
    refused = await resolve_attempts(
        pool, attempt_ids=[no_bytes.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert refused.decisions[0].reason == "no_audio_received"

    failed = await _seed(pool)
    # A cleanup failure happens after the attempt left awaiting_upload; use a terminal
    # status so this case tests the evidence coverage, not the no-bytes refusal.
    await pool.execute(
        "UPDATE ingestion_attempts"
        " SET cleanup_status = 'failed', status = 'requires_reupload', heartbeat_at = now()"
        " WHERE id = $1",
        failed.attempt_id,
    )
    await _expire(pool, failed.attempt_id)
    old = load_evidence(
        _write_evidence(
            tmp_path, destroyed_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        )
    )
    uncovered = await resolve_attempts(
        pool, attempt_ids=[failed.attempt_id], evidence=old,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert uncovered.decisions[0].reason == "not_covered_by_evidence"
    assert (await _row(pool, failed))["cleanup_status"] == "failed"


@pytest.mark.integration
async def test_resolve_refuses_an_attempt_created_after_the_destruction(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    attempt = await _seed(pool)
    await pool.execute(
        "UPDATE ingestion_attempts"
        " SET cleanup_status = 'failed', status = 'requires_reupload',"
        "     heartbeat_at = now() - interval '30 seconds'"
        " WHERE id = $1",
        attempt.attempt_id,
    )
    await _expire(pool, attempt.attempt_id)
    # ``created_at`` is later than this instant, while the heartbeat is inside the
    # one-minute clock-skew window: only the creation guard can refuse the attestation.
    evidence = load_evidence(
        _write_evidence(
            tmp_path,
            destroyed_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
        )
    )

    report = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )

    assert report.decisions[0].reason == "not_covered_by_evidence"
    assert (await _row(pool, attempt))["cleanup_status"] == "failed"


@pytest.mark.integration
async def test_sweep_ends_stranded_attempts_without_retranscribing(
    pool: asyncpg.Pool, tmpfs_dir: Path, tmp_path: Path,
) -> None:
    attempt, _owned, audio_id = await _ready(pool)
    await _expire(pool, attempt.attempt_id)

    report = await sweep_once(pool, instance="ops:test", tmp_dir=str(tmpfs_dir))

    assert report.stranded_ended >= 1
    row = await _row(pool, attempt)
    assert (row["status"], row["error_code"]) == ("requires_reupload", "technical_loss")
    assert row["cleanup_status"] == "pending"
    assert await _transcripts(pool, audio_id) == 0

    # A host-loss attestation still closes the cleanup of the attempt a restart already
    # swept. The attempt stays requires_reupload; no text is invented or retranscribed.
    evidence = load_evidence(_write_evidence(tmp_path))
    resolved = await resolve_attempts(
        pool, attempt_ids=[attempt.attempt_id], evidence=evidence,
        expected_host=EXPECTED_HOST, apply=True,
        tmp_dir=str(tmpfs_dir),
    )
    assert resolved.decisions[0].action is ResolveAction.VERIFIED
    row = await _row(pool, attempt)
    assert (row["status"], row["cleanup_status"]) == ("requires_reupload", "verified")
    assert row["audio_deleted_at"] is not None
    assert await _transcripts(pool, audio_id) == 0
