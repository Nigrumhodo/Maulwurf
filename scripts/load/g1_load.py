"""S4.3: G1 re-verification under load with injected failures (L-S4-SG-01).

The harness answers three questions with booleans, counts and command evidence, never with
audio:

* **Zero audio persistence:** after concurrent uploads and injected failures, no original,
  converted file, fragment or audio-magic byte remains in the attempt tmpfs, the harness
  trees, logs, Redis (when configured) or the scanned caches.
* **Zero orphan temporaries:** every attempt directory is gone, no descriptor points under
  it and no process of the ASR group survives.
* **RAM within the per-slot reservation:** the peak resident set of each slot (worker
  process + every process of the ASR group + the tmpfs bytes it holds) stays under
  ``PROVISIONAL_MEM_LIMIT_BYTES / PROVISIONAL_SLOTS`` (512 MiB with the provisional 1 GiB
  instance limit and two slots).

Each attempt runs in its own subprocess over the production path: ``receiver.receive``
streams the synthetic WAV to the attempt tmpfs, ``probe_file`` decides the format,
``maulwurf_ingest.asr_job`` runs in its own process group with the stub transcript (no Riva
channel, no key, no provider call) and ``cleanup.destroy_and_verify`` produces the same
``/proc``-based evidence the supervisor records.

Injections (one per scenario):

* ``kill`` — SIGKILL to the whole ASR process group while a fragment exists;
* ``timeout`` — the production ``_run_asr`` deadline, which kills the group;
* ``full`` — a real ``ENOSPC`` on a bounded private tmpfs mounted for the attempt. When the
  environment cannot mount a tmpfs (no ``CAP_SYS_ADMIN``), ``auto`` falls back to a
  deterministic ``ENOSPC`` raised by the write boundary and labels the row as injected, so
  a real full disk is never claimed without one;
* ``success`` — the same path without a failure, to measure the baseline.

Run from the repository root with the ingest environment::

    cd apps/ingest && uv run python ../../scripts/load/g1_load.py
    cd apps/ingest && uv run python ../../scripts/load/g1_load.py --format json
    cd apps/ingest && uv run python ../../scripts/load/g1_load.py \
        --attempts 8 --slots 2 --clip-seconds 6 --full-injection real

The synthetic audio is a tone generated in RAM at execution time. Nothing is versioned or
kept: the bytes only live inside the attempt tmpfs (or its bounded private mount) and every
directory is deleted and verified. The harness does not use PostgreSQL, Redis or a provider.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import errno
import json
import logging
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
from array import array
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT.parents[2]
_INGEST_ROOT = _REPO_ROOT / "apps" / "ingest"
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from maulwurf_ingest import audio_absence, cleanup, hardening, receiver  # noqa: E402
from maulwurf_ingest.asr_job import ORIGINAL, STUB_TRANSCRIPT_NAME  # noqa: E402
from maulwurf_ingest.audio.validate import probe_file  # noqa: E402
from maulwurf_ingest.capacity import (  # noqa: E402
    APPROVED_3H,
    APPROVED_200_MIB,
    PROVISIONAL_MEM_LIMIT_BYTES,
    PROVISIONAL_SLOTS,
    PROVISIONAL_TMPFS_BYTES,
)
from maulwurf_ingest.config import settings  # noqa: E402
from maulwurf_ingest.receiver import iter_bytes  # noqa: E402
from maulwurf_ingest.supervisor import (  # noqa: E402
    Outcome,
    SupervisorConfig,
    _run_asr,
)

NOT_VERIFIED = "NO VERIFICADO"
TICKET = "S4.3"
TEST_BASIS = "L-S4-SG-01"

SCENARIOS = ("success", "kill", "timeout", "full")
INJECTION_KINDS = {
    "success": "none",
    "kill": "sigkill_asr_group",
    "timeout": "asr_timeout",
    "full": "tmpfs_enospc",
}
FAILURE_SCENARIOS = ("kill", "timeout", "full")

DEFAULT_CLIP_SECONDS = 6.0
MIN_CLIP_SECONDS = 2.0
MAX_CLIP_SECONDS = 60.0
DEFAULT_FRAGMENT_SECONDS = 30
DEFAULT_TIMEOUT_S = 1.0
DEFAULT_SUCCESS_TIMEOUT_S = 120.0
DEFAULT_ATTEMPTS = 4
MAX_ATTEMPTS = 12
MAX_SLOTS = 4
DEFAULT_FULL_TMPFS_KIB = 96
DEFAULT_FAIL_AFTER_BYTES = 48 * 1024
DEFAULT_WORKER_TIMEOUT_S = 120.0
SAMPLE_INTERVAL_S = 0.02
TONE_HZ = 440.0
TONE_SAMPLE_RATE_HZ = 16_000
STUB_TEXT = "prueba sintetica g1"

_TMPFS_PREFIX = "mw-s43-"
_MOUNT_PREFIX = "mw-s43-mnt-"
_MIB = 1024 * 1024
_PAGE_BYTES = int(os.sysconf("SC_PAGE_SIZE")) if hasattr(os, "sysconf") else 4096


def per_slot_reservation_bytes(
    *,
    slots: int = PROVISIONAL_SLOTS,
    instance_limit_bytes: int = PROVISIONAL_MEM_LIMIT_BYTES,
) -> int:
    """RAM reservation of one slot: the provisional instance limit split by slots.

    It is a derived, provisional number. The harness never presents it as an approved
    limit.
    """
    if slots < 1:
        raise ValueError("slots must be >= 1")
    if instance_limit_bytes < 1:
        raise ValueError("instance_limit_bytes must be >= 1")
    return instance_limit_bytes // slots


@dataclass(frozen=True)
class AttemptMeasurement:
    """One attempt: what happened and what the cleanup evidence proved."""

    attempt: int
    wave: int
    slot: int
    scenario: str
    outcome: str
    injection: str
    injection_confirmed: bool
    upload_bytes: int
    received_bytes: int
    fragments: int
    wall_seconds: float
    tmpfs_peak_bytes: int
    tmpfs_after_bytes: int
    slot_ram_peak_bytes: int
    worker_rss_peak_bytes: int
    asr_group_rss_peak_bytes: int
    cleanup_verified: bool
    workdir_absent: bool
    live_processes: int
    open_descriptors: int
    unreadable_processes: int
    tmp_is_tmpfs: bool
    cleanup_seconds: float
    slot_dir_orphans: int = 0
    error: str | None = None
    error_number: int | None = None
    cleanup_error: str | None = None

    @property
    def filesystem_clean(self) -> bool:
        return (
            self.workdir_absent
            and self.tmpfs_after_bytes == 0
            and self.live_processes == 0
            and self.open_descriptors == 0
            and self.slot_dir_orphans == 0
        )

    def within_reservation(self, reservation_bytes: int) -> bool:
        return self.slot_ram_peak_bytes <= reservation_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "wave": self.wave,
            "slot": self.slot,
            "scenario": self.scenario,
            "outcome": self.outcome,
            "injection": self.injection,
            "injection_confirmed": self.injection_confirmed,
            "upload_bytes": self.upload_bytes,
            "received_bytes": self.received_bytes,
            "fragments": self.fragments,
            "wall_seconds": round(self.wall_seconds, 6),
            "tmpfs_peak_bytes": self.tmpfs_peak_bytes,
            "tmpfs_after_bytes": self.tmpfs_after_bytes,
            "slot_ram_peak_bytes": self.slot_ram_peak_bytes,
            "worker_rss_peak_bytes": self.worker_rss_peak_bytes,
            "asr_group_rss_peak_bytes": self.asr_group_rss_peak_bytes,
            "cleanup_verified": self.cleanup_verified,
            "workdir_absent": self.workdir_absent,
            "live_processes": self.live_processes,
            "open_descriptors": self.open_descriptors,
            "unreadable_processes": self.unreadable_processes,
            "tmp_is_tmpfs": self.tmp_is_tmpfs,
            "cleanup_seconds": round(self.cleanup_seconds, 6),
            "slot_dir_orphans": self.slot_dir_orphans,
            "error": self.error,
            "error_number": self.error_number,
            "cleanup_error": self.cleanup_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AttemptMeasurement:
        scenario = payload.get("scenario")
        if not isinstance(scenario, str):
            raise ValueError("scenario must be a string")
        if scenario not in SCENARIOS:
            raise ValueError("unknown scenario")
        outcome = payload.get("outcome")
        if not isinstance(outcome, str):
            raise ValueError("outcome must be a string")
        if outcome not in _KNOWN_OUTCOMES:
            raise ValueError("unknown outcome")
        try:
            measurement = cls(
                attempt=_require_int(payload, "attempt"),
                wave=_require_int(payload, "wave"),
                slot=_require_int(payload, "slot"),
                scenario=scenario,
                outcome=outcome,
                injection=_require_str(payload, "injection"),
                injection_confirmed=_require_bool(payload, "injection_confirmed"),
                upload_bytes=_require_int(payload, "upload_bytes"),
                received_bytes=_require_int(payload, "received_bytes"),
                fragments=_require_int(payload, "fragments"),
                wall_seconds=_require_float(payload, "wall_seconds"),
                tmpfs_peak_bytes=_require_int(payload, "tmpfs_peak_bytes"),
                tmpfs_after_bytes=_require_int(payload, "tmpfs_after_bytes"),
                slot_ram_peak_bytes=_require_int(payload, "slot_ram_peak_bytes"),
                worker_rss_peak_bytes=_require_int(payload, "worker_rss_peak_bytes"),
                asr_group_rss_peak_bytes=_require_int(payload, "asr_group_rss_peak_bytes"),
                cleanup_verified=_require_bool(payload, "cleanup_verified"),
                workdir_absent=_require_bool(payload, "workdir_absent"),
                live_processes=_require_int(payload, "live_processes"),
                open_descriptors=_require_int(payload, "open_descriptors"),
                unreadable_processes=_require_int(payload, "unreadable_processes"),
                tmp_is_tmpfs=_require_bool(payload, "tmp_is_tmpfs"),
                cleanup_seconds=_require_float(payload, "cleanup_seconds"),
                slot_dir_orphans=_optional_int(payload, "slot_dir_orphans") or 0,
                error=_optional_str(payload, "error"),
                error_number=_optional_int(payload, "error_number"),
                cleanup_error=_optional_str(payload, "cleanup_error"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("attempt measurement is incomplete") from exc
        if measurement.attempt < 0 or measurement.wave < 0 or measurement.slot < 0:
            raise ValueError("attempt indexes must be non-negative")
        return measurement


_KNOWN_OUTCOMES = frozenset({
    "success",
    "killed",
    "timeout",
    "enospc",
    "write_error",
    "rejected",
    "asr_failed",
    "lease_lost",
    "worker_error",
    "worker_crashed",
})


@dataclass(frozen=True)
class LoadReport:
    """Measured load plus the G1 booleans. Nothing here approves a limit."""

    attempts: int
    slots: int
    clip_seconds: float
    fragment_seconds: int
    timeout_s: float
    worker_timeout_s: float
    full_injection: str
    full_tmpfs_kib: int | None
    worker_isolation: str
    measurements: tuple[AttemptMeasurement, ...]
    environment: dict[str, str]
    scoped_absence: dict[str, object]
    host_absence: dict[str, object] | None
    harness_root_empty: bool
    harness_root_cleaned: bool
    per_slot_reservation_bytes: int

    @property
    def waves(self) -> int:
        return math.ceil(self.attempts / self.slots)

    @property
    def concurrent(self) -> bool:
        return self.slots > 1 and self.attempts > 1

    @property
    def scoped_clear(self) -> bool:
        return bool(self.scoped_absence.get("clear")) and bool(
            self.scoped_absence.get("scan_complete")
        )

    @property
    def zero_audio_persistence(self) -> bool:
        return (
            self.scoped_clear
            and self.harness_root_empty
            and all(
                measurement.workdir_absent and measurement.tmpfs_after_bytes == 0
                for measurement in self.measurements
            )
        )

    @property
    def zero_orphan_temporaries(self) -> bool:
        return self.harness_root_empty and all(
            measurement.filesystem_clean for measurement in self.measurements
        )

    @property
    def ram_within_per_slot_reservation(self) -> bool:
        return all(
            measurement.within_reservation(self.per_slot_reservation_bytes)
            for measurement in self.measurements
        )

    @property
    def tmpfs_peak_sum_bytes(self) -> int:
        """Conservative upper bound: per-slot peaks added, even if they never coincided."""
        return sum(measurement.tmpfs_peak_bytes for measurement in self.measurements)

    @property
    def tmpfs_within_provisional(self) -> bool:
        return self.tmpfs_peak_sum_bytes <= PROVISIONAL_TMPFS_BYTES

    @property
    def injections(self) -> dict[str, bool]:
        covered = {"kill": False, "timeout": False, "tmpfs_full": False}
        for measurement in self.measurements:
            if not measurement.injection_confirmed:
                continue
            if measurement.scenario == "kill":
                covered["kill"] = measurement.outcome == "killed"
            elif measurement.scenario == "timeout":
                covered["timeout"] = measurement.outcome == "timeout"
            elif measurement.scenario == "full":
                covered["tmpfs_full"] = measurement.outcome == "enospc"
                covered["tmpfs_full"] = covered["tmpfs_full"] and measurement.injection in {
                    "tmpfs_enospc_real",
                    "tmpfs_enospc_injected",
                }
        return covered

    @property
    def injections_covered(self) -> bool:
        return all(self.injections.values())

    @property
    def tmpfs_full_evidence(self) -> str:
        kinds = {
            measurement.injection
            for measurement in self.measurements
            if measurement.scenario == "full" and measurement.outcome == "enospc"
        }
        if "tmpfs_enospc_real" in kinds:
            return "real"
        if "tmpfs_enospc_injected" in kinds:
            return "injected"
        return "missing"

    @property
    def checks_pass(self) -> bool:
        return (
            self.zero_audio_persistence
            and self.zero_orphan_temporaries
            and self.ram_within_per_slot_reservation
        )

    @property
    def g1_verified(self) -> bool:
        """Strict verdict: every check and a real full-tmpfs injection."""
        return (
            self.checks_pass
            and self.injections_covered
            and self.tmpfs_full_evidence == "real"
        )

    @property
    def g1_verified_with_injected_fallback(self) -> bool:
        """Run verdict: checks and injections pass, with the tmpfs-full fidelity labelled."""
        return (
            self.checks_pass
            and self.injections_covered
            and self.tmpfs_full_evidence in {"real", "injected"}
        )

    def to_dict(self) -> dict[str, Any]:
        host = self.host_absence
        return {
            "ticket": TICKET,
            "test_basis": TEST_BASIS,
            "environment": self.environment,
            "load": {
                "attempts": self.attempts,
                "slots": self.slots,
                "waves": self.waves,
                "concurrent": self.concurrent,
                "clip_seconds": self.clip_seconds,
                "fragment_cap_seconds": self.fragment_seconds,
                "timeout_s": self.timeout_s,
                "worker_timeout_s": self.worker_timeout_s,
                "synthetic_audio": "tone 16 kHz mono PCM s16, generated in RAM",
                "riva_call": "none",
                "database": "not used",
                "redis": "only scanned when MAULWURF_REDIS_URL is set",
                "worker_isolation": self.worker_isolation,
            },
            "injections": {
                **self.injections,
                "tmpfs_full_evidence": self.tmpfs_full_evidence,
                "mode": self.full_injection,
                "bounded_tmpfs_kib": self.full_tmpfs_kib,
            },
            "attempts": [measurement.to_dict() for measurement in self.measurements],
            "aggregate": {
                "tmpfs_peak_sum_of_slot_maxima_bytes": self.tmpfs_peak_sum_bytes,
                "tmpfs_within_provisional": self.tmpfs_within_provisional,
                "per_slot_reservation_bytes": self.per_slot_reservation_bytes,
                "harness_root_empty": self.harness_root_empty,
                "harness_root_cleaned": self.harness_root_cleaned,
            },
            "absence": {
                "scoped": self.scoped_absence,
                "host": host,
            },
            "checks": {
                "zero_audio_persistence": self.zero_audio_persistence,
                "zero_orphan_temporaries": self.zero_orphan_temporaries,
                "ram_within_per_slot_reservation": self.ram_within_per_slot_reservation,
                "injections_covered": self.injections_covered,
                "checks_pass": self.checks_pass,
                "g1_verified": self.g1_verified,
                "g1_verified_with_injected_fallback": (
                    self.g1_verified_with_injected_fallback
                ),
            },
            "limits": {
                "provisional_slots": PROVISIONAL_SLOTS,
                "provisional_mem_limit_bytes": PROVISIONAL_MEM_LIMIT_BYTES,
                "provisional_tmpfs_bytes": PROVISIONAL_TMPFS_BYTES,
                "approved_200_mib": APPROVED_200_MIB,
                "approved_3h": APPROVED_3H,
                "limits_validated": False,
            },
            "notes": _limitations(),
        }

    def render_table(self) -> str:
        environment = ", ".join(f"{key}={value}" for key, value in self.environment.items())
        lines = [
            f"{TICKET} G1 re-verification under load ({TEST_BASIS})",
            f"Environment: {environment}",
            (
                f"Load: {self.attempts} attempt(s), {self.slots} concurrent slot(s), "
                f"{self.waves} wave(s), {self.clip_seconds:.1f} s synthetic tone, fragment "
                f"cap {self.fragment_seconds} s, ASR deadline {self.timeout_s:.2f} s"
            ),
            (
                f"{'att':>4} {'wave':>4} {'scenario':>8} {'outcome':>14} {'upload':>10} "
                f"{'tmpfs':>10} {'slot_ram':>10} {'fs_clean':>8} {'verified':>8} "
                f"{'orph':>4} injection"
            ),
        ]
        for measurement in self.measurements:
            lines.append(
                f"{measurement.attempt:>4} {measurement.wave:>4} "
                f"{measurement.scenario:>8} {measurement.outcome:>14} "
                f"{_bytes_text(measurement.upload_bytes):>10} "
                f"{_bytes_text(measurement.tmpfs_peak_bytes):>10} "
                f"{_bytes_text(measurement.slot_ram_peak_bytes):>10} "
                f"{_yes_no(measurement.filesystem_clean):>8} "
                f"{_yes_no(measurement.cleanup_verified):>8} "
                f"{measurement.slot_dir_orphans:>4} {measurement.injection}"
                + (f" ({measurement.error})" if measurement.error else "")
            )
        injections = self.injections
        lines.extend([
            (
                "Checks: zero audio persistence="
                f"{_yes_no(self.zero_audio_persistence)}; zero orphan temporaries="
                f"{_yes_no(self.zero_orphan_temporaries)}; RAM within per-slot "
                f"reservation={_yes_no(self.ram_within_per_slot_reservation)}"
            ),
            (
                f"RAM: {_bytes_text(self.per_slot_reservation_bytes)} per slot (provisional "
                f"{_bytes_text(PROVISIONAL_MEM_LIMIT_BYTES)} / {PROVISIONAL_SLOTS} slots) and "
                f"{_bytes_text(self.tmpfs_peak_sum_bytes)} tmpfs summed <= provisional "
                f"{_bytes_text(PROVISIONAL_TMPFS_BYTES)}: "
                f"{_yes_no(self.tmpfs_within_provisional)}"
            ),
            (
                f"Injections: kill={_yes_no(injections['kill'])}; "
                f"timeout={_yes_no(injections['timeout'])}; tmpfs full="
                f"{self.tmpfs_full_evidence}"
            ),
            (
                "Scoped absence (harness trees + logs): "
                f"audio_files={self.scoped_absence.get('audio_files')} "
                f"magic_matches={self.scoped_absence.get('magic_matches')} "
                f"log_markers={self.scoped_absence.get('log_audio_markers')} "
                f"clear={_yes_no(self.scoped_clear)}"
            ),
        ])
        host = self.host_absence
        if host is None:
            lines.append("Host absence: not run")
        else:
            lines.append(
                "Host absence (context only): "
                f"audio_files={host.get('audio_files')} "
                f"magic_matches={host.get('magic_matches')} "
                f"scan_complete={_yes_no(bool(host.get('scan_complete')))} "
                f"clear={_yes_no(bool(host.get('clear')))}"
            )
        lines.extend([
            (
                f"Harness root: empty after attempts={_yes_no(self.harness_root_empty)}; "
                f"removed={_yes_no(self.harness_root_cleaned)}"
            ),
            f"G1 re-verified (strict, real full tmpfs): {_yes_no(self.g1_verified)}",
            (
                "G1 checks pass (injected fallback labelled): "
                f"{_yes_no(self.g1_verified_with_injected_fallback)}"
            ),
            (
                "Limits: 200 MiB and 3 h are not approved; D4 is blocked. No Riva call, "
                "no key and no audio kept."
            ),
        ])
        return "\n".join(lines)


def _limitations() -> list[str]:
    return [
        "The user-facing API, PostgreSQL lease/outbox state and the transcript commit are "
        "not exercised by this harness; I-S1-SG-06/07/08 and I-S2-SG-06/07 already cover "
        "them. The load leg covers the audio path, the ASR process group and cleanup.",
        "No Riva channel is opened and no provider key is read. ASR latency, provider RAM "
        "and provider retention are out of scope.",
        "The audio is a synthetic tone. Silence detection, transcription quality and real "
        "class content are not measured.",
        "The per-slot reservation is derived from the provisional instance memory limit "
        "and the provisional slot count, not from an approved capacity decision.",
        "The host absence sweep scans the shared machine. On a host that already contains "
        "unrelated audio artifacts it is context, not a G1 verdict; the scoped sweep of "
        "the harness trees and logs is the load-leg evidence. A clean container is the "
        "reference environment for the global sweep.",
        "A full tmpfs is real only when the harness could mount a bounded private tmpfs. "
        "Without CAP_SYS_ADMIN the ENOSPC is injected at the write boundary and labelled "
        "as such; that run does not claim a real full disk.",
        "200 MiB and 3 h remain unapproved. The provisional tmpfs and memory limits are "
        "context, not an approval.",
    ]


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _bytes_text(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < _MIB:
        return f"{value / 1024:.1f} KiB"
    return f"{value / _MIB:.2f} MiB"


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


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def synthesize_tone(
    seconds: float,
    *,
    rate: int = TONE_SAMPLE_RATE_HZ,
    frequency: float = TONE_HZ,
) -> bytes:
    """Deterministic mono PCM s16 WAV generated in RAM. No speech, no file."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be a positive finite number")
    if rate < 8000 or rate > 48000:
        raise ValueError("rate must be between 8000 and 48000")
    frames = int(round(seconds * rate))
    amplitude = 6000
    samples = array(
        "h",
        (
            int(amplitude * math.sin(2 * math.pi * frequency * index / rate))
            for index in range(frames)
        ),
    )
    if sys.byteorder == "big":
        samples.byteswap()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    return buffer.getvalue()


def _stub_document(text: str) -> bytes:
    """The same fixture shape the supervisor writes for its test double."""
    body = {
        "text": text,
        "precision": "none",
        "words": [],
        "model": "whisper-large-v3",
        "client_version": "2.27.0",
    }
    return json.dumps(body).encode()


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


def directory_entries(root: Path) -> int:
    try:
        return sum(1 for _ in root.iterdir())
    except OSError:
        return 0


def _rss_bytes(pid: int) -> int | None:
    try:
        pages = int(Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return pages * _PAGE_BYTES


def _process_group_and_state(pid: int) -> tuple[int, str] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat[stat.rindex(")") + 2 :].split()
        return int(fields[2]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def group_rss_bytes(pgid: int) -> int:
    """Resident bytes of the live processes in ``pgid`` (zombies excluded)."""
    total = 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = _process_group_and_state(int(entry.name))
        if info is None:
            continue
        group, state = info
        if group != pgid or state in {"Z", "X"}:
            continue
        rss = _rss_bytes(int(entry.name))
        if rss is not None:
            total += rss
    return total


class _SlotSampler(threading.Thread):
    """Sample the slot while the attempt runs: worker + ASR group + tmpfs bytes."""

    def __init__(
        self, workdir: Path, worker_pid: int, interval_s: float = SAMPLE_INTERVAL_S
    ) -> None:
        super().__init__(daemon=True)
        self._workdir = workdir
        self._worker_pid = worker_pid
        self._interval_s = interval_s
        self._stop_event = threading.Event()
        self._pgid: int | None = None
        self.tmpfs_peak_bytes = directory_bytes(workdir)
        self.worker_rss_peak_bytes = _rss_bytes(worker_pid) or 0
        self.asr_group_rss_peak_bytes = 0
        self.slot_peak_bytes = self.worker_rss_peak_bytes + self.tmpfs_peak_bytes

    def set_group(self, pgid: int) -> None:
        self._pgid = pgid

    def run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            self._sample()

    def _sample(self) -> None:
        tmpfs = directory_bytes(self._workdir)
        worker = _rss_bytes(self._worker_pid) or 0
        group = group_rss_bytes(self._pgid) if self._pgid is not None else 0
        self.tmpfs_peak_bytes = max(self.tmpfs_peak_bytes, tmpfs)
        self.worker_rss_peak_bytes = max(self.worker_rss_peak_bytes, worker)
        self.asr_group_rss_peak_bytes = max(self.asr_group_rss_peak_bytes, group)
        self.slot_peak_bytes = max(self.slot_peak_bytes, worker + group + tmpfs)

    def stop(self) -> None:
        self._sample()
        self._stop_event.set()
        self.join()


class _Keeper:
    """Only the ``lost`` event is needed: the harness never owns a database lease."""

    def __init__(self) -> None:
        self.lost = asyncio.Event()

    async def stop(self) -> None:
        return None


@dataclass
class _Child:
    pgid: int | None = None
    starttime: int | None = None


class _FailingHandle:
    """Write a prefix and then raise ENOSPC, to exercise the injected fallback."""

    def __init__(self, handle: Any, limit: int) -> None:
        self._handle = handle
        self._limit = limit
        self._written = 0

    def write(self, data: bytes) -> int:
        allowed = max(0, self._limit - self._written)
        if allowed:
            self._handle.write(data[:allowed])
            self._written += min(len(data), allowed)
        if len(data) > allowed:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return len(data)

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class _ObservedHandle:
    """Count bytes actually accepted by the write path, for partial-write evidence."""

    def __init__(self, handle: Any, counter: list[int]) -> None:
        self._handle = handle
        self._counter = counter

    def write(self, data: bytes) -> int:
        written = self._handle.write(data)
        self._counter[0] += written
        return written

    def flush(self) -> None:
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


def install_write_observer(counter: list[int]) -> None:
    """Wrap the private-file opener so the worker knows how much was written."""
    if len(counter) != 1:
        raise ValueError("counter must have one element")
    original = receiver._open_private  # noqa: SLF001 - the measurement point

    def _open(path: Path) -> _ObservedHandle:
        return _ObservedHandle(original(path), counter)

    receiver._open_private = _open  # type: ignore[assignment]  # noqa: SLF001


def install_write_failure(limit: int) -> None:
    """Replace the private-file opener with one that fails after ``limit`` bytes."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    original = receiver._open_private  # noqa: SLF001 - the injection point is the write

    def _open(path: Path) -> _FailingHandle:
        return _FailingHandle(original(path), limit)

    receiver._open_private = _open  # type: ignore[assignment]  # noqa: SLF001


def _become_subreaper() -> None:
    """Reparent orphaned grandchildren to this worker, like ``init: true`` in compose."""
    if sys.platform != "linux":
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        return


def _reap_children() -> int:
    """Reap every exited child that this worker inherited or spawned."""
    reaped = 0
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return reaped
        except OSError:
            return reaped
        if pid == 0:
            return reaped
        reaped += 1


def _wait_group_dead(pgid: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cleanup.live_group_members(pgid) == 0:
            return True
        time.sleep(0.05)
    return cleanup.live_group_members(pgid) == 0


async def _wait_for_fragment(workdir: Path, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(workdir.glob("frag-*.wav")):
            return True
        await asyncio.sleep(SAMPLE_INTERVAL_S)
    return False


@dataclass(frozen=True)
class SlotOptions:
    attempt: int
    wave: int
    slot: int
    scenario: str
    tmp_dir: Path
    clip_seconds: float
    fragment_seconds: int
    timeout_s: float
    max_upload_bytes: int = 209_715_200
    full_injection: str = "auto"
    fail_after_bytes: int = DEFAULT_FAIL_AFTER_BYTES
    fragment_wait_s: float = 30.0
    success_timeout_s: float = DEFAULT_SUCCESS_TIMEOUT_S


async def run_slot(options: SlotOptions) -> AttemptMeasurement:
    """Run one attempt over the production audio path and verify its cleanup."""
    _become_subreaper()
    clip = synthesize_tone(options.clip_seconds)
    workdir = cleanup.create_workdir(str(options.attempt), str(options.tmp_dir))
    sampler = _SlotSampler(workdir, os.getpid())
    sampler.start()
    outcome = "worker_error"
    fragments = 0
    received_bytes = 0
    error: str | None = None
    error_number: int | None = None
    cleanup_error: str | None = None
    injection = INJECTION_KINDS[options.scenario]
    if options.scenario == "full":
        injection = (
            "tmpfs_enospc_real"
            if options.full_injection == "real"
            else "tmpfs_enospc_injected"
        )
    injection_confirmed = False
    child = _Child()
    killed = False
    evidence: cleanup.CleanupEvidence | None = None
    task: asyncio.Task[Any] | None = None
    started = time.perf_counter()

    try:
        if options.scenario == "full":
            written: list[int] = [0]
            install_write_observer(written)
            if options.full_injection == "write_error":
                install_write_failure(options.fail_after_bytes)
        else:
            written = [0]
        try:
            received = await receiver.receive(
                iter_bytes(clip), workdir / ORIGINAL, max_bytes=options.max_upload_bytes
            )
        except OSError as exc:
            outcome = "enospc" if exc.errno == errno.ENOSPC else "write_error"
            error = (
                errno.errorcode.get(exc.errno, type(exc).__name__)
                if exc.errno
                else type(exc).__name__
            )
            error_number = exc.errno
            injection_confirmed = outcome == "enospc"
            received_bytes = written[0]
        else:
            received_bytes = received.received_bytes
            decision = await asyncio.to_thread(probe_file, workdir / ORIGINAL)
            if decision.decision == "reject":
                outcome = "rejected"
                error = decision.reason
            else:
                cleanup.write_private(
                    workdir / STUB_TRANSCRIPT_NAME, _stub_document(STUB_TEXT)
                )
                config = SupervisorConfig(
                    instance="g1-load",
                    lease_ttl=timedelta(seconds=30),
                    heartbeat_interval_s=1.0,
                    asr_timeout_s=(
                        options.timeout_s
                        if options.scenario == "timeout"
                        else options.success_timeout_s
                    ),
                    tmp_dir=str(options.tmp_dir),
                    realtime=options.scenario in {"kill", "timeout"},
                    fragment_seconds=options.fragment_seconds,
                    stub_transcript=STUB_TEXT,
                )
                keeper = _Keeper()

                def _on_child(pid: int, _workdir: Path) -> None:
                    child.pgid = pid
                    child.starttime = cleanup.start_time(pid)
                    sampler.set_group(pid)

                task = asyncio.create_task(
                    _run_asr(workdir, config, keeper, _on_child, child, language=None)
                )
                if options.scenario == "kill":
                    if await _wait_for_fragment(
                        workdir, timeout_s=options.fragment_wait_s
                    ) and child.pgid is not None:
                        killed = True
                        cleanup.kill_group(child.pgid, child.starttime)
                failure, payload, child_code = await task
                if failure is None and payload is not None:
                    outcome = "success"
                    raw_fragments = payload.get("fragments")
                    fragments = (
                        raw_fragments
                        if isinstance(raw_fragments, int) and not isinstance(raw_fragments, bool)
                        else 0
                    )
                    injection_confirmed = True
                elif failure is Outcome.TIMEOUT:
                    outcome = "timeout"
                    injection_confirmed = options.scenario == "timeout"
                elif failure is Outcome.LEASE_LOST:
                    outcome = "lease_lost"
                elif failure is Outcome.ASR_FAILED:
                    outcome = "killed" if killed else "asr_failed"
                    error = child_code
                    injection_confirmed = killed
                else:
                    outcome = failure.value if failure is not None else "asr_failed"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the measurement must survive the failure
        outcome = "worker_error"
        error = type(exc).__name__
    finally:
        sampler.stop()
        cleanup_started = time.perf_counter()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if child.pgid is not None:
            cleanup.kill_group(child.pgid, child.starttime)
            _wait_group_dead(child.pgid)
        _reap_children()
        try:
            evidence = await asyncio.to_thread(
                cleanup.destroy_and_verify,
                workdir,
                child.pgid,
                starttime=child.starttime,
            )
        except Exception as exc:  # noqa: BLE001 - a cleanup error is evidence of failure
            cleanup_error = type(exc).__name__
        cleanup_seconds = time.perf_counter() - cleanup_started
        wall_seconds = time.perf_counter() - started

    tmpfs_after = directory_bytes(workdir)
    if evidence is None:
        cleanup_verified = False
        workdir_absent = not os.path.lexists(workdir)
        live_processes = cleanup.live_group_members(child.pgid) if child.pgid else 0
        descriptors, unreadable = cleanup.open_descriptors_under(workdir)
        tmp_is_tmpfs = False
        open_descriptors = descriptors
        unreadable_processes = unreadable
    else:
        cleanup_verified = evidence.verified
        workdir_absent = evidence.workdir_absent
        live_processes = evidence.live_processes
        open_descriptors = evidence.open_descriptors
        unreadable_processes = evidence.unreadable_processes
        tmp_is_tmpfs = evidence.tmp_is_tmpfs

    return AttemptMeasurement(
        attempt=options.attempt,
        wave=options.wave,
        slot=options.slot,
        scenario=options.scenario,
        outcome=outcome,
        injection=injection,
        injection_confirmed=injection_confirmed,
        upload_bytes=len(clip),
        received_bytes=received_bytes,
        fragments=fragments,
        wall_seconds=wall_seconds,
        tmpfs_peak_bytes=max(sampler.tmpfs_peak_bytes, received_bytes),
        tmpfs_after_bytes=tmpfs_after,
        slot_ram_peak_bytes=sampler.slot_peak_bytes,
        worker_rss_peak_bytes=sampler.worker_rss_peak_bytes,
        asr_group_rss_peak_bytes=sampler.asr_group_rss_peak_bytes,
        cleanup_verified=cleanup_verified,
        workdir_absent=workdir_absent,
        live_processes=live_processes,
        open_descriptors=open_descriptors,
        unreadable_processes=unreadable_processes,
        tmp_is_tmpfs=tmp_is_tmpfs,
        cleanup_seconds=cleanup_seconds,
        error=error,
        error_number=error_number,
        cleanup_error=cleanup_error,
    )


def find_tmpfs_base(override: Path | None = None) -> Path:
    """A writable tmpfs directory. Never falls back to persistent disk."""
    candidates = [override] if override is not None else [
        Path(tempfile.gettempdir()),
        Path("/dev/shm"),  # noqa: S108 - tmpfs candidate, never used for durable data
    ]
    try:
        mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    except OSError as exc:
        raise RuntimeError("tmpfs detection requires /proc/self/mountinfo") from exc
    for candidate in candidates:
        if candidate is None or not candidate.is_dir() or not os.access(candidate, os.W_OK):
            continue
        mount = hardening.mount_for(mounts, str(candidate))
        if mount is not None and mount.fstype == "tmpfs":
            return candidate
    raise RuntimeError("no writable tmpfs found; pass --tmpfs pointing at a tmpfs directory")


def mount_bounded_tmpfs(
    root: Path, size_kib: int, *, mount_binary: str | None = None
) -> tuple[Path | None, str | None]:
    """Mount a private bounded tmpfs. Returns ``(point, None)`` or ``(None, reason)``."""
    if size_kib < 16:
        raise ValueError("size_kib must be >= 16")
    resolved = mount_binary if mount_binary is not None else shutil.which("mount")
    if resolved is None:
        return None, "mount_binary_missing"
    point = Path(tempfile.mkdtemp(prefix=_MOUNT_PREFIX, dir=root))
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                resolved,
                "-t",
                "tmpfs",
                "-o",
                f"size={size_kib}k,mode=0700",
                "tmpfs",
                str(point),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        shutil.rmtree(point, ignore_errors=True)
        return None, "mount_unavailable"
    if completed.returncode != 0:
        shutil.rmtree(point, ignore_errors=True)
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        reason = detail[0][:120] if detail else f"mount_exit_{completed.returncode}"
        return None, reason
    return point, None


def unmount_and_remove(point: Path, *, umount_binary: str | None = None) -> tuple[bool, int]:
    """Unmount a bounded tmpfs and remove its mount point. Returns ``(ok, entries)``."""
    entries = directory_entries(point)
    resolved = umount_binary if umount_binary is not None else shutil.which("umount")
    if resolved is None:
        return False, entries
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [resolved, str(point)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, entries
    if completed.returncode != 0:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [resolved, "-l", str(point)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    shutil.rmtree(point, ignore_errors=True)
    return not point.exists(), entries


_ISOLATION_ARGV = (
    "--mount",
    "--pid",
    "--fork",
    "--mount-proc",
    "--propagation",
    "private",
)


def pid_namespace_available(unshare_binary: str | None = None) -> bool:
    """True when one worker can run in its own PID and mount namespace."""
    resolved = unshare_binary if unshare_binary is not None else shutil.which("unshare")
    if resolved is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [resolved, *_ISOLATION_ARGV, "true"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _worker_command(
    options: SlotOptions, isolation: str, unshare_binary: str | None = None
) -> list[str]:
    argv = _worker_argv(options)
    if isolation != "pid_namespace":
        return argv
    resolved = unshare_binary if unshare_binary is not None else shutil.which("unshare")
    if resolved is None:
        raise RuntimeError("unshare is not available")
    return [resolved, *_ISOLATION_ARGV, *argv]


def _environment(full_injection: str, worker_isolation: str) -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "system": f"{platform.system()} {platform.release()}",
        "ffmpeg": _tool_version(["ffmpeg", "-version"]),
        "tmpfs_mounts": "available" if shutil.which("mount") else "unavailable",
        "tmpfs_full_mode": full_injection,
        "worker_isolation": worker_isolation,
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


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _worker_argv(options: SlotOptions) -> list[str]:
    return [
        sys.executable,
        str(_SCRIPT),
        "--worker",
        "--attempt",
        str(options.attempt),
        "--wave",
        str(options.wave),
        "--slot",
        str(options.slot),
        "--scenario",
        options.scenario,
        "--tmp-dir",
        str(options.tmp_dir),
        "--clip-seconds",
        str(options.clip_seconds),
        "--fragment-seconds",
        str(options.fragment_seconds),
        "--timeout-s",
        str(options.timeout_s),
        "--max-upload-bytes",
        str(options.max_upload_bytes),
        "--full-injection",
        options.full_injection,
        "--fail-after-bytes",
        str(options.fail_after_bytes),
    ]


def _failed_measurement(
    *, attempt: int, wave: int, slot: int, scenario: str, search_dir: Path, error: str
) -> AttemptMeasurement:
    """A worker that died without reporting still produces a failing row."""
    return AttemptMeasurement(
        attempt=attempt,
        wave=wave,
        slot=slot,
        scenario=scenario,
        outcome="worker_crashed",
        injection=INJECTION_KINDS[scenario],
        injection_confirmed=False,
        upload_bytes=0,
        received_bytes=0,
        fragments=0,
        wall_seconds=0.0,
        tmpfs_peak_bytes=directory_bytes(search_dir),
        tmpfs_after_bytes=directory_bytes(search_dir),
        slot_ram_peak_bytes=0,
        worker_rss_peak_bytes=0,
        asr_group_rss_peak_bytes=0,
        cleanup_verified=False,
        workdir_absent=False,
        live_processes=0,
        open_descriptors=0,
        unreadable_processes=0,
        tmp_is_tmpfs=False,
        cleanup_seconds=0.0,
        slot_dir_orphans=directory_entries(search_dir),
        error=error,
    )


def run_load(
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    slots: int = PROVISIONAL_SLOTS,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    fragment_seconds: int = DEFAULT_FRAGMENT_SECONDS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    worker_timeout_s: float = DEFAULT_WORKER_TIMEOUT_S,
    full_tmpfs_kib: int = DEFAULT_FULL_TMPFS_KIB,
    full_injection: str = "auto",
    fail_after_bytes: int | None = None,
    tmpfs: Path | None = None,
    host_sweep: bool = True,
    host_sweep_budget: int = audio_absence.FILE_BUDGET,
    worker_isolation: str = "auto",
) -> LoadReport:
    """Run the concurrent attempts and aggregate the G1 booleans."""
    if attempts < 1 or attempts > MAX_ATTEMPTS:
        raise ValueError(f"attempts must be between 1 and {MAX_ATTEMPTS}")
    if slots < 1 or slots > MAX_SLOTS:
        raise ValueError(f"slots must be between 1 and {MAX_SLOTS}")
    if not math.isfinite(clip_seconds) or not (
        MIN_CLIP_SECONDS <= clip_seconds <= MAX_CLIP_SECONDS
    ):
        raise ValueError(
            f"clip_seconds must be between {MIN_CLIP_SECONDS} and {MAX_CLIP_SECONDS}"
        )
    if fragment_seconds < 1 or fragment_seconds > 30:
        raise ValueError("fragment_seconds must be between 1 and 30")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive finite number")
    if not math.isfinite(worker_timeout_s) or worker_timeout_s <= 0:
        raise ValueError("worker_timeout_s must be a positive finite number")
    if full_injection not in {"auto", "real", "write_error"}:
        raise ValueError("full_injection must be auto, real or write_error")
    if worker_isolation not in {"auto", "pid_namespace", "shared"}:
        raise ValueError("worker_isolation must be auto, pid_namespace or shared")
    if full_tmpfs_kib < 16:
        raise ValueError("full_tmpfs_kib must be >= 16")
    if fail_after_bytes is not None and fail_after_bytes < 1:
        raise ValueError("fail_after_bytes must be >= 1")

    resolved_isolation = worker_isolation
    if worker_isolation == "auto":
        resolved_isolation = (
            "pid_namespace" if pid_namespace_available() else "shared"
        )
    elif worker_isolation == "pid_namespace" and not pid_namespace_available():
        raise RuntimeError("pid namespace isolation was requested but unshare failed")

    clip_bytes = len(synthesize_tone(clip_seconds))
    if full_injection != "write_error" and full_tmpfs_kib * 1024 >= clip_bytes:
        raise ValueError(
            "the bounded tmpfs must be smaller than the synthetic upload; "
            "lower --full-tmpfs-kib or raise --clip-seconds"
        )
    resolved_fail_after = (
        fail_after_bytes if fail_after_bytes is not None else DEFAULT_FAIL_AFTER_BYTES
    )
    if resolved_fail_after >= clip_bytes:
        raise ValueError("fail_after_bytes must be below the synthetic upload size")

    base = find_tmpfs_base(tmpfs)
    root = Path(tempfile.mkdtemp(prefix=_TMPFS_PREFIX, dir=base))
    capture = _Capture()
    logger = logging.getLogger("maulwurf_ingest")
    logger.addHandler(capture)
    mounted: list[Path] = []
    active: list[subprocess.Popen[str]] = []
    full_point: Path | None = None
    resolved_mode = full_injection
    measurements: list[AttemptMeasurement] = []
    wave_dirs: list[Path] = []
    full_mount_entries = 0
    full_mount_removed = True

    try:
        scenarios = [SCENARIOS[index % len(SCENARIOS)] for index in range(attempts)]
        needs_full = "full" in scenarios
        if needs_full and full_injection in {"auto", "real"}:
            full_point, mount_reason = mount_bounded_tmpfs(root, full_tmpfs_kib)
            if full_point is not None:
                mounted.append(full_point)
                resolved_mode = "real"
            elif full_injection == "real":
                raise RuntimeError(f"cannot mount a bounded tmpfs: {mount_reason}")
            else:
                resolved_mode = "write_error"
        elif needs_full:
            resolved_mode = "write_error"

        for start in range(0, attempts, slots):
            wave = start // slots
            wave_dir = root / f"wave-{wave}"
            wave_dir.mkdir()
            wave_dirs.append(wave_dir)
            batch: list[tuple[int, int, int, str, Path, Path, subprocess.Popen[str]]] = []
            for offset in range(min(slots, attempts - start)):
                attempt = start + offset
                scenario = scenarios[attempt]
                slot = offset
                slot_dir = wave_dir / f"slot-{slot}"
                slot_dir.mkdir()
                tmp_dir = (
                    full_point
                    if scenario == "full" and resolved_mode == "real" and full_point
                    else slot_dir
                )
                options = SlotOptions(
                    attempt=attempt,
                    wave=wave,
                    slot=slot,
                    scenario=scenario,
                    tmp_dir=tmp_dir,
                    clip_seconds=clip_seconds,
                    fragment_seconds=fragment_seconds,
                    timeout_s=timeout_s,
                    max_upload_bytes=settings.max_upload_bytes,
                    full_injection=resolved_mode,
                    fail_after_bytes=resolved_fail_after,
                )
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    _worker_command(options, resolved_isolation),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                active.append(process)
                batch.append(
                    (attempt, wave, slot, scenario, slot_dir, tmp_dir, process)
                )

            deadline = time.monotonic() + worker_timeout_s
            for (
                attempt,
                wave_index,
                slot,
                scenario,
                slot_dir,
                tmp_dir,
                process,
            ) in batch:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    stdout, _stderr = process.communicate(timeout=remaining)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=30)
                    measurements.append(
                        _failed_measurement(
                            attempt=attempt,
                            wave=wave_index,
                            slot=slot,
                            scenario=scenario,
                            search_dir=tmp_dir,
                            error="worker_timeout",
                        )
                    )
                    continue
                finally:
                    if process in active:
                        active.remove(process)
                if process.returncode != 0:
                    measurements.append(
                        _failed_measurement(
                            attempt=attempt,
                            wave=wave_index,
                            slot=slot,
                            scenario=scenario,
                            search_dir=tmp_dir,
                            error=f"worker_exit_{process.returncode}",
                        )
                    )
                    continue
                try:
                    payload = _last_json_line(stdout)
                    measurement = AttemptMeasurement.from_dict(payload)
                except ValueError:
                    measurements.append(
                        _failed_measurement(
                            attempt=attempt,
                            wave=wave_index,
                            slot=slot,
                            scenario=scenario,
                            search_dir=tmp_dir,
                            error="worker_payload_invalid",
                        )
                    )
                    continue
                search_dirs = [slot_dir]
                if tmp_dir != slot_dir:
                    search_dirs.append(tmp_dir)
                orphans = sum(directory_entries(path) for path in search_dirs)
                leftovers = sum(directory_bytes(path) for path in search_dirs)
                measurements.append(
                    replace(
                        measurement,
                        slot_dir_orphans=measurement.slot_dir_orphans + orphans,
                        tmpfs_after_bytes=max(measurement.tmpfs_after_bytes, leftovers),
                    )
                )

        if full_point is not None:
            _, full_mount_entries = unmount_and_remove(full_point)
            full_mount_removed = not full_point.exists()
            mounted.remove(full_point)

        scoped = audio_absence.sweep(
            extra_roots=[root],
            cache_roots=[],
            log_texts=capture.lines,
            trace_texts=[],
            redis_url=None,
            mountinfo="",
        )

        wave_empty = True
        for wave_dir in wave_dirs:
            for entry in list(wave_dir.iterdir()):
                if entry.is_dir() and not any(entry.iterdir()):
                    entry.rmdir()
                else:
                    wave_empty = False
            if any(wave_dir.iterdir()):
                wave_empty = False
            else:
                wave_dir.rmdir()
        root_empty = wave_empty and not any(root.iterdir())
        if full_point is not None:
            root_empty = root_empty and full_mount_entries == 0 and full_mount_removed

        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root_cleaned = not root.exists()

        host: dict[str, object] | None = None
        if host_sweep:
            host_report = audio_absence.sweep(
                extra_roots=[],
                log_texts=capture.lines,
                trace_texts=[],
                redis_url=os.environ.get("MAULWURF_REDIS_URL"),
                budget=host_sweep_budget,
            )
            host = dict(host_report.as_json())

        return LoadReport(
            attempts=attempts,
            slots=slots,
            clip_seconds=clip_seconds,
            fragment_seconds=fragment_seconds,
            timeout_s=timeout_s,
            worker_timeout_s=worker_timeout_s,
            full_injection=resolved_mode,
            full_tmpfs_kib=full_tmpfs_kib if resolved_mode == "real" else None,
            worker_isolation=resolved_isolation,
            measurements=tuple(measurements),
            environment=_environment(resolved_mode, resolved_isolation),
            scoped_absence=dict(scoped.as_json()),
            host_absence=host,
            harness_root_empty=root_empty,
            harness_root_cleaned=root_cleaned,
            per_slot_reservation_bytes=per_slot_reservation_bytes(),
        )
    finally:
        for process in list(active):
            if process.poll() is None:
                process.kill()
                process.wait()
        for point in list(mounted):
            unmount_and_remove(point)
        shutil.rmtree(root, ignore_errors=True)
        logger.removeHandler(capture)


def _bounded_float(minimum: float, maximum: float) -> Any:
    def _parse(value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a number") from exc
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return _parse


def _bounded_int(minimum: int, maximum: int) -> Any:
    def _parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return parsed

    return _parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="g1_load.py",
        description=(
            "G1 re-verification under load with kill, timeout and full-tmpfs injections. "
            "Synthetic audio in RAM only; never calls Riva."
        ),
    )
    parser.add_argument(
        "--attempts",
        type=_bounded_int(1, MAX_ATTEMPTS),
        default=DEFAULT_ATTEMPTS,
        help=f"total attempts, one per scenario first (default: {DEFAULT_ATTEMPTS})",
    )
    parser.add_argument(
        "--slots",
        type=_bounded_int(1, MAX_SLOTS),
        default=PROVISIONAL_SLOTS,
        help=f"concurrent slots per wave (default: {PROVISIONAL_SLOTS})",
    )
    parser.add_argument(
        "--clip-seconds",
        type=_bounded_float(MIN_CLIP_SECONDS, MAX_CLIP_SECONDS),
        default=DEFAULT_CLIP_SECONDS,
        help=f"synthetic tone seconds (default: {DEFAULT_CLIP_SECONDS})",
    )
    parser.add_argument(
        "--fragment-seconds",
        type=_bounded_int(1, 30),
        default=DEFAULT_FRAGMENT_SECONDS,
        help=f"fragment cap (default: {DEFAULT_FRAGMENT_SECONDS})",
    )
    parser.add_argument(
        "--timeout-s",
        type=_bounded_float(0.1, 60.0),
        default=DEFAULT_TIMEOUT_S,
        help=f"ASR deadline for the timeout scenario (default: {DEFAULT_TIMEOUT_S})",
    )
    parser.add_argument(
        "--worker-timeout-s",
        type=_bounded_float(10.0, 600.0),
        default=DEFAULT_WORKER_TIMEOUT_S,
        help=f"per-worker watchdog (default: {DEFAULT_WORKER_TIMEOUT_S})",
    )
    parser.add_argument(
        "--full-tmpfs-kib",
        type=_bounded_int(16, 1_048_576),
        default=DEFAULT_FULL_TMPFS_KIB,
        help=f"size of the bounded private tmpfs (default: {DEFAULT_FULL_TMPFS_KIB} KiB)",
    )
    parser.add_argument(
        "--full-injection",
        choices=("auto", "real", "write_error"),
        default="auto",
        help=(
            "auto: real bounded tmpfs when mount is permitted, injected ENOSPC otherwise; "
            "real: require the mount; write_error: never mount"
        ),
    )
    parser.add_argument(
        "--fail-after-bytes",
        type=_bounded_int(1, 10_000_000),
        default=DEFAULT_FAIL_AFTER_BYTES,
        help=(
            "bytes accepted by the injected write error "
            f"(default: {DEFAULT_FAIL_AFTER_BYTES})"
        ),
    )
    parser.add_argument(
        "--tmpfs",
        type=Path,
        default=None,
        help="writable tmpfs directory; defaults to TMPDIR or /dev/shm",
    )
    parser.add_argument(
        "--no-host-sweep",
        action="store_true",
        help="skip the shared-host absence sweep (context only)",
    )
    parser.add_argument(
        "--worker-isolation",
        choices=("auto", "pid_namespace", "shared"),
        default="auto",
        help=(
            "auto: one PID/mount namespace per worker when unshare is permitted; "
            "shared: one namespace for every worker"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table)",
    )
    return parser


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="g1_load.py --worker")
    parser.add_argument("--attempt", type=_bounded_int(0, MAX_ATTEMPTS - 1), required=True)
    parser.add_argument("--wave", type=_bounded_int(0, MAX_ATTEMPTS - 1), required=True)
    parser.add_argument("--slot", type=_bounded_int(0, MAX_SLOTS - 1), required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--tmp-dir", type=Path, required=True)
    parser.add_argument(
        "--clip-seconds", type=_bounded_float(MIN_CLIP_SECONDS, MAX_CLIP_SECONDS), required=True
    )
    parser.add_argument("--fragment-seconds", type=_bounded_int(1, 30), required=True)
    parser.add_argument("--timeout-s", type=_bounded_float(0.1, 60.0), required=True)
    parser.add_argument("--max-upload-bytes", type=_bounded_int(1, 1_000_000_000), required=True)
    parser.add_argument(
        "--full-injection", choices=("real", "write_error"), required=True
    )
    parser.add_argument("--fail-after-bytes", type=_bounded_int(1, 10_000_000), required=True)
    return parser


def _worker_main(argv: Sequence[str]) -> int:
    args = build_worker_parser().parse_args(argv)
    options = SlotOptions(
        attempt=args.attempt,
        wave=args.wave,
        slot=args.slot,
        scenario=args.scenario,
        tmp_dir=args.tmp_dir,
        clip_seconds=args.clip_seconds,
        fragment_seconds=args.fragment_seconds,
        timeout_s=args.timeout_s,
        max_upload_bytes=args.max_upload_bytes,
        full_injection=args.full_injection,
        fail_after_bytes=args.fail_after_bytes,
    )
    try:
        measurement = asyncio.run(run_slot(options))
    except Exception as exc:  # noqa: BLE001 - the parent only needs a bounded error code
        shutil.rmtree(options.tmp_dir, ignore_errors=True)
        print(
            json.dumps({"ok": False, "attempt": args.attempt, "error": type(exc).__name__}),
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
            attempts=args.attempts,
            slots=args.slots,
            clip_seconds=args.clip_seconds,
            fragment_seconds=args.fragment_seconds,
            timeout_s=args.timeout_s,
            worker_timeout_s=args.worker_timeout_s,
            full_tmpfs_kib=args.full_tmpfs_kib,
            full_injection=args.full_injection,
            fail_after_bytes=args.fail_after_bytes,
            tmpfs=args.tmpfs,
            host_sweep=not args.no_host_sweep,
            worker_isolation=args.worker_isolation,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"g1_load: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render_table())
    return 0 if report.g1_verified_with_injected_fallback else 1


if __name__ == "__main__":
    raise SystemExit(main())
