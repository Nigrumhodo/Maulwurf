"""A failed rmtree is retried by the directory sweep, and a live lease is skipped."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from maulwurf_ingest import admission, cleanup, reconciler
from maulwurf_ingest.lease import Lease


async def test_sweep_removes_a_directory_after_rmtree_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = uuid.uuid4()
    workdir = tmp_path / f"attempt-{attempt_id}-aaaa"
    workdir.mkdir()
    (workdir / "original").write_bytes(b"RIFF")
    kept_id = uuid.uuid4()
    kept = tmp_path / f"attempt-{kept_id}-bbbb"
    kept.mkdir()
    (kept / "original").write_bytes(b"RIFF")
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path: Path, ignore_errors: bool = False) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            return
        real(path, ignore_errors=ignore_errors)

    monkeypatch.setattr("maulwurf_ingest.cleanup.shutil.rmtree", flaky)
    evidence = cleanup.destroy_and_verify(workdir, None, settle_s=0)
    assert evidence.workdir_absent is False
    assert workdir.exists()

    swept = await cleanup.sweep_orphan_dirs(tmp_path, protected={kept_id})

    assert workdir.exists() is False
    assert kept.exists()
    assert attempt_id in {item.attempt_id for item in swept}
    assert kept_id not in {item.attempt_id for item in swept}
    assert calls["n"] >= 2


async def test_db_sweep_skips_a_live_lease_and_records_a_terminal_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_id, done_id = uuid.uuid4(), uuid.uuid4()
    user_id = uuid.uuid4()
    live = tmp_path / f"attempt-{live_id}-live"
    done = tmp_path / f"attempt-{done_id}-done"
    live.mkdir()
    done.mkdir()
    (live / "original").write_bytes(b"keep")
    (done / "original").write_bytes(b"drop")
    recorded: list[Lease] = []

    async def fake_fetch(
        _pool: object, _user: object, _query: str, *_args: object,
    ) -> list[dict[str, object]]:
        return [
            {
                "id": live_id, "user_id": user_id, "fencing_token": 1,
                "status": "transcribing", "lease_live": True,
            },
            {
                "id": done_id, "user_id": user_id, "fencing_token": 4,
                "status": "requires_reupload", "lease_live": False,
            },
        ]

    async def fake_record(
        _pool: object, lease: Lease, *, verified: bool, evidence: dict[str, object],
    ) -> bool:
        recorded.append(lease)
        assert isinstance(verified, bool)
        assert "verified" in evidence
        return True

    monkeypatch.setattr("maulwurf_ingest.reconciler.rls.fetch", fake_fetch)
    monkeypatch.setattr("maulwurf_ingest.reconciler.leases.record_cleanup", fake_record)

    # A non-tmpfs pytest directory cannot prove verified destruction, so the
    # sweep closes admission. Restore the latch so later tests can receive.
    admission.reset()
    try:
        removed = await reconciler.sweep_orphan_dirs(object(), tmp_dir=str(tmp_path))
    finally:
        admission.reset()

    assert live.exists()
    assert done.exists() is False
    assert done_id in removed
    assert live_id not in removed
    assert [item.fencing_token for item in recorded] == [4]
