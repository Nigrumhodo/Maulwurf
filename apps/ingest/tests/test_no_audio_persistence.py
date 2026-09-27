"""I-S1-SG-06 (S1.B5, G1): cero audio en disco, Redis, logs, trazas y cachés.

El informe es de conteos. No guarda rutas, bytes ni dumps. El audio de la parte de
integración es sintético y solo vive en el tmpfs del intento.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import (  # noqa: E402
    _config,
    _seed,
    audio,
    tmpfs_dir,
)

from maulwurf_ingest.asr_job import ORIGINAL
from maulwurf_ingest.audio_absence import (
    _Counts,
    _walk,
    report_from_tree,
    scannable_mount_points,
    sweep,
)
from maulwurf_ingest.supervisor import ChildHook, Outcome, RunResult, run_attempt

# Re-export fixtures so this module collects them.
_ = (audio, tmpfs_dir)

_MOUNTINFO = "\n".join([
    "1 0 0:1 / / rw - tmpfs tmpfs rw,size=1m",
    "2 1 0:2 / /proc rw - proc proc rw",
    "3 1 0:3 / /mnt/c rw - drvfs C: rw",
    "4 1 0:4 / /dev rw - devtmpfs udev rw",
])


def test_scan_detects_asr_artifacts_and_report_has_no_paths(tmp_path: Path) -> None:
    (tmp_path / "original").write_bytes(b"RIFF" + b"\x00" * 8)
    (tmp_path / "converted.wav").write_bytes(b"RIFFxxxxWAVE")
    (tmp_path / "frag-0000.wav").write_bytes(b"not-audio!!")
    (tmp_path / "nota.txt").write_bytes(b"texto")
    try:
        report = report_from_tree(tmp_path)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    assert report.audio_files == 3
    assert report.magic_matches == 2
    assert report.clear is False
    payload = report.as_json()
    assert all(isinstance(value, (bool, int)) for value in payload.values())
    dumped = json.dumps(payload)
    assert "original" not in dumped
    assert "frag-" not in dumped
    assert str(tmp_path) not in dumped


def test_empty_tree_is_clear(tmp_path: Path) -> None:
    report = report_from_tree(tmp_path)

    assert report.audio_files == 0
    assert report.magic_matches == 0
    assert report.clear is True


def test_pruned_infra_child_is_not_scanned(tmp_path: Path) -> None:
    hidden = tmp_path / "usr"
    hidden.mkdir()
    (hidden / "original").write_bytes(b"RIFF" + b"\x00" * 8)
    (tmp_path / "converted.wav").write_bytes(b"RIFFxxxxWAVE")
    counts = _Counts()
    _walk(tmp_path, counts, suffixes=False, budget=1000, prune={os.path.normpath(hidden)})

    assert counts.audio_files == 1
    assert counts.complete is True


def test_infra_mount_is_counted_and_not_walked(tmp_path: Path) -> None:
    (tmp_path / "converted.wav").write_bytes(b"RIFFxxxxWAVE")
    mountinfo = "\n".join([
        f"1 0 0:1 / {tmp_path} rw - tmpfs tmpfs rw,size=1m",
        "2 1 8:1 / /mnt/ci rw - ext4 /dev/sdb rw",
    ])
    try:
        report = sweep(mountinfo=mountinfo, cache_roots=[], redis_url=None)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    assert report.audio_files == 1
    assert report.mounts_scanned == 1
    assert report.infra_pruned == 1
    assert "mnt" not in json.dumps(report.as_json())


def test_scannable_mounts_skip_pseudo_and_foreign_disks() -> None:
    points = scannable_mount_points(_MOUNTINFO)

    assert points == [os.path.normpath("/")]


def test_sweep_of_a_mount_sees_artifacts_without_dumping_them(tmp_path: Path) -> None:
    (tmp_path / "original").write_bytes(b"OggSxxxx")
    mountinfo = f"1 0 0:1 / {tmp_path} rw - tmpfs tmpfs rw,size=1m"
    try:
        report = sweep(mountinfo=mountinfo, cache_roots=[], redis_url=None)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)

    assert report.audio_files == 1
    assert report.magic_matches == 1
    assert report.mounts_scanned == 1
    assert report.infra_pruned == 0
    assert "OggS" not in json.dumps(report.as_json())
    assert str(tmp_path) not in json.dumps(report.as_json())


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _run(
    pool: asyncpg.Pool,
    audio: bytes,
    tmpfs_dir: Path,
    on_child: ChildHook | None = None,
) -> RunResult:
    attempt = await _seed(pool)
    handler = _Capture()
    logger = logging.getLogger("maulwurf_ingest")
    logger.addHandler(handler)
    try:
        result = await run_attempt(
            pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, audio=audio,
            config=_config(tmpfs_dir), on_child=on_child,
        )
    finally:
        logger.removeHandler(handler)
    report = sweep(
        extra_roots=[tmpfs_dir],
        log_texts=handler.lines,
        trace_texts=[],
        redis_url=os.environ.get("MAULWURF_REDIS_URL"),
    )
    assert report.audio_files == 0
    assert report.magic_matches == 0
    assert report.cache_audio_files == 0
    assert report.redis_audio_markers == 0
    assert report.log_audio_markers == 0
    assert report.trace_audio_markers == 0
    assert report.traces_configured is False
    if os.environ.get("MAULWURF_REDIS_URL"):
        assert report.redis_checked is True
    assert report.clear is True
    dumped = json.dumps(report.as_json())
    assert "RIFF" not in dumped
    assert str(tmpfs_dir) not in dumped
    return result


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
async def test_success_leaves_no_audio_in_durable_sinks(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    """I-S1-SG-06: tras el intento no queda original, convertido ni fragmento."""
    result = await _run(pool, audio, tmpfs_dir)
    # En un /proc de un solo UID el cleanup queda verificado. Si hay procesos de
    # otro UID, la verificación falla cerrada (igual que S1.B4) pero el barrido
    # de nombres ya pasó dentro de `_run`.
    if result.evidence.get("unreadable_processes") == 0:
        assert result.outcome is Outcome.SUCCEEDED
    else:
        assert result.evidence.get("workdir_absent") is True


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
async def test_open_descriptor_still_leaves_no_named_audio(
    pool: asyncpg.Pool, audio: bytes, tmpfs_dir: Path
) -> None:
    """I-S1-SG-06: un descriptor abierto no deja el nombre en disco ni en los sinks."""
    held: list[int] = []

    def _hold(_pid: int, workdir: Path) -> None:
        held.append(os.open(workdir / ORIGINAL, os.O_RDONLY))

    try:
        result = await _run(pool, audio, tmpfs_dir, on_child=_hold)
    finally:
        for fd in held:
            os.close(fd)
    assert result.outcome is Outcome.CLEANUP_FAILED
