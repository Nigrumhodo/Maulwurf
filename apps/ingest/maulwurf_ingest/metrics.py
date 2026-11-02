"""Ingest metrics (J2.5): RAM, tmpfs, slots, temporary age and failures.

Everything exposed is a boolean, a count, or a byte or second value. Never paths with user
data or file names: an attempt directory name embeds the `attempt_id`, so only its age is
read.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path
from typing import Final

from maulwurf_ingest.prometheus_text import Sample

MEMINFO: Final = Path("/proc/meminfo")
ATTEMPT_DIR_PREFIX: Final = "attempt-"

_failures: Counter[tuple[str, str]] = Counter()
_cleanup_failures = 0


def record_failure(stage: str, error_code: str) -> None:
    """Count one failure by stage and by ingest error-code catalog entry."""
    _failures[(stage, error_code)] += 1


def record_cleanup_failure() -> None:
    """Count one unverified cleanup (immediate `cleanup.failed` alert)."""
    global _cleanup_failures
    _cleanup_failures += 1


def reset() -> None:
    """Tests only: clear the process counters."""
    global _cleanup_failures
    _failures.clear()
    _cleanup_failures = 0


def memory_available_bytes(meminfo: Path = MEMINFO) -> int | None:
    """`MemAvailable` from /proc/meminfo in bytes, or None when unreadable."""
    try:
        text = meminfo.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            kib = line.split()[1]
            return int(kib) * 1024
    return None


def oldest_temporary_age_s(tmp_dir: str, *, now: float) -> float:
    """Age in seconds of the oldest attempt directory; 0 when there is none."""
    oldest = 0.0
    try:
        entries = list(Path(tmp_dir).iterdir())
    except OSError:
        return 0.0
    for entry in entries:
        if not entry.name.startswith(ATTEMPT_DIR_PREFIX):
            continue
        try:
            age = now - entry.lstat().st_mtime
        except OSError:
            continue
        oldest = max(oldest, age)
    return max(0.0, oldest)


def tmpfs_bytes(tmp_dir: str) -> tuple[int, int]:
    """(available bytes, total bytes) of the filesystem holding `tmp_dir`."""
    stat = os.statvfs(tmp_dir)
    return stat.f_bavail * stat.f_frsize, stat.f_blocks * stat.f_frsize


def collect_samples(
    *,
    tmp_dir: str,
    slots_in_use: int,
    slots_total: int,
    admission_open: bool,
    now: float | None = None,
) -> list[Sample]:
    """Full instance series at one instant."""
    instant = time.time() if now is None else now
    free, total = tmpfs_bytes(tmp_dir)
    ram = memory_available_bytes()
    samples = [
        Sample("maulwurf_ingest_tmpfs_free_bytes", "gauge", "Free bytes in the tmpfs.", free),
        Sample("maulwurf_ingest_tmpfs_total_bytes", "gauge", "Total bytes in the tmpfs.", total),
        Sample(
            "maulwurf_ingest_temporary_oldest_age_seconds",
            "gauge",
            "Age of the oldest temporary attempt directory.",
            oldest_temporary_age_s(tmp_dir, now=instant),
        ),
        Sample(
            "maulwurf_ingest_slots_in_use", "gauge", "Reception slots in use.", slots_in_use,
        ),
        Sample(
            "maulwurf_ingest_slots_total", "gauge", "Configured reception slots.", slots_total,
        ),
        Sample(
            "maulwurf_ingest_admission_open",
            "gauge",
            "1 when the instance accepts new uploads; 0 after a failed cleanup.",
            1 if admission_open else 0,
        ),
        Sample(
            "maulwurf_ingest_cleanup_failed_total",
            "counter",
            "Temporary-file cleanups not verified since start.",
            _cleanup_failures,
        ),
    ]
    if ram is not None:
        samples.append(
            Sample(
                "maulwurf_ingest_memory_available_bytes",
                "gauge",
                "MemAvailable of the host as seen by the process.",
                ram,
            )
        )
    samples.extend(
        Sample(
            "maulwurf_ingest_failures_total",
            "counter",
            "Attempt failures by stage and error code.",
            float(count),
            {"stage": stage, "error_code": code},
        )
        for (stage, code), count in sorted(_failures.items())
    )
    return samples
