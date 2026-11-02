"""S3.5: ASR effective-minute cost and RAM/tmpfs under concurrent load.

Basis for **C-S4-SG-04** (the reproducible cost table of S4.5). The harness measures two
different things and keeps them separate:

* **ASR cost:** the audio seconds a run would send to the provider (fragments, including the
  local overlap), projected to one class hour. The provider rate measured by the F0.1
  spike is ``NO VERIFICADO`` (S1.A7 never obtained a price), so the rate is an explicit
  operator input; without it the cost stays ``NO VERIFICADO`` instead of inventing a
  number.
* **RAM and tmpfs under load:** ``--slots`` concurrent worker processes, each running the
  production ``pipeline.process`` audio path (ffprobe, silencedetect, ffmpeg fragment
  extraction) used by ``asr_job``, over a synthetic clip generated in RAM. Only the
  transcriber is replaced by an empty-words stub: no Riva channel, no key and no provider
  call. The original and the active fragment live on a private tmpfs directory that every
  worker deletes and verifies.

Run from the repository root with the ingest environment::

    cd apps/ingest && uv run python ../../scripts/load/asr_cost.py
    cd apps/ingest && uv run python ../../scripts/load/asr_cost.py --format json
    cd apps/ingest && uv run python ../../scripts/load/asr_cost.py \
        --rate-usd-per-minute 0.01 --clip-seconds 300

The harness refuses to run if it cannot find a writable tmpfs. It never writes audio to
persistent storage, never prints audio and never keeps a WAV after the run. The synthetic
audio is generated at execution time with espeak-ng and discarded.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT.parents[2]
_INGEST_ROOT = _REPO_ROOT / "apps" / "ingest"
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from maulwurf_ingest import hardening  # noqa: E402
from maulwurf_ingest.audio.fragmenter import wav_duration_s  # noqa: E402
from maulwurf_ingest.audio.synthetic import (  # noqa: E402
    DEFAULT_PHRASE_ES,
    generate_synthetic_utterance,
)
from maulwurf_ingest.capacity import (  # noqa: E402
    APPROVED_3H,
    APPROVED_200_MIB,
    PROVISIONAL_MEM_LIMIT_BYTES,
    PROVISIONAL_SLOTS,
    PROVISIONAL_TMPFS_BYTES,
)
from maulwurf_ingest.config import settings  # noqa: E402
from maulwurf_ingest.pipeline import FragmentWords  # noqa: E402
from maulwurf_ingest.pipeline import process as process_audio  # noqa: E402

NOT_VERIFIED = "NO VERIFICADO"
TICKET = "S3.5"
TEST_BASIS = "C-S4-SG-04"

DEFAULT_CLIP_SECONDS = 60.0
MIN_CLIP_SECONDS = 5.0
MAX_CLIP_SECONDS = 300.0
MAX_SLOTS = 4
DEFAULT_WORKER_TIMEOUT_S = 300.0
SAMPLE_INTERVAL_S = 0.01
_TMPFS_PREFIX = "mw-s35-"
_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True)
class AsrUsage:
    """Audio the provider is billed for, and how much overlap the plan adds."""

    source_seconds: float
    fragments: int
    billed_seconds: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.source_seconds) or self.source_seconds <= 0:
            raise ValueError("source_seconds must be a positive finite number")
        if self.fragments < 1:
            raise ValueError("fragments must be >= 1")
        if not math.isfinite(self.billed_seconds) or self.billed_seconds <= 0:
            raise ValueError("billed_seconds must be a positive finite number")
        if self.billed_seconds + 1e-3 < self.source_seconds:
            raise ValueError("billed_seconds cannot be below the source duration")

    @property
    def billed_minutes(self) -> float:
        return self.billed_seconds / 60

    @property
    def overlap_seconds(self) -> float:
        return max(0.0, self.billed_seconds - self.source_seconds)

    @property
    def amplification(self) -> float:
        return self.billed_seconds / self.source_seconds

    @property
    def effective_minutes_per_class_hour(self) -> float:
        """Billed minutes per one hour of source audio; the ASR cost driver."""
        hours = self.source_seconds / 3600
        return self.billed_minutes / hours


def usage_from_spans(
    spans: Sequence[tuple[float, float]],
    *,
    source_seconds: float | None = None,
) -> AsrUsage:
    """Build the usage from ordered fragment spans, validating the plan invariants."""
    if not spans:
        raise ValueError("at least one fragment span is required")
    total = 0.0
    previous_start = -math.inf
    for index, (start, end) in enumerate(spans):
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            raise ValueError(f"fragment {index} has an invalid span")
        if start < previous_start:
            raise ValueError("fragment spans must be ordered by start")
        previous_start = start
        total += end - start
    last_end = spans[-1][1]
    resolved_source = last_end if source_seconds is None else source_seconds
    if not math.isfinite(resolved_source) or resolved_source <= 0:
        raise ValueError("source_seconds must be a positive finite number")
    if last_end > resolved_source + 1e-6:
        raise ValueError("fragment spans cannot exceed the source duration")
    return AsrUsage(
        source_seconds=resolved_source,
        fragments=len(spans),
        billed_seconds=total,
    )


@dataclass(frozen=True)
class AsrCost:
    """Cost rows. A missing rate never becomes a number."""

    usage: AsrUsage
    rate_usd_per_minute: float | None
    cost_usd: float | str
    cost_per_class_hour_usd: float | str

    @property
    def rate_status(self) -> str:
        return NOT_VERIFIED if self.rate_usd_per_minute is None else "operator-provided"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate_usd_per_minute": self.rate_usd_per_minute,
            "rate_status": self.rate_status,
            "rate_source": (
                "F0.1 spike S1.A7: NO VERIFICADO (no authorized rate was measured)"
            ),
            "billed_minutes": round(self.usage.billed_minutes, 6),
            "effective_minutes_per_class_hour": round(
                self.usage.effective_minutes_per_class_hour, 6
            ),
            "cost_usd": _rounded(self.cost_usd, 6),
            "cost_per_class_hour_usd": _rounded(self.cost_per_class_hour_usd, 6),
            "formula": "effective minutes per class hour x rate per minute",
        }


def estimate_asr_cost(
    usage: AsrUsage,
    rate_usd_per_minute: float | None,
) -> AsrCost:
    """Apply an explicit rate. Without a measured rate the cost rows stay unverified."""
    if rate_usd_per_minute is None:
        return AsrCost(
            usage=usage,
            rate_usd_per_minute=None,
            cost_usd=NOT_VERIFIED,
            cost_per_class_hour_usd=NOT_VERIFIED,
        )
    if (
        isinstance(rate_usd_per_minute, bool)
        or not math.isfinite(rate_usd_per_minute)
        or rate_usd_per_minute <= 0
    ):
        raise ValueError("rate_usd_per_minute must be a positive finite number")
    per_class_hour = usage.effective_minutes_per_class_hour * rate_usd_per_minute
    return AsrCost(
        usage=usage,
        rate_usd_per_minute=rate_usd_per_minute,
        cost_usd=usage.billed_minutes * rate_usd_per_minute,
        cost_per_class_hour_usd=per_class_hour,
    )


@dataclass(frozen=True)
class SlotMeasurement:
    """One concurrent slot: the real audio path plus its RAM/tmpfs peaks."""

    slot: int
    fragments: int
    billed_seconds: float
    wall_seconds: float
    tmpfs_peak_bytes: int
    tmpfs_after_bytes: int
    rss_before_bytes: int | None
    rss_peak_bytes: int | None
    rss_after_bytes: int | None
    ffmpeg_child_maxrss_bytes: int | None
    cleanup_removed: bool
    # CPU consumed by the ffmpeg/ffprobe children; ``None`` on platforms without
    # ``RUSAGE_CHILDREN`` or in captures taken before S4.5.
    cpu_user_seconds: float | None = None
    cpu_system_seconds: float | None = None

    @property
    def rss_delta_bytes(self) -> int | None:
        if self.rss_before_bytes is None or self.rss_peak_bytes is None:
            return None
        return max(0, self.rss_peak_bytes - self.rss_before_bytes)

    @property
    def cpu_total_seconds(self) -> float | None:
        if self.cpu_user_seconds is None or self.cpu_system_seconds is None:
            return None
        return self.cpu_user_seconds + self.cpu_system_seconds

    @property
    def peak_working_set_bytes(self) -> int | None:
        """RAM held by the slot: worker RSS plus the tmpfs original/fragment peak."""
        if self.rss_peak_bytes is None:
            return None
        return self.rss_peak_bytes + self.tmpfs_peak_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "fragments": self.fragments,
            "billed_seconds": round(self.billed_seconds, 6),
            "wall_seconds": round(self.wall_seconds, 6),
            "tmpfs_peak_bytes": self.tmpfs_peak_bytes,
            "tmpfs_after_bytes": self.tmpfs_after_bytes,
            "rss_before_bytes": self.rss_before_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "rss_after_bytes": self.rss_after_bytes,
            "rss_delta_bytes": self.rss_delta_bytes,
            "ffmpeg_child_maxrss_bytes": self.ffmpeg_child_maxrss_bytes,
            "cleanup_removed": self.cleanup_removed,
            "cpu_user_seconds": _rounded_optional(self.cpu_user_seconds, 6),
            "cpu_system_seconds": _rounded_optional(self.cpu_system_seconds, 6),
            "cpu_total_seconds": _rounded_optional(self.cpu_total_seconds, 6),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SlotMeasurement:
        try:
            slot = _require_int(payload, "slot")
            fragments = _require_int(payload, "fragments")
            billed_seconds = _require_float(payload, "billed_seconds")
            wall_seconds = _require_float(payload, "wall_seconds")
            tmpfs_peak = _require_int(payload, "tmpfs_peak_bytes")
            tmpfs_after = _require_int(payload, "tmpfs_after_bytes")
            cleanup_removed = payload.get("cleanup_removed")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("worker measurement is incomplete") from exc
        if not isinstance(cleanup_removed, bool):
            raise ValueError("cleanup_removed must be a boolean")
        try:
            rss_before = _optional_int(payload, "rss_before_bytes")
            rss_peak = _optional_int(payload, "rss_peak_bytes")
            rss_after = _optional_int(payload, "rss_after_bytes")
            child_maxrss = _optional_int(payload, "ffmpeg_child_maxrss_bytes")
            cpu_user = _optional_float(payload, "cpu_user_seconds")
            cpu_system = _optional_float(payload, "cpu_system_seconds")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("worker measurement is incomplete") from exc
        if (cpu_user is None) != (cpu_system is None):
            raise ValueError("cpu seconds must be both present or both absent")
        return cls(
            slot=slot,
            fragments=fragments,
            billed_seconds=billed_seconds,
            wall_seconds=wall_seconds,
            tmpfs_peak_bytes=tmpfs_peak,
            tmpfs_after_bytes=tmpfs_after,
            rss_before_bytes=rss_before,
            rss_peak_bytes=rss_peak,
            rss_after_bytes=rss_after,
            ffmpeg_child_maxrss_bytes=child_maxrss,
            cleanup_removed=cleanup_removed,
            cpu_user_seconds=cpu_user,
            cpu_system_seconds=cpu_system,
        )


@dataclass(frozen=True)
class LoadReport:
    """Measured load plus the ASR cost projection. Nothing here approves a limit."""

    slots: int
    clip_seconds: float
    fragment_seconds: int
    overlap_seconds: float
    worker_timeout_s: float
    measurements: tuple[SlotMeasurement, ...]
    tmpfs_peak_sampled_bytes: int | None
    root_cleaned: bool
    usage: AsrUsage
    cost: AsrCost
    environment: dict[str, str]

    @property
    def tmpfs_peak_sum_bytes(self) -> int:
        """Conservative upper bound: per-slot peaks added even if they do not coincide."""
        return sum(measurement.tmpfs_peak_bytes for measurement in self.measurements)

    @property
    def worker_rss_peak_sum_bytes(self) -> int | None:
        peaks = [measurement.rss_peak_bytes for measurement in self.measurements]
        if any(peak is None for peak in peaks):
            return None
        return sum(peak for peak in peaks if peak is not None)

    @property
    def worker_rss_delta_sum_bytes(self) -> int | None:
        deltas = [measurement.rss_delta_bytes for measurement in self.measurements]
        if any(delta is None for delta in deltas):
            return None
        return sum(delta for delta in deltas if delta is not None)

    @property
    def ffmpeg_child_maxrss_bytes(self) -> int | None:
        values = [
            measurement.ffmpeg_child_maxrss_bytes
            for measurement in self.measurements
            if measurement.ffmpeg_child_maxrss_bytes is not None
        ]
        return max(values) if values else None

    @property
    def ffmpeg_cpu_user_seconds(self) -> float | None:
        return _sum_optional(
            measurement.cpu_user_seconds for measurement in self.measurements
        )

    @property
    def ffmpeg_cpu_system_seconds(self) -> float | None:
        return _sum_optional(
            measurement.cpu_system_seconds for measurement in self.measurements
        )

    @property
    def ffmpeg_cpu_total_seconds(self) -> float | None:
        """CPU of every ffmpeg/ffprobe child in the concurrent run (all slots)."""
        return _sum_optional(
            measurement.cpu_total_seconds for measurement in self.measurements
        )

    def _per_class_hour(self, value: float) -> float:
        return value / (self.clip_seconds / 3600)

    @property
    def ffmpeg_cpu_seconds_per_class_hour(self) -> float | None:
        """CPU seconds a single class hour needs, max across slots (conservative)."""
        values = [
            measurement.cpu_total_seconds for measurement in self.measurements
        ]
        if any(value is None for value in values):
            return None
        return self._per_class_hour(max(value for value in values if value is not None))

    @property
    def cpu_ratio(self) -> float | None:
        """Cores busy per slot at the peak: CPU seconds per wall second."""
        values = [
            measurement.cpu_total_seconds for measurement in self.measurements
        ]
        if any(value is None for value in values):
            return None
        ratios = [
            measurement.cpu_total_seconds / measurement.wall_seconds
            for measurement in self.measurements
            if measurement.cpu_total_seconds is not None
            and measurement.wall_seconds > 0
        ]
        return max(ratios) if ratios else None

    @property
    def wall_seconds_per_class_hour(self) -> float:
        """Local audio-path seconds per source hour, max across slots."""
        return self._per_class_hour(
            max(measurement.wall_seconds for measurement in self.measurements)
        )

    @property
    def peak_working_set_bytes(self) -> int | None:
        """Largest slot working set (worker RSS + tmpfs), the RAM cost driver."""
        values = [
            measurement.peak_working_set_bytes
            for measurement in self.measurements
            if measurement.peak_working_set_bytes is not None
        ]
        return max(values) if values else None

    @property
    def ram_gib_hours_per_class_hour(self) -> float | None:
        """GiB-hours held per class hour of local processing (lower bound)."""
        peak = self.peak_working_set_bytes
        if peak is None:
            return None
        wall_hours = self.wall_seconds_per_class_hour / 3600
        return (peak / _GIB) * wall_hours

    @property
    def worker_cleanup_all_removed(self) -> bool:
        return all(measurement.cleanup_removed for measurement in self.measurements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": TICKET,
            "test_basis": TEST_BASIS,
            "environment": self.environment,
            "load": {
                "slots": self.slots,
                "concurrent": True,
                "clip_seconds": self.clip_seconds,
                "fragment_cap_seconds": self.fragment_seconds,
                "overlap_seconds": self.overlap_seconds,
                "worker_timeout_s": self.worker_timeout_s,
            },
            "slots": [measurement.to_dict() for measurement in self.measurements],
            "aggregate": {
                "fragments_per_slot": self.measurements[0].fragments,
                "billed_seconds_per_slot": round(self.measurements[0].billed_seconds, 6),
                "tmpfs_peak_sum_of_slot_maxima_bytes": self.tmpfs_peak_sum_bytes,
                "tmpfs_peak_sampled_bytes": self.tmpfs_peak_sampled_bytes,
                "worker_rss_peak_sum_bytes": self.worker_rss_peak_sum_bytes,
                "worker_rss_delta_sum_bytes": self.worker_rss_delta_sum_bytes,
                "ffmpeg_child_maxrss_bytes": self.ffmpeg_child_maxrss_bytes,
                "ffmpeg_cpu_user_seconds": _rounded_optional(
                    self.ffmpeg_cpu_user_seconds, 6
                ),
                "ffmpeg_cpu_system_seconds": _rounded_optional(
                    self.ffmpeg_cpu_system_seconds, 6
                ),
                "ffmpeg_cpu_total_seconds": _rounded_optional(
                    self.ffmpeg_cpu_total_seconds, 6
                ),
                "ffmpeg_cpu_seconds_per_class_hour": _rounded_optional(
                    self.ffmpeg_cpu_seconds_per_class_hour, 6
                ),
                "cpu_ratio": _rounded_optional(self.cpu_ratio, 6),
                "wall_seconds_per_class_hour": round(
                    self.wall_seconds_per_class_hour, 6
                ),
                "peak_working_set_bytes": self.peak_working_set_bytes,
                "ram_gib_hours_per_class_hour": _rounded_optional(
                    self.ram_gib_hours_per_class_hour, 9
                ),
                "fits_provisional_tmpfs_upper_bound": (
                    self.tmpfs_peak_sum_bytes <= PROVISIONAL_TMPFS_BYTES
                ),
                "fits_provisional_mem_limit_upper_bound": (
                    None
                    if self.worker_rss_peak_sum_bytes is None
                    else self.worker_rss_peak_sum_bytes <= PROVISIONAL_MEM_LIMIT_BYTES
                ),
                "worker_cleanup_all_removed": self.worker_cleanup_all_removed,
                "harness_tmpfs_root_cleaned": self.root_cleaned,
            },
            "asr_usage": {
                "source_seconds": self.usage.source_seconds,
                "fragments": self.usage.fragments,
                "billed_seconds": round(self.usage.billed_seconds, 6),
                "overlap_seconds": round(self.usage.overlap_seconds, 6),
                "amplification": round(self.usage.amplification, 6),
                "billed_minutes": round(self.usage.billed_minutes, 6),
                "effective_minutes_per_class_hour": round(
                    self.usage.effective_minutes_per_class_hour, 6
                ),
            },
            "asr_cost": self.cost.to_dict(),
            "limits": {
                "provisional_tmpfs_bytes": PROVISIONAL_TMPFS_BYTES,
                "provisional_mem_limit_bytes": PROVISIONAL_MEM_LIMIT_BYTES,
                "provisional_slots": PROVISIONAL_SLOTS,
                "approved_200_mib": APPROVED_200_MIB,
                "approved_3h": APPROVED_3H,
                "limits_validated": False,
            },
            "notes": _limitations(),
        }

    def render_table(self) -> str:
        environment = ", ".join(
            f"{key}={value}" for key, value in self.environment.items()
        )
        lines = [
            f"{TICKET} ASR cost and RAM/tmpfs under load (basis for {TEST_BASIS})",
            f"Environment: {environment}",
            (
                f"Load: {self.slots} concurrent slot(s), {self.clip_seconds:.1f} s synthetic "
                f"clip, fragment cap {self.fragment_seconds} s, overlap "
                f"{self.overlap_seconds:.2f} s"
            ),
            (
                f"{'slot':>4} {'frags':>6} {'billed_s':>9} {'wall_s':>8} "
                f"{'tmpfs_peak':>12} {'tmpfs_after':>12} {'rss_before':>11} "
                f"{'rss_peak':>10} {'rss_delta':>10}"
            ),
        ]
        for measurement in self.measurements:
            lines.append(
                f"{measurement.slot:>4} {measurement.fragments:>6} "
                f"{measurement.billed_seconds:>9.3f} {measurement.wall_seconds:>8.3f} "
                f"{_bytes_text(measurement.tmpfs_peak_bytes):>12} "
                f"{_bytes_text(measurement.tmpfs_after_bytes):>12} "
                f"{_bytes_text(measurement.rss_before_bytes):>11} "
                f"{_bytes_text(measurement.rss_peak_bytes):>10} "
                f"{_bytes_text(measurement.rss_delta_bytes):>10}"
            )
        usage = self.usage
        lines.extend(
            [
                (
                    f"Cleanup: all slot dirs removed by their worker="
                    f"{_yes_no(self.worker_cleanup_all_removed)}; harness tmpfs root "
                    f"emptied={_yes_no(self.root_cleaned)}"
                ),
                (
                    f"ASR usage per slot: {usage.fragments} fragments, "
                    f"{usage.billed_seconds:.3f} billed s, overlap "
                    f"{usage.overlap_seconds:.3f} s, amplification "
                    f"{usage.amplification:.6f}"
                ),
                (
                    "ASR effective minutes per class hour: "
                    f"{usage.effective_minutes_per_class_hour:.3f}"
                ),
                (
                    f"ASR rate: {self.cost.rate_status}"
                    + (
                        ""
                        if self.cost.rate_usd_per_minute is None
                        else f" ({self.cost.rate_usd_per_minute} USD/min)"
                    )
                ),
                (
                    "ASR cost per class hour: "
                    f"{_money_text(self.cost.cost_per_class_hour_usd)}"
                ),
                (
                    "RAM/tmpfs: tmpfs peak (sum of slot maxima) "
                    f"{_bytes_text(self.tmpfs_peak_sum_bytes)} <= provisional "
                    f"{_bytes_text(PROVISIONAL_TMPFS_BYTES)}: "
                    f"{_yes_no(self.tmpfs_peak_sum_bytes <= PROVISIONAL_TMPFS_BYTES)}"
                ),
                (
                    "           sampled concurrent tmpfs peak: "
                    f"{_bytes_text(self.tmpfs_peak_sampled_bytes)}"
                ),
                (
                    "           worker RSS peak (sum) "
                    f"{_bytes_text(self.worker_rss_peak_sum_bytes)} <= provisional "
                    f"{_bytes_text(PROVISIONAL_MEM_LIMIT_BYTES)}: "
                    f"{_fits_text(self.worker_rss_peak_sum_bytes)}"
                ),
                (
                    "           ffmpeg child max RSS (max of children, not additive): "
                    f"{_bytes_text(self.ffmpeg_child_maxrss_bytes)}"
                ),
                (
                    "ffmpeg CPU (children): user "
                    f"{_seconds_text(self.ffmpeg_cpu_user_seconds)}, system "
                    f"{_seconds_text(self.ffmpeg_cpu_system_seconds)}, total "
                    f"{_seconds_text(self.ffmpeg_cpu_total_seconds)} for the "
                    f"{self.slots}-slot run"
                ),
                (
                    "ffmpeg CPU per class hour (max slot): "
                    f"{_seconds_text(self.ffmpeg_cpu_seconds_per_class_hour)}, "
                    f"ratio {_ratio_text(self.cpu_ratio)} cores"
                ),
                (
                    "peak working set (worker RSS + tmpfs, max slot): "
                    f"{_bytes_text(self.peak_working_set_bytes)}"
                ),
                (
                    "wall per class hour (max slot): "
                    f"{self.wall_seconds_per_class_hour:.3f} s; RAM "
                    f"{_gib_hours_text(self.ram_gib_hours_per_class_hour)} per class hour"
                ),
                (
                    "Limits: 200 MiB and 3 h are not approved; D4 is blocked. "
                    "No Riva call, no key and no audio kept."
                ),
            ]
        )
        return "\n".join(lines)


def _limitations() -> list[str]:
    return [
        "ASR rate: NO VERIFICADO. The F0.1 spike did not measure a cost per minute (S1.A7); "
        "an operator-provided rate is labelled operator-provided, not spike-measured.",
        "Billing granularity and minimums are NO VERIFICADO. Effective minutes are the audio "
        "sent to the provider, not an invoice.",
        "No Riva channel is opened. The stub transcriber measures the ffmpeg/tmpfs path, "
        "not provider latency, provider RAM or ASR quality.",
        "Worker RSS excludes the ffmpeg child peak (reported separately) and any Riva SDK "
        "buffer; it is not a full slot budget.",
        "Worker peak RSS prefers /proc/self/status VmHWM and falls back to "
        "getrusage(RUSAGE_SELF).ru_maxrss when a sandbox hides it; with neither source "
        "the RAM term stays NO VERIFICADO and the load case skips instead of reporting 0.",
        "ffmpeg CPU is the user+system time of the ffprobe/ffmpeg children decoded from the "
        "host's RUSAGE_CHILDREN; provider CPU, network and wall-time contention are not "
        "included.",
        "RAM GiB-hours multiply the peak working set by the local audio-path wall time; "
        "they are a lower bound that excludes provider waiting and idle reservation.",
        "The class-hour ASR projection is linear from the measured clip; real classes have "
        "silence, questions and non-speech audio.",
        "200 MiB and 3 h remain unapproved. D4 is blocked; the provisional tmpfs and memory "
        "limits are context, not an approval.",
    ]


def _rounded(value: float | str, digits: int) -> float | str:
    return value if isinstance(value, str) else round(value, digits)


def _rounded_optional(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _sum_optional(values: Iterable[float | None]) -> float | None:
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return sum(value for value in items if value is not None)


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _require_float(payload: dict[str, Any], key: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite")
    return number


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{key} must be a number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{key} must be a non-negative finite number")
    return number


def _bytes_text(value: int | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value / _MIB:.2f} MiB"


def _money_text(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return f"{value:.6f} USD"


def _seconds_text(value: float | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value:.6f} s"


def _ratio_text(value: float | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value:.6f}"


def _gib_hours_text(value: float | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return f"{value:.9f} GiB-hours"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _fits_text(value: int | None) -> str:
    if value is None:
        return NOT_VERIFIED
    return _yes_no(value <= PROVISIONAL_MEM_LIMIT_BYTES)


def process_rss_bytes() -> int | None:
    """Current resident set size from ``VmRSS``; ``None`` when unavailable."""
    return _status_kib("VmRSS")


def process_hwm_bytes() -> int | None:
    """Peak resident set size in bytes, or ``None`` when no source is available.

    ``VmHWM`` in ``/proc/self/status`` is the primary source. Some sandboxes (gVisor,
    for example) hide that field, so fall back to ``ru_maxrss`` from
    ``getrusage(RUSAGE_SELF)``, which is the same kernel peak expressed in KiB on Linux.
    A missing or zero peak stays unknown instead of becoming a fabricated value.
    """
    vm_hwm = _status_kib("VmHWM")
    if vm_hwm is not None and vm_hwm > 0:
        return vm_hwm
    return self_maxrss_bytes()


def self_maxrss_bytes() -> int | None:
    """Peak RSS from ``getrusage(RUSAGE_SELF)``; Linux reports KiB, else ``None``."""
    if sys.platform != "linux":
        return None
    import resource

    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return None
    return value * 1024 if value > 0 else None


def _status_kib(name: str) -> int | None:
    if sys.platform != "linux":
        return None
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith(name + ":"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def children_maxrss_bytes() -> int | None:
    """Max RSS of any waited child (ffmpeg/ffprobe). Linux reports KiB."""
    if sys.platform != "linux":
        return None
    import resource

    value = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    return value * 1024 if value > 0 else None


def children_cpu_seconds() -> tuple[float | None, float | None]:
    """Cumulative user/system CPU of waited children, in seconds.

    The audio path waits on ffprobe/ffmpeg, so the delta around ``process`` is the
    ffmpeg CPU cost. Non-Linux platforms return ``None`` instead of guessing.
    """
    if sys.platform != "linux":
        return None, None
    import resource

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return float(usage.ru_utime), float(usage.ru_stime)


class _DirectorySampler(threading.Thread):
    """Sample a directory's bytes while ffmpeg writes and deletes fragments."""

    def __init__(self, root: Path, interval_s: float = 0.005) -> None:
        super().__init__(daemon=True)
        self._root = root
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self.peak_bytes = directory_bytes(root)

    def run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            self.peak_bytes = max(self.peak_bytes, directory_bytes(self._root))

    def stop(self) -> None:
        self._stop_event.set()
        self.join()


def directory_bytes(root: Path) -> int:
    """Bytes of regular files under ``root``; races with writers are ignored."""
    total = 0
    try:
        paths = list(root.rglob("*"))
    except OSError:
        return 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def resize_wav_bytes(wav_bytes: bytes, seconds: float) -> bytes:
    """Return a mono PCM WAV of exactly ``seconds``: truncate or pad with silence."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be a positive finite number")
    with wave.open(BytesIO(wav_bytes), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if channels != 1 or sample_width != 2 or rate <= 0:
        raise ValueError("synthetic audio must be mono PCM s16")
    target_frames = int(round(seconds * rate))
    current_frames = len(frames) // (channels * sample_width)
    if current_frames > target_frames:
        frames = frames[: target_frames * channels * sample_width]
    elif current_frames < target_frames:
        frames += b"\x00" * (target_frames - current_frames) * channels * sample_width
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return buffer.getvalue()


def synthesize_clip(
    seconds: float,
    *,
    language: str = "es",
    seed: int = 0,
) -> bytes:
    """Generate synthetic speech in RAM and trim it to exact ``seconds``."""
    if not math.isfinite(seconds) or not MIN_CLIP_SECONDS <= seconds <= MAX_CLIP_SECONDS:
        raise ValueError(
            f"clip seconds must be between {MIN_CLIP_SECONDS} and {MAX_CLIP_SECONDS}"
        )
    probe = generate_synthetic_utterance(
        text=DEFAULT_PHRASE_ES, seed=seed, language_code=language
    )
    with wave.open(BytesIO(probe.wav_bytes), "rb") as reader:
        rate = reader.getframerate()
        phrase_seconds = reader.getnframes() / rate if rate else 0.0
    if phrase_seconds <= 0:
        raise RuntimeError("synthetic audio has no duration")
    # Keep a small margin so espeak never exceeds its 300 s output guard.
    repeats = max(1, int(seconds // (phrase_seconds * 1.02)))
    utterance = generate_synthetic_utterance(
        text=" ".join([DEFAULT_PHRASE_ES] * repeats),
        seed=seed,
        language_code=language,
    )
    return resize_wav_bytes(utterance.wav_bytes, seconds)


def find_tmpfs_base(override: Path | None = None) -> Path:
    """A writable tmpfs directory. Never falls back to persistent disk."""
    if override is not None:
        candidates = [override]
    else:
        candidates = [Path(tempfile.gettempdir()), Path("/dev/shm")]  # noqa: S108
    try:
        mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    except OSError as exc:
        raise RuntimeError("tmpfs detection requires /proc/self/mountinfo") from exc
    for candidate in candidates:
        if not candidate.is_dir() or not os.access(candidate, os.W_OK):
            continue
        mount = hardening.mount_for(mounts, str(candidate))
        if mount is not None and mount.fstype == "tmpfs":
            return candidate
    raise RuntimeError(
        "no writable tmpfs found; pass --tmpfs pointing at a tmpfs directory"
    )


def measure_slot(
    *,
    slot: int,
    workdir: Path,
    fragment_seconds: int,
) -> SlotMeasurement:
    """Run the production audio pipeline once and delete every byte it wrote.

    The transcriber callback is the measurement point: the fragment exists on disk when
    it is called, so the tmpfs peak is exact rather than a sleep-sampling guess. It returns
    empty words, like the ``asr_job --read-stub-transcript`` double, and never builds a
    Riva client.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    rss_before = process_rss_bytes()
    cpu_user_before, cpu_system_before = children_cpu_seconds()
    sampler = _DirectorySampler(workdir)
    billed_seconds = 0.0
    peak_bytes = directory_bytes(workdir)

    def _hear_fragment(path: Path) -> FragmentWords:
        nonlocal billed_seconds, peak_bytes
        billed_seconds += wav_duration_s(path)
        peak_bytes = max(peak_bytes, directory_bytes(workdir))
        return FragmentWords()

    started = time.perf_counter()
    sampler.start()
    try:
        result = process_audio(
            workdir / "original",
            workdir,
            fragment_seconds=float(fragment_seconds),
            overlap_s=settings.fragment_overlap_s,
            transcribe=_hear_fragment,
        )
    finally:
        sampler.stop()
    wall_seconds = time.perf_counter() - started
    tmpfs_after = directory_bytes(workdir)
    rss_peak = process_hwm_bytes()
    rss_after = process_rss_bytes()
    child_maxrss = children_maxrss_bytes()
    cpu_user_after, cpu_system_after = children_cpu_seconds()
    shutil.rmtree(workdir, ignore_errors=True)
    return SlotMeasurement(
        slot=slot,
        fragments=result.fragments,
        billed_seconds=billed_seconds,
        wall_seconds=wall_seconds,
        tmpfs_peak_bytes=max(peak_bytes, sampler.peak_bytes),
        tmpfs_after_bytes=tmpfs_after,
        rss_before_bytes=rss_before,
        rss_peak_bytes=rss_peak,
        rss_after_bytes=rss_after,
        ffmpeg_child_maxrss_bytes=child_maxrss,
        cleanup_removed=not workdir.exists(),
        cpu_user_seconds=_cpu_delta(cpu_user_before, cpu_user_after),
        cpu_system_seconds=_cpu_delta(cpu_system_before, cpu_system_after),
    )


def _cpu_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return max(0.0, after - before)


def _environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "system": f"{platform.system()} {platform.release()}",
        "ffmpeg": _tool_version(["ffmpeg", "-version"]),
        "espeak_ng": _tool_version(["espeak-ng", "--version"]),
        "riva_call": "none",
    }


def _tool_version(argv: Sequence[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return NOT_VERIFIED
    line = (completed.stdout or completed.stderr).splitlines()
    if completed.returncode != 0 or not line:
        return NOT_VERIFIED
    version = line[0].strip()
    for marker in (" Data at:", " Copyright"):
        version = version.split(marker, 1)[0].strip()
    return version


def _last_json_line(stdout: str) -> dict[str, Any]:
    for line in reversed([item for item in stdout.splitlines() if item.strip()]):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("worker produced no JSON measurement")


def run_load(
    *,
    slots: int = PROVISIONAL_SLOTS,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    fragment_seconds: int | None = None,
    rate_usd_per_minute: float | None = None,
    worker_timeout_s: float = DEFAULT_WORKER_TIMEOUT_S,
    tmpfs: Path | None = None,
) -> LoadReport:
    """Run ``slots`` concurrent audio-path workers on one synthetic clip."""
    if slots < 1 or slots > MAX_SLOTS:
        raise ValueError(f"slots must be between 1 and {MAX_SLOTS}")
    if not math.isfinite(worker_timeout_s) or worker_timeout_s <= 0:
        raise ValueError("worker_timeout_s must be a positive finite number")
    if rate_usd_per_minute is not None and (
        isinstance(rate_usd_per_minute, bool)
        or not math.isfinite(rate_usd_per_minute)
        or rate_usd_per_minute <= 0
    ):
        raise ValueError("rate_usd_per_minute must be a positive finite number")
    resolved_fragment_seconds = (
        settings.fragment_seconds if fragment_seconds is None else fragment_seconds
    )
    if resolved_fragment_seconds < 1 or resolved_fragment_seconds > 30:
        raise ValueError("fragment_seconds must be between 1 and 30")
    base = find_tmpfs_base(tmpfs)
    root = Path(tempfile.mkdtemp(prefix=_TMPFS_PREFIX, dir=base))
    processes: list[subprocess.Popen[str]] = []
    try:
        clip = synthesize_clip(clip_seconds)
        slot_dirs: list[Path] = []
        for slot in range(slots):
            slot_dir = root / f"slot-{slot}"
            slot_dir.mkdir()
            (slot_dir / "original").write_bytes(clip)
            slot_dirs.append(slot_dir)
        del clip
        for slot, slot_dir in enumerate(slot_dirs):
            processes.append(
                subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    [
                        sys.executable,
                        str(_SCRIPT),
                        "--worker",
                        "--workdir",
                        str(slot_dir),
                        "--slot",
                        str(slot),
                        "--fragment-seconds",
                        str(resolved_fragment_seconds),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + worker_timeout_s
        sampled_peak = directory_bytes(root)
        while any(process.poll() is None for process in processes):
            sampled_peak = max(sampled_peak, directory_bytes(root))
            if time.monotonic() > deadline:
                for process in processes:
                    process.kill()
                raise RuntimeError("worker timeout")
            time.sleep(SAMPLE_INTERVAL_S)
        measurements: list[SlotMeasurement] = []
        for process in processes:
            stdout, _stderr = process.communicate(timeout=30)
            if process.returncode != 0:
                raise RuntimeError(f"worker {len(measurements)} failed")
            payload = _last_json_line(stdout)
            if payload.get("ok") is not True:
                raise RuntimeError(f"worker {len(measurements)} failed")
            measurements.append(SlotMeasurement.from_dict(payload))
        sampled_peak = max(sampled_peak, directory_bytes(root))
        root_cleaned = not any(root.iterdir())
        first = measurements[0]
        if any(
            measurement.fragments != first.fragments
            or not math.isclose(measurement.billed_seconds, first.billed_seconds)
            for measurement in measurements[1:]
        ):
            raise RuntimeError("slots measured different audio durations")
        usage = AsrUsage(
            source_seconds=clip_seconds,
            fragments=first.fragments,
            billed_seconds=first.billed_seconds,
        )
        report = LoadReport(
            slots=slots,
            clip_seconds=clip_seconds,
            fragment_seconds=resolved_fragment_seconds,
            overlap_seconds=settings.fragment_overlap_s,
            worker_timeout_s=worker_timeout_s,
            measurements=tuple(measurements),
            tmpfs_peak_sampled_bytes=sampled_peak,
            root_cleaned=root_cleaned,
            usage=usage,
            cost=estimate_asr_cost(usage, rate_usd_per_minute),
            environment=_environment(),
        )
        if not root_cleaned or not report.worker_cleanup_all_removed:
            raise RuntimeError("audio cleanup was not verified")
        shutil.rmtree(root, ignore_errors=True)
        if root.exists():
            raise RuntimeError("tmpfs root cleanup failed")
        return report
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        shutil.rmtree(root, ignore_errors=True)


def _bounded_float(minimum: float, maximum: float) -> Any:
    def _parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a number") from exc
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return parsed

    return _parse


def _bounded_int(minimum: int, maximum: int) -> Any:
    def _parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return parsed

    return _parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asr_cost.py",
        description=(
            "Measure ASR effective minutes and RAM/tmpfs under concurrent load. "
            "Never calls Riva; synthetic audio in RAM only."
        ),
    )
    parser.add_argument(
        "--slots",
        type=_bounded_int(1, MAX_SLOTS),
        default=PROVISIONAL_SLOTS,
        help=f"concurrent slots (default: {PROVISIONAL_SLOTS})",
    )
    parser.add_argument(
        "--clip-seconds",
        type=_bounded_float(MIN_CLIP_SECONDS, MAX_CLIP_SECONDS),
        default=DEFAULT_CLIP_SECONDS,
        help=f"synthetic clip seconds (default: {DEFAULT_CLIP_SECONDS})",
    )
    parser.add_argument(
        "--fragment-seconds",
        type=_bounded_int(1, 30),
        default=None,
        help="fragment cap; values above the observed 30 s ceiling are rejected",
    )
    parser.add_argument(
        "--rate-usd-per-minute",
        type=float,
        default=None,
        help=(
            "explicit ASR rate; omit to keep cost NO VERIFICADO (the spike never "
            "measured one)"
        ),
    )
    parser.add_argument(
        "--worker-timeout-s",
        type=_bounded_float(10, 600),
        default=DEFAULT_WORKER_TIMEOUT_S,
        help=f"per-run worker timeout (default: {DEFAULT_WORKER_TIMEOUT_S})",
    )
    parser.add_argument(
        "--tmpfs",
        type=Path,
        default=None,
        help="writable tmpfs directory; defaults to TMPDIR or /dev/shm",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    return parser


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asr_cost.py --worker")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--slot", type=_bounded_int(0, MAX_SLOTS - 1), required=True)
    parser.add_argument("--fragment-seconds", type=_bounded_int(1, 30), required=True)
    return parser


def _worker_main(argv: Sequence[str]) -> int:
    args = build_worker_parser().parse_args(argv)
    try:
        measurement = measure_slot(
            slot=args.slot,
            workdir=args.workdir,
            fragment_seconds=args.fragment_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - the parent only needs a bounded error code
        shutil.rmtree(args.workdir, ignore_errors=True)
        print(
            json.dumps({"ok": False, "slot": args.slot, "error": type(exc).__name__}),
            flush=True,
        )
        return 1
    payload: dict[str, Any] = {"ok": True}
    payload.update(measurement.to_dict())
    print(json.dumps(payload), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--worker"]:
        return _worker_main(arguments[1:])
    args = build_parser().parse_args(arguments)
    try:
        report = run_load(
            slots=args.slots,
            clip_seconds=args.clip_seconds,
            fragment_seconds=args.fragment_seconds,
            rate_usd_per_minute=args.rate_usd_per_minute,
            worker_timeout_s=args.worker_timeout_s,
            tmpfs=args.tmpfs,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"asr_cost: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
