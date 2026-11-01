"""S4.4: effective TTL benchmark for L-S4-SG-02.

The ticket proposes three ephemeral budgets: 10 minutes to start the upload, 30 minutes to
receive it and 60 minutes for ASR. This harness measures the production paths and publishes
the data behind those values:

* **Reception (30 min):** ``receiver.receive`` streams a synthetic payload into a private
  tmpfs attempt directory, hashing it chunk by chunk. Several slots run concurrently. The
  projection is the time a 200 MiB upload would take at the slowest measured slot.
* **ASR (60 min):** the production ``pipeline.process`` path (ffprobe, silencedetect, ffmpeg
  fragment extraction, at most one fragment on disk) with an empty-word stub transcriber.
  No Riva client or key is constructed. The projection to a 3 h class is linear from the
  measured clip; the provider leg uses the F0.1 S1.A7 observation (30.0 s audio, 1.692 s
  latency) and is explicitly a projection, not a new provider measurement.
* **Upload start (10 min):** the worst case is a client that uses the whole start window and
  then uploads 200 MiB. The chained budget is start + reception; the measured reception
  throughput decides whether that upload fits.

The script also checks that ``GET /ingestion/capabilities`` would publish exactly the
values measured here (the defaults in ``app/schemas/ingestion.py``), including the benchmark
source. It never calls a provider, never writes PostgreSQL/Redis and never keeps audio:
the synthetic tone is generated in RAM and every attempt directory is deleted and verified.

Run from the repository root with the ingest environment::

    cd apps/ingest && uv run python ../../scripts/load/ttl_benchmark.py
    cd apps/ingest && uv run python ../../scripts/load/ttl_benchmark.py --format json
    cd apps/ingest && uv run python ../../scripts/load/ttl_benchmark.py \
        --upload-mib 96 --slots 2 --asr-clip-seconds 180

Exit codes: 0 when every TTL check passes, 1 when a check fails, 2 when the configuration
cannot run the benchmark (no tmpfs, not enough free space, bad input).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Final

_SCRIPT = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT.parents[2]
_INGEST_ROOT = _REPO_ROOT / "apps" / "ingest"
_API_ROOT = _REPO_ROOT / "apps" / "api"
for _root in (_INGEST_ROOT, _API_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from app.schemas.ingestion import (  # noqa: E402
    EFFECTIVE_TTL_MINUTES,
    TTL_SOURCE_REPORT,
    TTL_SOURCE_VERSION,
    IngestionCapabilities,
)

from maulwurf_ingest import cleanup, hardening, receiver  # noqa: E402
from maulwurf_ingest.audio.fragmenter import wav_duration_s  # noqa: E402
from maulwurf_ingest.config import settings  # noqa: E402
from maulwurf_ingest.pipeline import FragmentWords  # noqa: E402
from maulwurf_ingest.pipeline import process as process_audio  # noqa: E402

TICKET: Final = "S4.4"
TEST_BASIS: Final = "L-S4-SG-02"
PROPOSED_TTL_MINUTES: Final[dict[str, int]] = dict(EFFECTIVE_TTL_MINUTES)
PROVISIONAL_UPLOAD_MIB: Final = 200
PROVISIONAL_CLASS_SECONDS: Final = 3 * 3600
DEFAULT_UPLOAD_MIB: Final = 64
MAX_UPLOAD_MIB: Final = PROVISIONAL_UPLOAD_MIB
DEFAULT_SLOTS: Final = 2
MAX_SLOTS: Final = 4
DEFAULT_ASR_CLIP_SECONDS: Final = 120
MIN_ASR_CLIP_SECONDS: Final = 5
MAX_ASR_CLIP_SECONDS: Final = 300
_CHUNK_BYTES: Final = 65_536
_MIB: Final = 1024 * 1024
# F0.1 S1.A7, row `es` at 30.0 s: 1.692 s of latency including call establishment.
PROVIDER_REALTIME_FACTOR: Final = 1.692 / 30.0
PROVIDER_BASIS: Final = "F0.1 S1.A7: 30.000 s audio -> 1.692 s latency"
_TMPFS_PREFIX = "mw-s44-"


@dataclass(frozen=True)
class ReceptionMeasurement:
    """One concurrent reception: bytes written through the production receiver."""

    slot: int
    payload_bytes: int
    wall_seconds: float
    first_byte_seconds: float
    sha256_ok: bool
    cleanup_removed: bool

    @property
    def throughput_mib_s(self) -> float:
        if self.wall_seconds <= 0:
            raise ValueError("reception wall time must be positive")
        return (self.payload_bytes / _MIB) / self.wall_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "payload_bytes": self.payload_bytes,
            "wall_seconds": round(self.wall_seconds, 6),
            "first_byte_seconds": round(self.first_byte_seconds, 6),
            "throughput_mib_s": round(self.throughput_mib_s, 6),
            "sha256_ok": self.sha256_ok,
            "cleanup_removed": self.cleanup_removed,
        }


@dataclass(frozen=True)
class AsrMeasurement:
    """One concurrent ASR run over the production audio pipeline and a stub transcriber."""

    slot: int
    clip_seconds: float
    billed_seconds: float
    fragments: int
    wall_seconds: float
    cleanup_removed: bool

    @property
    def local_realtime_factor(self) -> float:
        if self.clip_seconds <= 0:
            raise ValueError("clip seconds must be positive")
        return self.wall_seconds / self.clip_seconds

    @property
    def billed_realtime_factor(self) -> float:
        return self.billed_seconds / self.clip_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "clip_seconds": round(self.clip_seconds, 6),
            "billed_seconds": round(self.billed_seconds, 6),
            "fragments": self.fragments,
            "wall_seconds": round(self.wall_seconds, 6),
            "local_realtime_factor": round(self.local_realtime_factor, 6),
            "billed_realtime_factor": round(self.billed_realtime_factor, 6),
            "cleanup_removed": self.cleanup_removed,
        }


@dataclass(frozen=True)
class ReceptionProjection:
    """Projection of the measured reception throughput to the proposal size."""

    slowest_mib_s: float
    aggregate_mib_s: float
    proposal_mib: float
    seconds_for_proposal: float
    max_mib_in_budget: float
    budget_minutes: int
    headroom_factor: float
    fits: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "slowest_mib_s": round(self.slowest_mib_s, 6),
            "aggregate_mib_s": round(self.aggregate_mib_s, 6),
            "proposal_mib": self.proposal_mib,
            "seconds_for_proposal": round(self.seconds_for_proposal, 6),
            "max_mib_in_budget": round(self.max_mib_in_budget, 3),
            "budget_minutes": self.budget_minutes,
            "headroom_factor": round(self.headroom_factor, 3),
            "fits": self.fits,
        }


@dataclass(frozen=True)
class AsrProjection:
    """Linear projection of the measured local ASR path plus the F0.1 provider leg."""

    provider_realtime_factor: float
    provider_basis: str
    local_realtime_factor: float
    billed_realtime_factor: float
    class_seconds: float
    projected_local_seconds: float
    projected_provider_seconds: float
    projected_total_seconds: float
    budget_minutes: int
    max_class_seconds: float
    headroom_factor: float
    fits: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_realtime_factor": round(self.provider_realtime_factor, 9),
            "provider_basis": self.provider_basis,
            "local_realtime_factor": round(self.local_realtime_factor, 9),
            "billed_realtime_factor": round(self.billed_realtime_factor, 9),
            "class_seconds": self.class_seconds,
            "projected_local_seconds": round(self.projected_local_seconds, 3),
            "projected_provider_seconds": round(self.projected_provider_seconds, 3),
            "projected_total_seconds": round(self.projected_total_seconds, 3),
            "budget_minutes": self.budget_minutes,
            "max_class_seconds": round(self.max_class_seconds, 3),
            "headroom_factor": round(self.headroom_factor, 3),
            "fits": self.fits,
        }


@dataclass(frozen=True)
class UploadStartProjection:
    """Worst case: the client uses the full start window and then sends the proposal."""

    start_window_seconds: float
    reception_budget_seconds: float
    start_delay_seconds: float
    upload_seconds: float
    total_seconds: float
    remaining_seconds: float
    fits: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_window_seconds": self.start_window_seconds,
            "reception_budget_seconds": self.reception_budget_seconds,
            "start_delay_seconds": self.start_delay_seconds,
            "upload_seconds": round(self.upload_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "remaining_seconds": round(self.remaining_seconds, 3),
            "fits": self.fits,
        }


@dataclass(frozen=True)
class TtlBenchmarkReport:
    """Measured TTL benchmark and the checks that bind it to the publication."""

    slots: int
    upload_mib: int
    asr_clip_seconds: int
    published: dict[str, Any]
    reception: tuple[ReceptionMeasurement, ...]
    asr: tuple[AsrMeasurement, ...]
    reception_projection: ReceptionProjection
    asr_projection: AsrProjection
    upload_start_projection: UploadStartProjection
    environment: dict[str, str]
    root_cleaned: bool

    @property
    def cleanup_removed(self) -> bool:
        return self.root_cleaned and all(
            item.cleanup_removed for item in (*self.reception, *self.asr)
        )

    @property
    def checks(self) -> dict[str, bool]:
        return {
            "published_ttls_match_the_benchmark": (
                self.published["ttl"] == PROPOSED_TTL_MINUTES
            ),
            "published_ttl_source": (
                self.published["ttl_source_report"] == TTL_SOURCE_REPORT
                and self.published["ttl_source_version"] == TTL_SOURCE_VERSION
            ),
            "reception_budget_fits_the_proposal": self.reception_projection.fits,
            "upload_start_chain_fits": self.upload_start_projection.fits,
            "asr_budget_fits_the_proposal": self.asr_projection.fits,
            "no_temporary_is_left": self.cleanup_removed,
        }

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": TICKET,
            "test_basis": TEST_BASIS,
            "environment": self.environment,
            "proposal": {
                "upload_start_minutes": PROPOSED_TTL_MINUTES["upload_start_minutes"],
                "receive_minutes": PROPOSED_TTL_MINUTES["receive_minutes"],
                "asr_minutes": PROPOSED_TTL_MINUTES["asr_minutes"],
                "upload_mib": PROVISIONAL_UPLOAD_MIB,
                "class_seconds": PROVISIONAL_CLASS_SECONDS,
            },
            "published_capabilities": self.published,
            "load": {
                "slots": self.slots,
                "concurrent": True,
                "upload_mib": self.upload_mib,
                "asr_clip_seconds": self.asr_clip_seconds,
            },
            "reception": [item.to_dict() for item in self.reception],
            "reception_projection": self.reception_projection.to_dict(),
            "upload_start_projection": self.upload_start_projection.to_dict(),
            "asr": [item.to_dict() for item in self.asr],
            "asr_projection": self.asr_projection.to_dict(),
            "checks": self.checks,
            "passed": self.passed,
            "limitations": _limitations(),
        }

    def render_table(self) -> str:
        environment = ", ".join(f"{key}={value}" for key, value in self.environment.items())
        reception = self.reception_projection
        asr = self.asr_projection
        start = self.upload_start_projection
        lines = [
            f"{TICKET} effective TTL benchmark (basis for {TEST_BASIS})",
            f"Environment: {environment}",
            (
                "Published TTLs: "
                f"upload_start={PROPOSED_TTL_MINUTES['upload_start_minutes']} min, "
                f"receive={PROPOSED_TTL_MINUTES['receive_minutes']} min, "
                f"asr={PROPOSED_TTL_MINUTES['asr_minutes']} min "
                f"({self.published['ttl_source_report']} "
                f"{self.published['ttl_source_version']})"
            ),
            (
                f"Reception: {self.slots} concurrent slot(s), {self.upload_mib} MiB each; "
                f"slowest {reception.slowest_mib_s:.2f} MiB/s, aggregate "
                f"{reception.aggregate_mib_s:.2f} MiB/s"
            ),
            (
                f"           200 MiB projected in {reception.seconds_for_proposal:.2f} s "
                f"against {reception.budget_minutes * 60} s: fits="
                f"{_yes_no(reception.fits)} (headroom x{reception.headroom_factor:.2f})"
            ),
            (
                f"           bytes that fit the budget: {reception.max_mib_in_budget:.1f} MiB"
            ),
            (
                f"Upload start: worst-case delay {start.start_delay_seconds:.0f} s + "
                f"upload {start.upload_seconds:.2f} s = {start.total_seconds:.2f} s "
                f"of {start.start_window_seconds + start.reception_budget_seconds:.0f} s: "
                f"fits={_yes_no(start.fits)}"
            ),
            (
                f"ASR: {self.slots} concurrent slot(s), {self.asr_clip_seconds} s clip; "
                f"local {asr.local_realtime_factor:.4f} s/s, billed "
                f"{asr.billed_realtime_factor:.4f} s/s, provider "
                f"{asr.provider_realtime_factor:.4f} s/s ({asr.provider_basis})"
            ),
            (
                f"     3 h class projected in {asr.projected_total_seconds:.2f} s "
                f"(local {asr.projected_local_seconds:.2f} + provider "
                f"{asr.projected_provider_seconds:.2f}) against "
                f"{asr.budget_minutes * 60} s: fits={_yes_no(asr.fits)} "
                f"(headroom x{asr.headroom_factor:.2f})"
            ),
            (
                f"     class length that fits the ASR budget: "
                f"{asr.max_class_seconds / 60:.1f} min"
            ),
            (
                f"Cleanup: every attempt directory removed={_yes_no(self.cleanup_removed)}; "
                f"harness root empty={_yes_no(self.root_cleaned)}"
            ),
            "Checks: "
            + ", ".join(f"{name}={_yes_no(value)}" for name, value in self.checks.items()),
            f"Result: {'PASS' if self.passed else 'FAIL'} (no Riva call, no audio kept)",
        ]
        return "\n".join(lines)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def published_ttls() -> dict[str, Any]:
    """The TTLs the API would publish today, with their benchmark source."""
    capabilities = IngestionCapabilities()
    return {
        "limits_validated": capabilities.limits_validated,
        "ttl": capabilities.ttl.model_dump(),
        "ttl_source_report": capabilities.ttl_source_report,
        "ttl_source_version": capabilities.ttl_source_version,
    }


async def synthetic_stream(total_bytes: int, chunk: bytes) -> AsyncIterator[bytes]:
    """Yield the same deterministic chunk until ``total_bytes``; never builds the payload."""
    if total_bytes < 0:
        raise ValueError("total_bytes must not be negative")
    if not chunk:
        raise ValueError("chunk must not be empty")
    remaining = total_bytes
    while remaining >= len(chunk):
        yield chunk
        remaining -= len(chunk)
    if remaining:
        yield chunk[:remaining]


def expected_sha256(total_bytes: int, chunk: bytes) -> str:
    """SHA-256 of ``synthetic_stream(total_bytes, chunk)`` without holding the payload."""
    if total_bytes < 0:
        raise ValueError("total_bytes must not be negative")
    if not chunk:
        raise ValueError("chunk must not be empty")
    digest = hashlib.sha256()
    remaining = total_bytes
    while remaining >= len(chunk):
        digest.update(chunk)
        remaining -= len(chunk)
    if remaining:
        digest.update(chunk[:remaining])
    return digest.hexdigest()


def synthetic_tone_wav(seconds: int, *, sample_rate: int = 16_000) -> bytes:
    """A mono PCM s16 tone generated in RAM. No speech, no espeak-ng, no file kept."""
    if seconds < 1:
        raise ValueError("seconds must be at least 1")
    if sample_rate < 1:
        raise ValueError("sample_rate must be positive")
    amplitude = 8_000
    one_second = array(
        "h",
        (
            int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(sample_rate)
        ),
    )
    frames = one_second.tobytes() * seconds
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(frames)
    return buffer.getvalue()


def reception_projection(
    measurements: Sequence[ReceptionMeasurement],
    *,
    proposal_mib: int = PROVISIONAL_UPLOAD_MIB,
    budget_minutes: int,
) -> ReceptionProjection:
    """Project the slowest measured slot to the proposal size and compare with the budget."""
    if not measurements:
        raise ValueError("at least one reception measurement is required")
    slowest = min(item.throughput_mib_s for item in measurements)
    aggregate = sum(item.throughput_mib_s for item in measurements)
    if slowest <= 0:
        raise ValueError("reception throughput must be positive")
    seconds = proposal_mib / slowest
    budget_seconds = budget_minutes * 60
    max_mib = slowest * budget_seconds
    return ReceptionProjection(
        slowest_mib_s=slowest,
        aggregate_mib_s=aggregate,
        proposal_mib=float(proposal_mib),
        seconds_for_proposal=seconds,
        max_mib_in_budget=max_mib,
        budget_minutes=budget_minutes,
        headroom_factor=budget_seconds / seconds if seconds > 0 else math.inf,
        fits=seconds <= budget_seconds,
    )


def asr_projection(
    measurements: Sequence[AsrMeasurement],
    *,
    class_seconds: int = PROVISIONAL_CLASS_SECONDS,
    budget_minutes: int,
    provider_realtime_factor: float = PROVIDER_REALTIME_FACTOR,
) -> AsrProjection:
    """Project local pipeline + provider leg to a class; take the slowest measured slot."""
    if not measurements:
        raise ValueError("at least one ASR measurement is required")
    if not math.isfinite(provider_realtime_factor) or provider_realtime_factor <= 0:
        raise ValueError("provider realtime factor must be positive and finite")
    slowest = max(measurements, key=lambda item: item.local_realtime_factor)
    local = slowest.local_realtime_factor
    billed = slowest.billed_realtime_factor
    projected_local = class_seconds * local
    projected_provider = class_seconds * billed * provider_realtime_factor
    projected_total = projected_local + projected_provider
    budget_seconds = budget_minutes * 60
    return AsrProjection(
        provider_realtime_factor=provider_realtime_factor,
        provider_basis=PROVIDER_BASIS,
        local_realtime_factor=local,
        billed_realtime_factor=billed,
        class_seconds=float(class_seconds),
        projected_local_seconds=projected_local,
        projected_provider_seconds=projected_provider,
        projected_total_seconds=projected_total,
        budget_minutes=budget_minutes,
        max_class_seconds=budget_seconds / (local + billed * provider_realtime_factor)
        if local + billed * provider_realtime_factor > 0
        else math.inf,
        headroom_factor=budget_seconds / projected_total if projected_total > 0 else math.inf,
        fits=projected_total <= budget_seconds,
    )


def upload_start_projection(
    *,
    upload_seconds: float,
    budget_minutes: int,
    start_minutes: int,
) -> UploadStartProjection:
    """Worst case: the full start window is consumed before the upload begins."""
    start_window = start_minutes * 60
    reception_budget = budget_minutes * 60
    total = start_window + upload_seconds
    remaining = start_window + reception_budget - total
    return UploadStartProjection(
        start_window_seconds=start_window,
        reception_budget_seconds=reception_budget,
        start_delay_seconds=start_window,
        upload_seconds=upload_seconds,
        total_seconds=total,
        remaining_seconds=remaining,
        fits=remaining >= 0,
    )


def find_tmpfs_base(override: Path | None = None) -> Path:
    """A writable tmpfs directory. Never falls back to persistent disk."""
    candidates = [override] if override is not None else [
        Path(tempfile.gettempdir()),
        Path("/dev/shm"),  # noqa: S108 — tmpfs candidate, verified by mountinfo
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
    raise RuntimeError(
        "no writable tmpfs found; pass --tmpfs pointing at a tmpfs directory"
    )


def _available_bytes(path: Path) -> int:
    try:
        statvfs = os.statvfs(path)
    except OSError as exc:
        raise RuntimeError(f"cannot stat {path}") from exc
    return statvfs.f_bavail * statvfs.f_frsize


def _require_room(base: Path, required_bytes: int) -> None:
    available = _available_bytes(base)
    margin = 16 * _MIB
    if available < required_bytes + margin:
        raise RuntimeError(
            f"not enough tmpfs room: need {required_bytes + margin} bytes, "
            f"have {available}"
        )


async def _measure_reception(
    *,
    slot: int,
    root: Path,
    payload_bytes: int,
    chunk: bytes,
) -> ReceptionMeasurement:
    workdir = root / f"reception-{slot}"
    workdir.mkdir(mode=0o700)
    dest = workdir / "original"
    expected = expected_sha256(payload_bytes, chunk)
    first_at: float | None = None
    started = time.perf_counter()

    async def timed_stream() -> AsyncIterator[bytes]:
        nonlocal first_at
        async for part in synthetic_stream(payload_bytes, chunk):
            if first_at is None:
                first_at = time.perf_counter()
            yield part

    try:
        result = await receiver.receive(timed_stream(), dest, max_bytes=payload_bytes + 1)
        wall_seconds = time.perf_counter() - started
        sha256_ok = result.sha256 == expected and result.received_bytes == payload_bytes
        first_byte_seconds = (
            (first_at - started) if first_at is not None else wall_seconds
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return ReceptionMeasurement(
        slot=slot,
        payload_bytes=payload_bytes,
        wall_seconds=wall_seconds,
        first_byte_seconds=first_byte_seconds,
        sha256_ok=sha256_ok,
        cleanup_removed=not workdir.exists(),
    )


async def run_reception_load(
    *,
    root: Path,
    upload_mib: int,
    slots: int,
) -> tuple[ReceptionMeasurement, ...]:
    payload_bytes = upload_mib * _MIB
    chunk = bytes(range(256)) * (_CHUNK_BYTES // 256)
    measurements = await asyncio.gather(
        *(
            _measure_reception(
                slot=slot,
                root=root,
                payload_bytes=payload_bytes,
                chunk=chunk,
            )
            for slot in range(slots)
        )
    )
    return tuple(measurements)


def _measure_asr(
    *,
    slot: int,
    root: Path,
    clip_wav: bytes,
    clip_seconds: int,
) -> AsrMeasurement:
    workdir = root / f"asr-{slot}"
    workdir.mkdir(mode=0o700)
    source = workdir / "original.wav"
    cleanup.write_private(source, clip_wav)
    billed_seconds = 0.0

    def hear_fragment(path: Path) -> FragmentWords:
        nonlocal billed_seconds
        billed_seconds += wav_duration_s(path)
        return FragmentWords()

    started = time.perf_counter()
    try:
        result = process_audio(
            source,
            workdir,
            fragment_seconds=float(settings.fragment_seconds),
            overlap_s=settings.fragment_overlap_s,
            transcribe=hear_fragment,
        )
        wall_seconds = time.perf_counter() - started
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return AsrMeasurement(
        slot=slot,
        clip_seconds=float(clip_seconds),
        billed_seconds=billed_seconds,
        fragments=result.fragments,
        wall_seconds=wall_seconds,
        cleanup_removed=not workdir.exists(),
    )


async def run_asr_load(
    *,
    root: Path,
    clip_seconds: int,
    slots: int,
) -> tuple[AsrMeasurement, ...]:
    clip_wav = synthetic_tone_wav(clip_seconds)
    measurements = await asyncio.gather(
        *(
            asyncio.to_thread(
                _measure_asr,
                slot=slot,
                root=root,
                clip_wav=clip_wav,
                clip_seconds=clip_seconds,
            )
            for slot in range(slots)
        )
    )
    return tuple(measurements)


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "system": f"{platform.system()} {platform.release()}",
        "ffmpeg": _tool_version(["ffmpeg", "-version"]),
        "riva_call": "none",
    }


def _tool_version(argv: Sequence[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    first = (completed.stdout or completed.stderr).splitlines()
    return first[0].strip() if first else "unavailable"


def run_benchmark(
    *,
    upload_mib: int = DEFAULT_UPLOAD_MIB,
    slots: int = DEFAULT_SLOTS,
    clip_seconds: int = DEFAULT_ASR_CLIP_SECONDS,
    tmpfs: Path | None = None,
) -> TtlBenchmarkReport:
    """Measure both phases and return the report. The caller decides on the exit code."""
    if not 1 <= upload_mib <= MAX_UPLOAD_MIB:
        raise ValueError(f"upload_mib must be between 1 and {MAX_UPLOAD_MIB}")
    if not 1 <= slots <= MAX_SLOTS:
        raise ValueError(f"slots must be between 1 and {MAX_SLOTS}")
    if not MIN_ASR_CLIP_SECONDS <= clip_seconds <= MAX_ASR_CLIP_SECONDS:
        raise ValueError(
            f"asr_clip_seconds must be between {MIN_ASR_CLIP_SECONDS} and "
            f"{MAX_ASR_CLIP_SECONDS}"
        )
    published = published_ttls()
    if published["ttl"] != PROPOSED_TTL_MINUTES:
        raise RuntimeError("published TTLs do not match the benchmark proposal")
    source = published["ttl_source_report"]
    if not isinstance(source, str) or not (_REPO_ROOT / source).is_file():
        raise RuntimeError("published TTL source report is missing")

    base = find_tmpfs_base(tmpfs)
    clip_bytes = len(synthetic_tone_wav(clip_seconds))
    required = slots * max(upload_mib * _MIB, clip_bytes)
    _require_room(base, required)

    root = Path(tempfile.mkdtemp(prefix=_TMPFS_PREFIX, dir=base))
    try:
        reception = asyncio.run(
            run_reception_load(root=root, upload_mib=upload_mib, slots=slots)
        )
        asr = asyncio.run(
            run_asr_load(root=root, clip_seconds=clip_seconds, slots=slots)
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    root_cleaned = not root.exists()

    receive_projection = reception_projection(
        reception, budget_minutes=PROPOSED_TTL_MINUTES["receive_minutes"]
    )
    asr_projection_value = asr_projection(
        asr, budget_minutes=PROPOSED_TTL_MINUTES["asr_minutes"]
    )
    start_projection = upload_start_projection(
        upload_seconds=receive_projection.seconds_for_proposal,
        budget_minutes=PROPOSED_TTL_MINUTES["receive_minutes"],
        start_minutes=PROPOSED_TTL_MINUTES["upload_start_minutes"],
    )
    return TtlBenchmarkReport(
        slots=slots,
        upload_mib=upload_mib,
        asr_clip_seconds=clip_seconds,
        published=published,
        reception=reception,
        asr=asr,
        reception_projection=receive_projection,
        asr_projection=asr_projection_value,
        upload_start_projection=start_projection,
        environment=environment(),
        root_cleaned=root_cleaned,
    )


def _bounded_int(minimum: int, maximum: int) -> Any:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}"
            )
        return number

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upload-mib",
        type=_bounded_int(1, MAX_UPLOAD_MIB),
        default=DEFAULT_UPLOAD_MIB,
        help=f"payload per reception slot (default {DEFAULT_UPLOAD_MIB} MiB)",
    )
    parser.add_argument(
        "--slots",
        type=_bounded_int(1, MAX_SLOTS),
        default=DEFAULT_SLOTS,
        help=f"concurrent slots (default {DEFAULT_SLOTS})",
    )
    parser.add_argument(
        "--asr-clip-seconds",
        type=_bounded_int(MIN_ASR_CLIP_SECONDS, MAX_ASR_CLIP_SECONDS),
        default=DEFAULT_ASR_CLIP_SECONDS,
        help=f"synthetic clip measured by ASR (default {DEFAULT_ASR_CLIP_SECONDS} s)",
    )
    parser.add_argument(
        "--tmpfs",
        type=Path,
        default=None,
        help="writable tmpfs directory (default: TMPDIR or /dev/shm)",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="human table or machine-readable JSON",
    )
    return parser


def _limitations() -> list[str]:
    return [
        "The client uplink is not measured: the reception projection covers the server "
        "path (API -> ingest -> tmpfs), not the user's network or the Caddy edge.",
        "The provider leg is a linear projection from one F0.1 S1.A7 30 s observation "
        "(1.692 s); no Riva channel, key or new provider call is used.",
        "The ASR synthetic clip is a tone, not speech; local ffmpeg/fragmentation time is "
        "measured, not transcription quality or provider queueing.",
        "No PostgreSQL, Redis or API HTTP hop is exercised; the API->ingest streaming path "
        "is represented by receiver.receive and is covered by I-S2-AN-12.",
        "The reception deadline is the published application budget; the ingest receiver "
        "does not hard-kill a live stream at 30 min (the client disconnect and the edge "
        "bound it). The ASR child cap is 60 min from the transcribing phase.",
        "200 MiB and 3 h remain unapproved. D4 is blocked; the 200 MiB/3 h rows are the "
        "proposal used to size the projection, not a published limit.",
        "The 10/30/60 min values do not become approved by this run: they are published "
        "only when their benchmark source is present in capabilities.",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_benchmark(
            upload_mib=args.upload_mib,
            slots=args.slots,
            clip_seconds=args.asr_clip_seconds,
            tmpfs=args.tmpfs,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render_table())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
