"""S4.2: periodic recovery sweeper (60 s target) and fail-closed pass handling.

`L-S4-SG-01` covers the load leg in S4.3. These unit cases pin the missing contract:
one pass at a time, the documented 60 s cadence, a failed pass that closes admission
without killing the loop, and the composition of the post-commit reconciler with the
stranded sweep.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import cast

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from maulwurf_ingest import admission, hardening, main, supervisor
from maulwurf_ingest.config import Settings, settings
from maulwurf_ingest.supervisor import SweepResult, run_sweeper, sweep_once, sweep_pass


def _pool() -> asyncpg.Pool:
    return cast(asyncpg.Pool, object())


@pytest.fixture(autouse=True)
def _reset_admission() -> Iterator[None]:
    admission.reset()
    yield
    admission.reset()


@pytest.fixture(autouse=True)
def _readable_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: the latch read succeeds and leaves admission as it is.

    Tests that exercise the read override this fixture.
    """

    async def _noop(_pool: asyncpg.Pool) -> None:
        return None

    monkeypatch.setattr(admission, "restore_from_database", _noop)


def test_default_sweep_interval_is_60_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_SWEEP_INTERVAL_S", raising=False)
    assert Settings().sweep_interval_s == 60.0
    assert supervisor.SWEEP_INTERVAL_S == 60.0


def test_sweep_interval_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_SWEEP_INTERVAL_S", "5")
    assert Settings().sweep_interval_s == 5.0


def test_sweep_interval_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_SWEEP_INTERVAL_S", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_default_heartbeat_leaves_three_beats_before_lease_expiry() -> None:
    """S4.2: one late heartbeat (or a short event-loop stall) must not fence a live run."""
    config = supervisor.SupervisorConfig(instance="ingest:test")

    assert config.heartbeat_interval_s == 10.0
    assert config.lease_ttl == timedelta(seconds=30)
    assert (
        config.heartbeat_interval_s * supervisor.MIN_HEARTBEATS_PER_LEASE
        <= config.lease_ttl.total_seconds()
    )


def test_a_heartbeat_without_margin_is_rejected() -> None:
    with pytest.raises(ValueError, match="margin"):
        supervisor.SupervisorConfig(
            instance="ingest:test",
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval_s=15.0,
        )


async def test_sweeper_repeats_the_pass_on_the_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    finished = asyncio.Event()

    async def fake_sweep(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        nonlocal calls
        calls += 1
        if calls >= 3:
            finished.set()
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(supervisor, "sweep_once", fake_sweep)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.01),
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 3


async def test_passes_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    active = peak = calls = 0
    finished = asyncio.Event()

    async def slow_sweep(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        if calls >= 2:
            finished.set()
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(supervisor, "sweep_once", slow_sweep)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.001),
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert peak == 1


async def test_a_failed_pass_closes_admission_and_the_loop_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    finished = asyncio.Event()
    canary = "postgresql://maulwurf:CANARY-4f2a@db/maulwurf"

    async def failing_then_ok(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(canary)
        finished.set()
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(supervisor, "sweep_once", failing_then_ok)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.01),
    )
    try:
        with caplog.at_level(logging.ERROR, logger="maulwurf_ingest.supervisor"):
            await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls >= 2
    assert admission.uploads_admitted() is False
    assert admission.alerts() == ()
    assert "supervisor.sweep_failed error=OSError" in caplog.text
    assert "CANARY-4f2a" not in caplog.text


async def test_sweep_interval_must_be_positive_at_runtime() -> None:
    with pytest.raises(ValueError, match="interval"):
        await run_sweeper(_pool(), instance="ingest:test", interval_s=0)


async def test_every_pass_resyncs_the_persisted_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite 1: a `failed` row written after startup closes admission here."""
    foreign = uuid.uuid4()
    reads = 0
    finished = asyncio.Event()

    async def latch_appears_on_second_read(_pool: asyncpg.Pool) -> None:
        nonlocal reads
        reads += 1
        if reads >= 2:
            admission.halt(foreign)
            finished.set()

    async def ok_sweep(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(admission, "restore_from_database", latch_appears_on_second_read)
    monkeypatch.setattr(supervisor, "sweep_once", ok_sweep)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.01),
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert reads >= 2
    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", foreign),)


async def test_a_clean_pass_reopens_a_transient_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite 2: a database outage closes admission, a clean pass reopens it."""
    calls = 0
    finished = asyncio.Event()

    async def clean_latch(_pool: asyncpg.Pool) -> None:
        admission.reopen()

    async def failing_then_ok(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("database down")
        finished.set()
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(admission, "restore_from_database", clean_latch)
    monkeypatch.setattr(supervisor, "sweep_once", failing_then_ok)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.01),
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls >= 2
    assert admission.uploads_admitted() is True
    assert admission.alerts() == ()


async def test_a_persisted_failed_latch_is_not_reopened_by_clean_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hoplite 2: the durable latch stays closed; only the transient close reopens."""
    foreign = uuid.uuid4()
    calls = 0
    finished = asyncio.Event()

    async def failed_latch(_pool: asyncpg.Pool) -> None:
        admission.halt(foreign)

    async def ok_sweep(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        nonlocal calls
        calls += 1
        if calls >= 2:
            finished.set()
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(admission, "restore_from_database", failed_latch)
    monkeypatch.setattr(supervisor, "sweep_once", ok_sweep)
    task = asyncio.create_task(
        run_sweeper(_pool(), instance="ingest:test", interval_s=0.01),
    )
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls >= 2
    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", foreign),)


async def test_sweep_pass_reads_the_latch_before_recovering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    order: list[str] = []

    async def fake_restore(_pool: asyncpg.Pool) -> None:
        order.append("latch")

    async def fake_sweep(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
    ) -> SweepResult:
        order.append(f"sweep:{instance}:{tmp_dir}")
        return SweepResult(committed=(), stranded=())

    monkeypatch.setattr(admission, "restore_from_database", fake_restore)
    monkeypatch.setattr(supervisor, "sweep_once", fake_sweep)
    result = await sweep_pass(_pool(), instance="ingest:test", tmp_dir=str(tmp_path))

    assert order == ["latch", f"sweep:ingest:test:{tmp_path}"]
    assert result == SweepResult(committed=(), stranded=())


async def test_sweep_once_reconciles_then_ends_stranded_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    order: list[str] = []

    async def fake_reconcile(
        _pool: asyncpg.Pool, *, instance: str, tmp_dir: str | None = None,
        lease_ttl: object = None,
    ) -> tuple[object, ...]:
        order.append(f"reconcile:{instance}:{tmp_dir}")
        return ()

    async def fake_stranded(_pool: asyncpg.Pool) -> tuple[object, ...]:
        order.append("stranded")
        return ()

    monkeypatch.setattr(supervisor, "reconcile_committed", fake_reconcile)
    monkeypatch.setattr(supervisor, "sweep_stranded", fake_stranded)
    result = await sweep_once(_pool(), instance="ingest:test", tmp_dir=str(tmp_path))

    assert order == [f"reconcile:ingest:test:{tmp_path}", "stranded"]
    assert result == SweepResult(committed=(), stranded=())


def test_start_sweeper_without_a_pool_starts_nothing() -> None:
    app = FastAPI()
    app.state.pool = None
    assert main.start_sweeper(app) is None


def test_lifespan_starts_and_stops_the_sweeper(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[object, str, float]] = []

    async def fake_run(
        pool: object, *, instance: str, interval_s: float, tmp_dir: str | None = None,
    ) -> None:
        started.append((pool, instance, interval_s))

    sentinel = object()

    async def _skip() -> None:
        return None

    monkeypatch.setattr(main, "run_sweeper", fake_run)
    monkeypatch.setattr(main, "_sweeper_pool", lambda _app: sentinel)
    monkeypatch.setattr(hardening, "check", lambda _: [])
    monkeypatch.setattr(settings, "ingest_hardening", "report")
    monkeypatch.setattr(settings, "database_url", None)
    monkeypatch.setattr(main, "reconcile_on_startup", _skip)
    monkeypatch.setattr(main, "open_service_pool", lambda _app: _skip())
    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200
        assert main.app.state.sweeper is not None
    assert main.app.state.sweeper is None
    assert len(started) == 1
    pool, instance, interval = started[0]
    assert pool is sentinel
    assert instance.startswith("ingest:")
    assert interval == settings.sweep_interval_s
