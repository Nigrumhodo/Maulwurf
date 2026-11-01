"""S4.5 harness tests: the reproducible cost table per class hour (C-S4-SG-04).

The unit cases cover the three-term model (ASR minutes, ffmpeg CPU, RAM GiB-hours), the
``NO VERIFICADO`` rule for missing rates and the serialization of the measurement. The
``load`` case runs the real ffprobe/ffmpeg audio path with a stub transcriber on a small
synthetic clip in a private tmpfs; no Riva channel, key or provider call is involved.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "load"


def _load_module(name: str) -> ModuleType:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness() -> tuple[ModuleType, ModuleType]:
    asr = _load_module("asr_cost")
    cost = _load_module("cost_table")
    return asr, cost


def _frame(
    asr: ModuleType,
    *,
    slot: int = 0,
    cpu_user: float | None = 0.6,
    cpu_system: float | None = 0.4,
    wall: float = 3.0,
    rss: int = 36_000_000,
    tmpfs: int = 14_000_000,
) -> Any:
    return asr.SlotMeasurement(
        slot=slot,
        fragments=11,
        billed_seconds=302.5,
        wall_seconds=wall,
        tmpfs_peak_bytes=tmpfs,
        tmpfs_after_bytes=tmpfs - 1_000_000,
        rss_before_bytes=rss - 1_000_000,
        rss_peak_bytes=rss,
        rss_after_bytes=rss - 100_000,
        ffmpeg_child_maxrss_bytes=50_000_000,
        cleanup_removed=True,
        cpu_user_seconds=cpu_user,
        cpu_system_seconds=cpu_system,
    )


def _report(
    asr: ModuleType,
    *,
    cpu: bool = True,
    rate_usd_per_minute: float | None = None,
    slots: int = 1,
) -> Any:
    usage = asr.AsrUsage(source_seconds=300.0, fragments=11, billed_seconds=302.5)
    frames = tuple(
        _frame(
            asr,
            slot=slot,
            cpu_user=0.6 if cpu else None,
            cpu_system=0.4 if cpu else None,
        )
        for slot in range(slots)
    )
    return asr.LoadReport(
        slots=slots,
        clip_seconds=300.0,
        fragment_seconds=30,
        overlap_seconds=0.25,
        worker_timeout_s=300.0,
        measurements=frames,
        tmpfs_peak_sampled_bytes=13_500_000,
        root_cleaned=True,
        usage=usage,
        cost=asr.estimate_asr_cost(usage, rate_usd_per_minute),
        environment={"python": "3.12", "ffmpeg": "test", "riva_call": "none"},
    )


def test_a_missing_rate_never_becomes_a_number(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(_report(asr))

    assert [term.rate_status for term in table.terms] == ["NO VERIFICADO"] * 3
    assert [term.cost_per_class_hour_usd for term in table.terms] == [
        "NO VERIFICADO"
    ] * 3
    assert table.total_cost_per_class_hour_usd == "NO VERIFICADO"
    assert table.all_rates_provided is False


def test_measured_quantities_normalize_to_one_class_hour(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(_report(asr))

    # 300 s clip -> x12 to one class hour; 1.0 CPU s -> 12 CPU s -> 12/3600 vCPU-hour.
    assert table.asr.quantity_per_class_hour == pytest.approx(60.5)
    assert table.cpu.quantity_per_class_hour == pytest.approx(12 / 3600)
    assert table.ffmpeg_cpu_seconds == pytest.approx(12.0)
    assert table.cpu_ratio == pytest.approx(1.0 / 3.0)
    assert table.wall_seconds_per_class_hour == pytest.approx(36.0)
    assert table.peak_working_set_bytes == 50_000_000
    expected_gib_hours = (50_000_000 / (1024**3)) * (36 / 3600)
    assert table.ram.quantity_per_class_hour == pytest.approx(expected_gib_hours)


def test_operator_rates_fill_each_cost_and_the_total(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(
        _report(asr, rate_usd_per_minute=0.01),
        cpu_rate_usd_per_vcpu_hour=0.5,
        ram_rate_usd_per_gib_hour=0.02,
    )

    expected_cpu = (12 / 3600) * 0.5
    expected_ram = (50_000_000 / (1024**3)) * (36 / 3600) * 0.02
    assert table.asr.cost_per_class_hour_usd == pytest.approx(60.5 * 0.01)
    assert table.cpu.cost_per_class_hour_usd == pytest.approx(expected_cpu)
    assert table.ram.cost_per_class_hour_usd == pytest.approx(expected_ram)
    assert table.total_cost_per_class_hour_usd == pytest.approx(
        60.5 * 0.01 + expected_cpu + expected_ram
    )
    assert table.all_rates_provided is True


def test_a_partial_rate_leaves_the_total_unverified(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(
        _report(asr), cpu_rate_usd_per_vcpu_hour=0.5
    )

    assert table.cpu.cost_per_class_hour_usd == pytest.approx((12 / 3600) * 0.5)
    assert table.asr.cost_per_class_hour_usd == "NO VERIFICADO"
    assert table.total_cost_per_class_hour_usd == "NO VERIFICADO"


def test_a_capture_without_cpu_keeps_the_cpu_term_unverified(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(
        _report(asr, cpu=False), cpu_rate_usd_per_vcpu_hour=0.5
    )
    payload = table.to_dict()

    assert table.cpu.quantity_per_class_hour is None
    assert table.cpu.rate_status == "operator-provided"
    assert table.cpu.cost_per_class_hour_usd == "NO VERIFICADO"
    assert table.total_cost_per_class_hour_usd == "NO VERIFICADO"
    assert payload["measured"]["ffmpeg_cpu_seconds_per_class_hour"] is None
    assert payload["cost"]["terms"][1]["cost_per_class_hour_usd"] == "NO VERIFICADO"


@pytest.mark.parametrize("rate", [0.0, -0.1, float("nan"), float("inf")])
def test_cost_terms_reject_non_positive_or_non_finite_rates(
    harness: tuple[ModuleType, ModuleType], rate: float
) -> None:
    _, cost = harness

    with pytest.raises(ValueError, match="rate"):
        cost.CostTerm(
            key="cpu",
            label="ffmpeg CPU",
            quantity_per_class_hour=0.01,
            unit="vCPU-hour",
            rate_usd_per_unit=rate,
        )


def test_cost_terms_reject_a_negative_quantity(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    _, cost = harness

    with pytest.raises(ValueError, match="quantity"):
        cost.CostTerm(
            key="ram",
            label="RAM",
            quantity_per_class_hour=-1.0,
            unit="GiB-hour",
            rate_usd_per_unit=None,
        )


def test_cpu_measurement_round_trips_and_old_captures_stay_unverified(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, _ = harness
    measurement = _frame(asr)
    parsed = asr.SlotMeasurement.from_dict(measurement.to_dict())

    assert parsed == measurement
    legacy = measurement.to_dict()
    legacy.pop("cpu_user_seconds")
    legacy.pop("cpu_system_seconds")
    legacy.pop("cpu_total_seconds")
    old = asr.SlotMeasurement.from_dict(legacy)
    assert old.cpu_user_seconds is None
    assert old.cpu_total_seconds is None

    half = measurement.to_dict()
    half.pop("cpu_system_seconds")
    with pytest.raises(ValueError, match="both present"):
        asr.SlotMeasurement.from_dict(half)


def test_cpu_aggregates_sum_both_slots_and_do_not_invent_missing_seconds(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, _ = harness
    report = _report(asr, slots=2)

    assert report.ffmpeg_cpu_user_seconds == pytest.approx(1.2)
    assert report.ffmpeg_cpu_system_seconds == pytest.approx(0.8)
    assert report.ffmpeg_cpu_total_seconds == pytest.approx(2.0)
    assert report.ffmpeg_cpu_seconds_per_class_hour == pytest.approx(12.0)
    assert report.to_dict()["aggregate"]["ffmpeg_cpu_total_seconds"] == pytest.approx(2.0)

    mixed = _report(asr, slots=2)
    mixed = replace(
        mixed,
        measurements=(
            mixed.measurements[0],
            replace(mixed.measurements[1], cpu_user_seconds=None),
        ),
    )
    assert mixed.ffmpeg_cpu_total_seconds is None
    assert mixed.ffmpeg_cpu_seconds_per_class_hour is None
    assert mixed.cpu_ratio is None


def test_report_serializes_without_paths_or_audio(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    table = cost.build_cost_table(_report(asr))
    serialized = json.dumps(table.to_dict(), ensure_ascii=False)

    assert table.to_dict()["ticket"] == "S4.5"
    assert table.to_dict()["test_basis"] == "C-S4-SG-04"
    assert str(_SCRIPTS) not in serialized
    assert "RIFF" not in serialized
    assert "NO VERIFICADO" in serialized


def test_table_names_the_three_terms_and_the_unverified_rates(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    asr, cost = harness
    rendered = cost.build_cost_table(_report(asr)).render_table()

    assert "S4.5 real cost per class hour" in rendered
    assert "ASR" in rendered
    assert "ffmpeg CPU" in rendered
    assert "RAM" in rendered
    assert "NO VERIFICADO" in rendered
    assert "200 MiB and 3 h are not approved" in rendered
    assert "Cleanup verified: yes" in rendered


def test_the_cli_rejects_a_bad_rate(
    harness: tuple[ModuleType, ModuleType], capsys: pytest.CaptureFixture[str]
) -> None:
    _, cost = harness

    with pytest.raises(SystemExit):
        cost.main(["--cpu-rate-usd-per-vcpu-hour", "0"])
    assert "positive finite" in capsys.readouterr().err


@pytest.mark.load
def test_the_real_audio_path_produces_a_reproducible_cost_table(
    harness: tuple[ModuleType, ModuleType],
) -> None:
    """Real ffprobe/ffmpeg path with a stub transcriber; no Riva and no key."""
    asr, cost = harness
    if asr.process_hwm_bytes() is None:
        pytest.skip(
            "peak RSS unavailable: neither /proc/self/status VmHWM nor "
            "getrusage(RUSAGE_SELF).ru_maxrss reports a peak"
        )
    table = cost.run_cost_table(slots=1, clip_seconds=5.0, worker_timeout_s=120.0)

    assert table.slots == 1
    assert table.asr_effective_minutes >= 60.0 - 1e-6
    assert table.ffmpeg_cpu_seconds is not None
    assert table.ffmpeg_cpu_seconds > 0
    assert table.cpu_ratio is not None
    assert table.cpu_ratio > 0
    assert table.peak_working_set_bytes is not None
    assert table.peak_working_set_bytes > 0
    assert table.ram_gib_hours is not None
    assert table.ram_gib_hours > 0
    assert table.clean is True
    assert table.total_cost_per_class_hour_usd == "NO VERIFICADO"
