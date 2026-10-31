"""S3.5 harness tests: ASR effective minutes, cost rows and RAM/tmpfs reporting.

These tests are the unit support for C-S4-SG-04 (the S4.5 reproducible cost table). The
pure math never calls ffmpeg or Riva. The real audio-path measurement runs only under the
``load`` marker, and it writes solely to a tmpfs that it deletes and verifies. No audio,
transcript or provider output is persisted by any test.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import wave
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "load" / "asr_cost.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("asr_cost", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["asr_cost"] = module
    spec.loader.exec_module(module)
    return module


def _frame(module: ModuleType, *, tmpfs_peak: int = 3_600_000) -> Any:
    return module.SlotMeasurement(
        slot=0,
        fragments=3,
        billed_seconds=60.5,
        wall_seconds=1.25,
        tmpfs_peak_bytes=tmpfs_peak,
        tmpfs_after_bytes=100,
        rss_before_bytes=50_000_000,
        rss_peak_bytes=70_000_000,
        rss_after_bytes=69_000_000,
        ffmpeg_child_maxrss_bytes=12_000_000,
        cleanup_removed=True,
    )


def _report(module: ModuleType, cost_rate: float | None = None) -> Any:
    usage = module.AsrUsage(source_seconds=60.0, fragments=3, billed_seconds=60.5)
    return module.LoadReport(
        slots=1,
        clip_seconds=60.0,
        fragment_seconds=30,
        overlap_seconds=0.25,
        worker_timeout_s=300.0,
        measurements=(_frame(module),),
        tmpfs_peak_sampled_bytes=3_500_000,
        root_cleaned=True,
        usage=usage,
        cost=module.estimate_asr_cost(usage, cost_rate),
        environment={"python": "3.12", "ffmpeg": "test", "riva_call": "none"},
    )


def test_defaults_keep_the_provisional_limits_unapproved() -> None:
    harness = _load()

    assert harness.TICKET == "S3.5"
    assert harness.TEST_BASIS == "C-S4-SG-04"
    assert harness.PROVISIONAL_SLOTS == 2
    assert harness.PROVISIONAL_TMPFS_BYTES == 256 * 1024 * 1024
    assert harness.PROVISIONAL_MEM_LIMIT_BYTES == 1024 * 1024 * 1024
    assert harness.APPROVED_200_MIB is False
    assert harness.APPROVED_3H is False
    assert harness.DEFAULT_CLIP_SECONDS == 60.0


def test_usage_from_spans_counts_the_local_overlap() -> None:
    harness = _load()
    usage = harness.usage_from_spans([(0.0, 30.0), (29.75, 59.75), (59.5, 60.0)])

    assert usage.source_seconds == 60.0
    assert usage.fragments == 3
    assert usage.billed_seconds == pytest.approx(60.5)
    assert usage.billed_minutes == pytest.approx(60.5 / 60)
    assert usage.overlap_seconds == pytest.approx(0.5)
    assert usage.amplification == pytest.approx(60.5 / 60)
    assert usage.effective_minutes_per_class_hour == pytest.approx(60.5)


def test_the_production_fragment_plan_adds_under_one_percent() -> None:
    from maulwurf_ingest.audio.fragmenter import plan_fragments

    harness = _load()
    spans = plan_fragments(60.0, (), max_fragment_s=30, overlap_s=0.25)
    usage = harness.usage_from_spans([(span.start_s, span.end_s) for span in spans])

    assert usage.fragments == 3
    assert usage.billed_seconds == pytest.approx(60.5)
    assert usage.amplification < 1.01
    assert usage.effective_minutes_per_class_hour < 61.0


def test_usage_rejects_invalid_spans() -> None:
    harness = _load()

    with pytest.raises(ValueError, match="at least one"):
        harness.usage_from_spans([])
    with pytest.raises(ValueError, match="invalid span"):
        harness.usage_from_spans([(1.0, 1.0)])
    with pytest.raises(ValueError, match="ordered"):
        harness.usage_from_spans([(10.0, 20.0), (0.0, 5.0)])
    with pytest.raises(ValueError, match="exceed"):
        harness.usage_from_spans([(0.0, 61.0)], source_seconds=60.0)


def test_usage_rejects_billing_less_than_the_source() -> None:
    harness = _load()

    with pytest.raises(ValueError, match="below"):
        harness.AsrUsage(source_seconds=60.0, fragments=1, billed_seconds=59.0)


def test_cost_without_a_measured_rate_stays_unverified() -> None:
    harness = _load()
    usage = harness.AsrUsage(source_seconds=60.0, fragments=3, billed_seconds=60.5)
    cost = harness.estimate_asr_cost(usage, None)
    payload = cost.to_dict()

    assert cost.rate_status == "NO VERIFICADO"
    assert cost.cost_usd == "NO VERIFICADO"
    assert cost.cost_per_class_hour_usd == "NO VERIFICADO"
    assert payload["rate_usd_per_minute"] is None
    assert payload["rate_status"] == "NO VERIFICADO"
    assert "NO VERIFICADO" in payload["rate_source"]
    assert payload["cost_per_class_hour_usd"] == "NO VERIFICADO"


def test_explicit_rate_multiplies_effective_minutes() -> None:
    harness = _load()
    usage = harness.AsrUsage(source_seconds=60.0, fragments=3, billed_seconds=60.5)
    cost = harness.estimate_asr_cost(usage, 0.01)

    assert cost.rate_status == "operator-provided"
    assert cost.cost_usd == pytest.approx(60.5 / 60 * 0.01)
    assert cost.cost_per_class_hour_usd == pytest.approx(60.5 * 0.01)
    assert cost.to_dict()["formula"] == (
        "effective minutes per class hour x rate per minute"
    )


@pytest.mark.parametrize("rate", [0.0, -0.1, float("nan"), float("inf")])
def test_cost_rejects_a_non_positive_or_non_finite_rate(rate: float) -> None:
    harness = _load()
    usage = harness.AsrUsage(source_seconds=60.0, fragments=1, billed_seconds=60.0)

    with pytest.raises(ValueError, match="rate_usd_per_minute"):
        harness.estimate_asr_cost(usage, rate)


def test_worker_payload_round_trips_and_rejects_missing_fields() -> None:
    harness = _load()
    measurement = _frame(harness)
    parsed = harness.SlotMeasurement.from_dict(measurement.to_dict())

    assert parsed == measurement
    with pytest.raises(ValueError, match="incomplete"):
        harness.SlotMeasurement.from_dict({"slot": 0})


def test_report_serializes_without_paths_or_provider_claims() -> None:
    harness = _load()
    report = _report(harness)
    payload = report.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ticket"] == "S3.5"
    assert payload["test_basis"] == "C-S4-SG-04"
    assert payload["aggregate"]["tmpfs_peak_sum_of_slot_maxima_bytes"] == 3_600_000
    assert payload["aggregate"]["fits_provisional_tmpfs_upper_bound"] is True
    assert payload["aggregate"]["worker_cleanup_all_removed"] is True
    assert payload["aggregate"]["harness_tmpfs_root_cleaned"] is True
    assert payload["limits"]["approved_200_mib"] is False
    assert payload["limits"]["approved_3h"] is False
    assert payload["limits"]["limits_validated"] is False
    assert payload["asr_cost"]["rate_status"] == "NO VERIFICADO"
    assert payload["asr_usage"]["effective_minutes_per_class_hour"] == pytest.approx(
        60.5
    )
    assert "NO VERIFICADO" in serialized
    assert str(_SCRIPT.parent) not in serialized
    assert "RIFF" not in serialized


def test_table_names_the_unmeasured_rate_and_limits() -> None:
    harness = _load()
    table = _report(harness).render_table()

    assert "NO VERIFICADO" in table
    assert "ASR effective minutes per class hour: 60.500" in table
    assert "worker RSS peak (sum)" in table
    assert "sampled concurrent tmpfs peak" in table
    assert "200 MiB and 3 h are not approved" in table
    assert "all slot dirs removed by their worker=yes" in table


def test_resize_wav_pads_and_truncates_without_audio_persistence() -> None:
    harness = _load()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x01\x00" * 8_000)
    source = buffer.getvalue()

    longer = harness.resize_wav_bytes(source, 2.0)
    with wave.open(BytesIO(longer), "rb") as reader:
        assert reader.getnframes() / reader.getframerate() == pytest.approx(2.0)
    shorter = harness.resize_wav_bytes(source, 0.25)
    with wave.open(BytesIO(shorter), "rb") as reader:
        assert reader.getnframes() == 4_000


def test_find_tmpfs_refuses_a_directory_without_a_tmpfs_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _load()
    monkeypatch.setattr(harness.hardening, "parse_mountinfo", lambda _text: ())

    with pytest.raises(RuntimeError, match="tmpfs"):
        harness.find_tmpfs_base(tmp_path)


def test_cli_rejects_an_out_of_range_slot_count(capsys: pytest.CaptureFixture[str]) -> None:
    harness = _load()

    with pytest.raises(SystemExit):
        harness.main(["--slots", "0"])
    assert "between 1 and" in capsys.readouterr().err


@pytest.mark.load
def test_concurrent_audio_path_measures_and_cleans_up() -> None:
    """Real ffmpeg/ffprobe path with the stub transcriber; no Riva and no key."""
    harness = _load()
    report = harness.run_load(slots=2, clip_seconds=5.0, worker_timeout_s=120.0)

    assert report.slots == 2
    assert len(report.measurements) == 2
    assert report.usage.source_seconds == 5.0
    assert report.usage.fragments >= 1
    assert report.usage.billed_seconds >= 5.0 - 1e-3
    assert report.usage.amplification >= 1.0
    assert report.usage.effective_minutes_per_class_hour >= 60.0 - 1e-6
    assert report.cost.cost_usd == "NO VERIFICADO"
    assert report.tmpfs_peak_sum_bytes > 0
    assert report.tmpfs_peak_sampled_bytes is not None
    assert report.tmpfs_peak_sampled_bytes > 0
    assert report.worker_cleanup_all_removed is True
    assert report.root_cleaned is True
    assert all(measurement.cleanup_removed for measurement in report.measurements)
    assert math.isfinite(report.measurements[0].wall_seconds)
    assert report.measurements[0].wall_seconds > 0
