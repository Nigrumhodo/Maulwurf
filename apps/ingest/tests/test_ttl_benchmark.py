"""S4.4 harness tests: L-S4-SG-02 TTL benchmark.

The unit cases cover the pure projections, the synthetic input and the publication link
without running ffmpeg. The ``load`` case executes the real production reception and
ffmpeg/fragmentation paths on a small synthetic clip in a private tmpfs.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import math
import sys
import wave
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "load" / "ttl_benchmark.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ttl_benchmark", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ttl_benchmark"] = module
    spec.loader.exec_module(module)
    return module


def _reception(module: ModuleType, wall: float, *, slot: int = 0) -> Any:
    return module.ReceptionMeasurement(
        slot=slot,
        payload_bytes=64 * 1024 * 1024,
        wall_seconds=wall,
        first_byte_seconds=0.001,
        sha256_ok=True,
        cleanup_removed=True,
    )


def _asr(module: ModuleType, wall: float, *, slot: int = 0) -> Any:
    return module.AsrMeasurement(
        slot=slot,
        clip_seconds=120.0,
        billed_seconds=121.0,
        fragments=5,
        wall_seconds=wall,
        cleanup_removed=True,
    )


def test_the_script_publishes_exactly_the_benchmark_values() -> None:
    module = _load()
    published = module.published_ttls()

    assert published["ttl"] == {
        "upload_start_minutes": 10,
        "receive_minutes": 30,
        "asr_minutes": 60,
    }
    assert published["ttl_source_report"] == "docs/eval/ttl-benchmark.md"
    assert published["ttl_source_version"] == "2026-11-01"
    assert (module._REPO_ROOT / published["ttl_source_report"]).is_file()


def test_synthetic_stream_yields_exactly_the_requested_bytes() -> None:
    module = _load()
    chunk = b"abcdef"
    total = len(chunk) * 3 + 2

    async def collect() -> bytes:
        return b"".join([part async for part in module.synthetic_stream(total, chunk)])

    body = asyncio.run(collect())
    assert len(body) == total
    assert body == chunk * 3 + chunk[:2]
    assert module.expected_sha256(total, chunk) == hashlib.sha256(body).hexdigest()


def test_the_synthetic_tone_is_a_valid_wav_without_espeak() -> None:
    module = _load()
    payload = module.synthetic_tone_wav(2)

    with wave.open(BytesIO(payload), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 16_000
        assert reader.getnframes() == 32_000


def test_reception_projection_uses_the_slowest_slot_and_the_budget() -> None:
    module = _load()
    # 64 MiB in 2 s = 32 MiB/s; 200 MiB would take 6.25 s.
    fast = _reception(module, 2.0, slot=0)
    slow = _reception(module, 4.0, slot=1)

    projection = module.reception_projection([fast, slow], budget_minutes=30)

    assert projection.slowest_mib_s == pytest.approx(16.0)
    assert projection.aggregate_mib_s == pytest.approx(48.0)
    assert projection.seconds_for_proposal == pytest.approx(12.5)
    assert projection.max_mib_in_budget == pytest.approx(28_800.0)
    assert projection.fits is True
    assert projection.headroom_factor == pytest.approx(144.0)


def test_reception_projection_fails_when_the_transfer_exceeds_the_budget() -> None:
    module = _load()
    # 64 MiB in 100 s = 0.64 MiB/s: 200 MiB needs 312.5 s. A 6-minute budget fits; 1 min not.
    slow = _reception(module, 100.0)
    assert module.reception_projection([slow], budget_minutes=6).fits
    assert not module.reception_projection([slow], budget_minutes=1).fits


def test_asr_projection_adds_the_local_path_and_the_provider_leg() -> None:
    module = _load()
    # 120 s clip in 6 s = 0.05 s/s local; billed 121 s (0.25 s overlap).
    measurement = _asr(module, 6.0)

    projection = module.asr_projection([measurement], budget_minutes=60)

    expected_provider = 3 * 3600 * (121.0 / 120.0) * module.PROVIDER_REALTIME_FACTOR
    assert projection.local_realtime_factor == pytest.approx(0.05)
    assert projection.billed_realtime_factor == pytest.approx(121.0 / 120.0)
    assert projection.projected_local_seconds == pytest.approx(3 * 3600 * 0.05)
    assert projection.projected_provider_seconds == pytest.approx(expected_provider)
    assert projection.fits is True
    assert projection.headroom_factor > 1


def test_asr_projection_fails_when_the_class_does_not_fit() -> None:
    module = _load()
    # A full-speed local run (1 s per audio second) plus the provider leg exceeds 60 min.
    assert not module.asr_projection([_asr(module, 120.0)], budget_minutes=60).fits


def test_upload_start_projection_charges_the_full_window() -> None:
    module = _load()

    projection = module.upload_start_projection(
        upload_seconds=1.0, budget_minutes=30, start_minutes=10
    )

    assert projection.start_delay_seconds == 600.0
    assert projection.reception_budget_seconds == 1_800.0
    assert projection.total_seconds == 601.0
    assert projection.remaining_seconds == 1_799.0
    assert projection.fits is True


def test_the_report_passes_only_when_every_check_and_cleanup_pass() -> None:
    module = _load()
    reception = (_reception(module, 1.0),)
    asr = (_asr(module, 1.0),)
    report = module.TtlBenchmarkReport(
        slots=1,
        upload_mib=64,
        asr_clip_seconds=120,
        published=module.published_ttls(),
        reception=reception,
        asr=asr,
        reception_projection=module.reception_projection(reception, budget_minutes=30),
        asr_projection=module.asr_projection(asr, budget_minutes=60),
        upload_start_projection=module.upload_start_projection(
            upload_seconds=1.0, budget_minutes=30, start_minutes=10
        ),
        environment={"riva_call": "none"},
        root_cleaned=True,
    )

    assert report.passed is True
    payload = report.to_dict()
    assert payload["checks"]["published_ttls_match_the_benchmark"] is True
    assert payload["checks"]["no_temporary_is_left"] is True
    assert payload["limitations"]

    dirty = module.TtlBenchmarkReport(
        slots=report.slots,
        upload_mib=report.upload_mib,
        asr_clip_seconds=report.asr_clip_seconds,
        published=report.published,
        reception=report.reception,
        asr=report.asr,
        reception_projection=report.reception_projection,
        asr_projection=report.asr_projection,
        upload_start_projection=report.upload_start_projection,
        environment=report.environment,
        root_cleaned=False,
    )
    assert dirty.passed is False


def test_invalid_configuration_is_rejected() -> None:
    module = _load()

    with pytest.raises(ValueError, match="upload_mib"):
        module.run_benchmark(upload_mib=0)
    with pytest.raises(ValueError, match="slots"):
        module.run_benchmark(slots=99)
    with pytest.raises(ValueError, match="asr_clip_seconds"):
        module.run_benchmark(clip_seconds=1)


def test_the_supervisor_asr_cap_matches_the_published_ttl() -> None:
    """The 60-min TTL must not be silently shortened by the old 600 s child cap."""
    from types import SimpleNamespace

    from pydantic import ValidationError

    from maulwurf_ingest.config import Settings, settings
    from maulwurf_ingest.receptions import _asr_config
    from maulwurf_ingest.supervisor import SupervisorConfig

    assert settings.asr_timeout_s == 60 * 60
    assert SupervisorConfig(instance="test").asr_timeout_s == 60 * 60

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(instance_id="ingest:test"))
    )
    config = _asr_config(request)  # type: ignore[arg-type]
    assert config.asr_timeout_s == settings.asr_timeout_s == 60 * 60

    for raw in ("600", "7200"):
        with pytest.raises(ValidationError):
            Settings(INGEST_ASR_TIMEOUT_S=raw)  # type: ignore[call-arg]


@pytest.mark.load
def test_real_ttl_benchmark_measures_the_production_paths() -> None:
    module = _load()
    try:
        report = module.run_benchmark(upload_mib=8, slots=1, clip_seconds=10)
    except RuntimeError as exc:
        pytest.skip(f"no tmpfs available: {exc}")

    assert report.passed is True
    assert report.reception[0].sha256_ok is True
    assert report.reception[0].cleanup_removed is True
    assert report.asr[0].fragments >= 1
    assert report.asr[0].cleanup_removed is True
    assert math.isfinite(report.reception_projection.slowest_mib_s)
    assert report.root_cleaned is True
