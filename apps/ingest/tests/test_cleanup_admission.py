"""I-S1-SG-05 (S1.B6, G1): el lease vencido no verifica el cleanup ni cierra la admisión.

Un cleanup persistido como `failed` sí cierra la admisión de la instancia y emite la
alerta. La outbox bloqueada sin evidencia se prueba en la API (`test_cleanup_gate`).
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    PROVEN_CLEANUP,
    SYNTHETIC_TEXT,
    _config,
    _row,
    _seed,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest import admission, cancel, cleanup, lease, metrics, reconciler, rls, supervisor
from maulwurf_ingest.asr_job import ORIGINAL
from maulwurf_ingest.prometheus_text import Sample
from maulwurf_ingest.receiver import iter_bytes
from maulwurf_ingest.reconciler import ReconcileAction, ReconcileResult, sweep_stranded
from maulwurf_ingest.supervisor import Outcome, run_attempt

_ = (audio, tmpfs_dir)


def _unused_pool() -> asyncpg.Pool:
    return cast(asyncpg.Pool, object())


@pytest.fixture(autouse=True)
def _reset_admission() -> Iterator[None]:
    admission.reset()
    yield
    admission.reset()


def test_halt_closes_admission_until_reset() -> None:
    attempt_id = uuid.uuid4()
    admission.halt(attempt_id)

    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt_id),)


async def test_transient_close_reopens_after_a_readable_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4.2: a failed latch read is transient; a clean read reopens admission."""

    async def unreadable(*_args: object, **_kwargs: object) -> object:
        raise OSError("database down")

    monkeypatch.setattr(rls, "fetchrow", unreadable)
    await admission.restore_from_database(_unused_pool())

    assert admission.uploads_admitted() is False

    async def no_failed_rows(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(rls, "fetchrow", no_failed_rows)
    await admission.restore_from_database(_unused_pool())

    assert admission.uploads_admitted() is True
    assert admission.alerts() == ()


async def test_durable_halt_survives_a_clean_latch_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4.2: a persisted `failed` row is sticky; later clean reads cannot reopen it."""
    attempt_id = uuid.uuid4()

    async def failed_row(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"id": attempt_id}

    monkeypatch.setattr(rls, "fetchrow", failed_row)
    await admission.restore_from_database(_unused_pool())
    assert admission.uploads_admitted() is False

    async def no_failed_rows(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(rls, "fetchrow", no_failed_rows)
    await admission.restore_from_database(_unused_pool())

    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt_id),)


def _unverified_evidence() -> cleanup.CleanupEvidence:
    return cleanup.CleanupEvidence(
        workdir_absent=True, tmp_is_tmpfs=True, live_processes=0,
        open_descriptors=1, mounts_under_workdir=0, unreadable_processes=0,
    )


def _cleanup_metric(samples: list[Sample]) -> float:
    for sample in samples:
        if (
            sample.labels.get("stage") == "cleanup"
            and sample.labels.get("error_code") == "cleanup_unrecorded"
        ):
            return float(sample.value)
    return 0.0


def _verified_evidence() -> cleanup.CleanupEvidence:
    return cleanup.CleanupEvidence(
        workdir_absent=True, tmp_is_tmpfs=True, live_processes=0,
        open_descriptors=0, mounts_under_workdir=0, unreadable_processes=0,
    )


async def _reconcile_unrecorded(
    monkeypatch: pytest.MonkeyPatch, *, cleanup_status: str, destroy: object,
    record: object, workdir: Path,
) -> tuple[ReconcileResult, ...]:
    attempt_id = uuid.uuid4()
    user_id = uuid.uuid4()
    owned = lease.Lease(attempt_id, user_id, "ingest:test", 3, timedelta(seconds=30))

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [{"id": attempt_id, "user_id": user_id, "cleanup_status": cleanup_status}]

    async def fake_reclaim(*_args: object, **_kwargs: object) -> lease.Lease:
        return owned

    monkeypatch.setattr(rls, "fetch", fake_fetch)
    monkeypatch.setattr(lease, "reclaim_committed", fake_reclaim)
    monkeypatch.setattr(reconciler, "local_workdirs", lambda *_args: [workdir])
    monkeypatch.setattr(reconciler, "_destroy", destroy)
    monkeypatch.setattr(lease, "record_cleanup", record)
    return await reconciler.reconcile_committed(
        _unused_pool(), instance="ingest:test", tmp_dir=str(workdir),
    )


async def test_reconciler_alerts_a_cleanup_exception_that_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Hoplite fix3 1: a driver error must not leave the row pending with only a log."""

    async def destroy_boom(*_args: object, **_kwargs: object) -> cleanup.CleanupEvidence:
        raise OSError("rmtree")

    async def record_boom(*_args: object, **_kwargs: object) -> bool:
        raise OSError("record")

    metrics.reset()
    results = await _reconcile_unrecorded(
        monkeypatch, cleanup_status="pending", destroy=destroy_boom,
        record=record_boom, workdir=tmp_path,
    )
    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True,
    )

    attempt_id = results[0].attempt_id
    assert results == (ReconcileResult(attempt_id, ReconcileAction.CLEANUP_FAILED),)
    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", attempt_id),)
    assert _cleanup_metric(list(samples)) == 1.0


async def test_reconciler_alerts_unverified_evidence_that_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Hoplite fix3 1: a False write on the evidence path is observable too."""

    async def unverified(*_args: object, **_kwargs: object) -> cleanup.CleanupEvidence:
        return _unverified_evidence()

    async def record_false(*_args: object, **_kwargs: object) -> bool:
        return False

    metrics.reset()
    results = await _reconcile_unrecorded(
        monkeypatch, cleanup_status="pending", destroy=unverified,
        record=record_false, workdir=tmp_path,
    )
    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True,
    )

    attempt_id = results[0].attempt_id
    assert results == (ReconcileResult(attempt_id, ReconcileAction.CLEANUP_FAILED),)
    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", attempt_id),)
    assert _cleanup_metric(list(samples)) == 1.0


async def test_reconciler_alerts_verified_evidence_that_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Hoplite fix3 1: even verified evidence is not silent when the write fails."""

    async def verified(*_args: object, **_kwargs: object) -> cleanup.CleanupEvidence:
        return _verified_evidence()

    async def record_false(*_args: object, **_kwargs: object) -> bool:
        return False

    metrics.reset()
    results = await _reconcile_unrecorded(
        monkeypatch, cleanup_status="pending", destroy=verified,
        record=record_false, workdir=tmp_path,
    )
    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True,
    )

    attempt_id = results[0].attempt_id
    assert results == (ReconcileResult(attempt_id, ReconcileAction.LOST),)
    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", attempt_id),)
    assert _cleanup_metric(list(samples)) == 1.0


async def test_cancelled_cleanup_that_cannot_be_recorded_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite fix3 1: the cancelled path has the same explicit alert."""
    owned = lease.Lease(uuid.uuid4(), uuid.uuid4(), "ingest:test", 3, timedelta(seconds=30))

    async def record_false(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(cancel, "record_cancelled_cleanup", record_false)
    metrics.reset()
    result = supervisor.RunResult(Outcome.CANCELLED)
    await supervisor._note_cancelled_cleanup(
        _unused_pool(), owned, owned.attempt_id, evidence=_verified_evidence(),
        exc=None, result=result,
    )

    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", owned.attempt_id),)


async def test_a_malformed_failed_latch_keeps_admission_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite fix3 3: an unusable ``id`` must fail closed, never reopen admission."""

    async def malformed(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"id": "not-a-uuid"}

    monkeypatch.setattr(rls, "fetchrow", malformed)
    await admission.restore_from_database(_unused_pool())

    assert admission.uploads_admitted() is False


def test_the_alert_buffer_is_bounded() -> None:
    """Hoplite fix3 4: the observable buffer cannot grow with every affected attempt."""
    assert admission.MAX_ALERTS > 0

    first = uuid.uuid4()
    admission.halt(first)
    for _ in range(admission.MAX_ALERTS + 4):
        admission.halt(uuid.uuid4())

    assert len(admission.alerts()) == admission.MAX_ALERTS
    assert admission.alerts()[-1].attempt_id != first
    assert all(alert.code == "cleanup_failed" for alert in admission.alerts())



async def test_unrecorded_unverified_evidence_alerts_without_closing_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Hoplite fix2 2: a zombie cleanup that cannot persist ``failed`` stays observable."""
    attempt_id = uuid.uuid4()
    owned = lease.Lease(attempt_id, uuid.uuid4(), "ingest:test", 3, timedelta(seconds=30))

    async def lost_token(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(lease, "record_cleanup", lost_token)
    metrics.reset()
    result = supervisor.RunResult(Outcome.SUCCEEDED)
    await supervisor._note_cleanup_evidence(
        _unused_pool(), owned, attempt_id, _unverified_evidence(), owned_audio=True,
        committed=True, result=result, audio_id=uuid.uuid4(), version=1,
    )
    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True,
    )

    assert result.outcome is Outcome.CLEANUP_FAILED
    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", attempt_id),)
    assert _cleanup_metric(list(samples)) == 1.0


async def test_unrecorded_cleanup_exception_alerts_without_closing_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite fix2 2: the exception path has the same explicit alert."""
    attempt_id = uuid.uuid4()
    owned = lease.Lease(attempt_id, uuid.uuid4(), "ingest:test", 3, timedelta(seconds=30))

    async def lost_token(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(lease, "record_cleanup", lost_token)
    metrics.reset()
    result = supervisor.RunResult(Outcome.LEASE_LOST)
    await supervisor._note_cleanup_failure(
        _unused_pool(), owned, attempt_id, owned_audio=True, exc=OSError("rmtree"),
        result=result,
    )

    assert admission.uploads_admitted() is True
    assert admission.alerts() == (admission.Alert("cleanup_unrecorded", attempt_id),)


@pytest.mark.integration
async def test_expired_lease_stays_pending_and_admits(pool: asyncpg.Pool) -> None:
    """I-S1-SG-05: vencer el lease no marca cleanup verificado ni detiene la admisión."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        attempt.attempt_id,
    )

    assert await lease.heartbeat(pool, owned) is False
    assert await lease.transition(
        pool, owned, from_status="awaiting_upload", to_status="receiving"
    ) is False
    row = await _row(pool, attempt)

    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None
    assert admission.uploads_admitted() is True
    assert admission.alerts() == ()


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
async def test_cleanup_failure_halts_admission_and_alerts(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """I-S1-SG-05: cleanup fallido cierra la admisión y dispara la alerta, sin rutas."""
    attempt = await _seed(pool)
    held: list[int] = []

    def _hold(_pid: int, workdir: Path) -> None:
        held.append(os.open(workdir / ORIGINAL, os.O_RDONLY))

    try:
        with caplog.at_level(logging.ERROR, logger="maulwurf_ingest.admission"):
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
    assert row["cleanup_verified_at"] is None
    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt.attempt_id),)
    messages = [record.getMessage() for record in caplog.records]
    assert any("code=cleanup_failed" in message for message in messages)
    assert all(str(tmpfs_dir) not in message for message in messages)
    assert all("RIFF" not in message for message in messages)


@pytest.mark.integration
async def test_incomplete_evidence_never_stamps_audio_deleted_at(
    pool: asyncpg.Pool,
) -> None:
    """S4.2: a bare ``verified`` claim is not proof of absence."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None

    assert await lease.record_cleanup(
        pool, owned, verified=True,
        evidence={**PROVEN_CLEANUP, "open_descriptors": 1},
    ) is False
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None


@pytest.mark.integration
async def test_failed_cleanup_cannot_be_upgraded_to_verified(pool: asyncpg.Pool) -> None:
    """S4.2: a failed cleanup is terminal; later evidence cannot reopen it."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    assert await lease.record_cleanup(
        pool, owned, verified=False, evidence={"verified": False, "error": "OSError"},
    )

    assert await lease.record_cleanup(
        pool, owned, verified=True, evidence=PROVEN_CLEANUP,
    ) is False
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "failed"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None


@pytest.mark.integration
async def test_failed_cleanup_clears_a_previous_audio_deleted_at(pool: asyncpg.Pool) -> None:
    """S4.2: moving to `failed` must not leave the deletion stamp from an earlier pass."""
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    assert await lease.record_cleanup(
        pool, owned, verified=True, evidence=PROVEN_CLEANUP,
    )
    row = await _row(pool, attempt)
    assert row["cleanup_status"] == "verified"
    assert row["cleanup_verified_at"] is not None
    assert row["audio_deleted_at"] is not None

    assert await lease.record_cleanup(
        pool, owned, verified=False, evidence={"verified": False, "error": "OSError"},
    )
    row = await _row(pool, attempt)

    assert row["cleanup_status"] == "failed"
    assert row["cleanup_verified_at"] is None
    assert row["audio_deleted_at"] is None


@pytest.mark.integration
async def test_failed_cleanup_blocks_a_new_owner(pool: asyncpg.Pool) -> None:
    """S4.2: the failed latch also fences acquisition, not only ``reclaim_committed``."""
    attempt = await _seed(pool)
    await pool.execute(
        """UPDATE ingestion_attempts
              SET status = 'transcribing', cleanup_status = 'failed',
                  lease_expires_at = now() - interval '1 second'
            WHERE id = $1""",
        attempt.attempt_id,
    )

    assert await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:b",
        ttl=timedelta(seconds=30),
    ) is None


@pytest.mark.integration
async def test_unrecorded_cleanup_on_a_moved_token_stays_pending_and_alerts(
    pool: asyncpg.Pool,
) -> None:
    """Hoplite fix2 2: a live run whose token moved cannot persist ``failed``.

    ``sweep_stranded`` is the owner that claims the row after the lease expires.
    The zombie's unverified evidence matches no row, so ``cleanup_status`` stays
    ``pending`` and admission is not closed; the explicit alert and metric are the
    observable record of the unverified cleanup.
    """
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id,
        instance="ingest:zombie", ttl=timedelta(seconds=30),
    )
    assert owned is not None
    await pool.execute(
        """UPDATE ingestion_attempts
              SET status = 'transcribing', lease_expires_at = now() - interval '1 second'
            WHERE id = $1""",
        attempt.attempt_id,
    )
    assert attempt.attempt_id in await sweep_stranded(pool)

    metrics.reset()
    result = supervisor.RunResult(Outcome.LEASE_LOST)
    await supervisor._note_cleanup_evidence(
        pool, owned, attempt.attempt_id, _unverified_evidence(), owned_audio=True,
        committed=False, result=result, audio_id=None, version=None,
    )
    row = await _row(pool, attempt)

    assert row["cleanup_status"] == "pending"
    assert row["cleanup_verified_at"] is None and row["audio_deleted_at"] is None
    assert admission.uploads_admitted() is True
    assert admission.alerts() == (
        admission.Alert("cleanup_unrecorded", attempt.attempt_id),
    )
