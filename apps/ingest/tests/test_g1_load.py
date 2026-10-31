"""S4.3 harness tests: G1 booleans under concurrent load with injected failures.

These tests are the unit support for L-S4-SG-01. They cover the pure aggregation, the
measurement contract, the RAM reservation math and the injection labelling without running
ffmpeg. The real audio path runs only under the ``load`` marker: it uses a synthetic tone
generated in RAM, a private tmpfs that is deleted and verified, no Riva channel and no
provider key.
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import tempfile
import wave
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "load" / "g1_load.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("g1_load", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["g1_load"] = module
    spec.loader.exec_module(module)
    return module


def _measurement(module: ModuleType, **overrides: Any) -> Any:
    base: dict[str, Any] = {
        "attempt": 0,
        "wave": 0,
        "slot": 0,
        "scenario": "success",
        "outcome": "success",
        "injection": "none",
        "injection_confirmed": True,
        "upload_bytes": 192_044,
        "received_bytes": 192_044,
        "fragments": 1,
        "wall_seconds": 1.5,
        "tmpfs_peak_bytes": 192_166,
        "tmpfs_after_bytes": 0,
        "slot_ram_peak_bytes": 100_000_000,
        "worker_rss_peak_bytes": 40_000_000,
        "asr_group_rss_peak_bytes": 60_000_000,
        "cleanup_verified": True,
        "workdir_absent": True,
        "live_processes": 0,
        "open_descriptors": 0,
        "unreadable_processes": 0,
        "tmp_is_tmpfs": True,
        "cleanup_seconds": 0.1,
        "slot_dir_orphans": 0,
    }
    base.update(overrides)
    return module.AttemptMeasurement(**base)


def _rows(module: ModuleType) -> tuple[Any, ...]:
    return (
        _measurement(module, attempt=0, wave=0, slot=0, scenario="success"),
        _measurement(
            module,
            attempt=1,
            wave=0,
            slot=1,
            scenario="kill",
            outcome="killed",
            injection="sigkill_asr_group",
        ),
        _measurement(
            module,
            attempt=2,
            wave=1,
            slot=0,
            scenario="timeout",
            outcome="timeout",
            injection="asr_timeout",
            fragments=0,
        ),
        _measurement(
            module,
            attempt=3,
            wave=1,
            slot=1,
            scenario="full",
            outcome="enospc",
            injection="tmpfs_enospc_real",
            error="ENOSPC",
            error_number=28,
            received_bytes=0,
            fragments=0,
        ),
    )


def _report(module: ModuleType, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "attempts": 4,
        "slots": 2,
        "clip_seconds": 6.0,
        "fragment_seconds": 30,
        "timeout_s": 1.0,
        "worker_timeout_s": 120.0,
        "full_injection": "real",
        "full_tmpfs_kib": 96,
        "worker_isolation": "pid_namespace",
        "measurements": _rows(module),
        "environment": {
            "python": "3.12",
            "system": "Linux test",
            "ffmpeg": "test",
            "riva_call": "none",
        },
        "scoped_absence": {"clear": True, "scan_complete": True, "audio_files": 0,
                           "magic_matches": 0, "log_audio_markers": 0},
        "host_absence": None,
        "harness_root_empty": True,
        "harness_root_cleaned": True,
        "per_slot_reservation_bytes": module.per_slot_reservation_bytes(),
    }
    values.update(overrides)
    return module.LoadReport(**values)


def test_ticket_constants_and_reservation_math() -> None:
    harness = _load()

    assert harness.TICKET == "S4.3"
    assert harness.TEST_BASIS == "L-S4-SG-01"
    assert harness.SCENARIOS == ("success", "kill", "timeout", "full")
    assert harness.per_slot_reservation_bytes() == 512 * 1024 * 1024
    assert harness.per_slot_reservation_bytes(slots=4) == 256 * 1024 * 1024
    with pytest.raises(ValueError, match="slots"):
        harness.per_slot_reservation_bytes(slots=0)


def test_synthetic_tone_is_mono_pcm_s16_with_the_exact_duration() -> None:
    harness = _load()
    payload = harness.synthesize_tone(2.5)

    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    with wave.open(BytesIO(payload), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 16_000
        assert reader.getnframes() / reader.getframerate() == pytest.approx(2.5)
    assert len(payload) == 44 + int(2.5 * 16_000) * 2


def test_synthetic_tone_rejects_invalid_parameters() -> None:
    harness = _load()

    for seconds in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="seconds"):
            harness.synthesize_tone(seconds)
    with pytest.raises(ValueError, match="rate"):
        harness.synthesize_tone(1.0, rate=1000)


def test_measurement_round_trips_and_rejects_incomplete_payloads() -> None:
    harness = _load()
    measurement = _measurement(harness)
    parsed = harness.AttemptMeasurement.from_dict(measurement.to_dict())

    assert parsed == measurement
    with pytest.raises(ValueError, match="scenario"):
        harness.AttemptMeasurement.from_dict({"attempt": 0})
    incomplete = measurement.to_dict()
    incomplete.pop("cleanup_seconds")
    with pytest.raises(ValueError, match="incomplete"):
        harness.AttemptMeasurement.from_dict(incomplete)
    with pytest.raises(ValueError, match="unknown scenario"):
        harness.AttemptMeasurement.from_dict({**measurement.to_dict(), "scenario": "other"})
    with pytest.raises(ValueError, match="unknown outcome"):
        harness.AttemptMeasurement.from_dict({**measurement.to_dict(), "outcome": "other"})


def test_filesystem_clean_requires_every_absence_signal() -> None:
    harness = _load()
    assert _measurement(harness).filesystem_clean is True

    for override in (
        {"workdir_absent": False},
        {"tmpfs_after_bytes": 1},
        {"live_processes": 1},
        {"open_descriptors": 1},
        {"slot_dir_orphans": 1},
    ):
        assert _measurement(harness, **override).filesystem_clean is False


def test_ram_reservation_is_inclusive_and_rejects_one_byte_over() -> None:
    harness = _load()
    budget = harness.per_slot_reservation_bytes()

    assert _measurement(harness, slot_ram_peak_bytes=budget).within_reservation(budget) is True
    assert (
        _measurement(harness, slot_ram_peak_bytes=budget + 1).within_reservation(budget) is False
    )


def test_full_report_passes_every_g1_check_with_a_real_tmpfs() -> None:
    harness = _load()
    report = _report(harness)

    assert report.waves == 2
    assert report.concurrent is True
    assert report.zero_audio_persistence is True
    assert report.zero_orphan_temporaries is True
    assert report.ram_within_per_slot_reservation is True
    assert report.injections == {"kill": True, "timeout": True, "tmpfs_full": True}
    assert report.injections_covered is True
    assert report.tmpfs_full_evidence == "real"
    assert report.checks_pass is True
    assert report.g1_verified is True
    assert report.g1_verified_with_injected_fallback is True


def test_injected_full_tmpfs_is_labelled_and_never_claims_a_real_one() -> None:
    harness = _load()
    rows = list(_rows(harness))
    rows[-1] = _measurement(
        harness,
        attempt=3,
        wave=1,
        slot=1,
        scenario="full",
        outcome="enospc",
        injection="tmpfs_enospc_injected",
        error="ENOSPC",
        error_number=28,
    )
    report = _report(harness, measurements=tuple(rows), full_injection="write_error")

    assert report.injections_covered is True
    assert report.tmpfs_full_evidence == "injected"
    assert report.checks_pass is True
    assert report.g1_verified is False
    assert report.g1_verified_with_injected_fallback is True
    payload = report.to_dict()
    assert payload["injections"]["tmpfs_full_evidence"] == "injected"
    assert payload["checks"]["g1_verified"] is False


def test_missing_full_tmpfs_injection_does_not_count_as_coverage() -> None:
    harness = _load()
    rows = tuple(row for row in _rows(harness) if row.scenario != "full")
    report = _report(harness, attempts=3, measurements=rows)

    assert report.injections["tmpfs_full"] is False
    assert report.injections_covered is False
    assert report.tmpfs_full_evidence == "missing"
    assert report.g1_verified_with_injected_fallback is False


def test_a_single_orphan_or_persisted_byte_fails_the_checks() -> None:
    harness = _load()
    rows = list(_rows(harness))
    rows[1] = _measurement(
        harness,
        attempt=1,
        wave=0,
        slot=1,
        scenario="kill",
        outcome="killed",
        injection="sigkill_asr_group",
        tmpfs_after_bytes=4096,
    )
    report = _report(harness, measurements=tuple(rows))
    assert report.zero_audio_persistence is False
    assert report.zero_orphan_temporaries is False

    rows = list(_rows(harness))
    rows[2] = _measurement(
        harness,
        attempt=2,
        wave=1,
        slot=0,
        scenario="timeout",
        outcome="timeout",
        injection="asr_timeout",
        slot_dir_orphans=1,
    )
    report = _report(harness, measurements=tuple(rows))
    assert report.zero_audio_persistence is True
    assert report.zero_orphan_temporaries is False


def test_an_over_budget_slot_fails_the_ram_check() -> None:
    harness = _load()
    budget = harness.per_slot_reservation_bytes()
    rows = list(_rows(harness))
    rows[0] = _measurement(harness, slot_ram_peak_bytes=budget + 1)
    report = _report(harness, measurements=tuple(rows))

    assert report.ram_within_per_slot_reservation is False
    assert report.checks_pass is False
    assert report.g1_verified is False


def test_an_unclear_scoped_sweep_fails_audio_persistence() -> None:
    harness = _load()
    report = _report(
        harness,
        scoped_absence={"clear": False, "scan_complete": True, "audio_files": 1,
                        "magic_matches": 1, "log_audio_markers": 0},
    )

    assert report.scoped_clear is False
    assert report.zero_audio_persistence is False
    assert report.checks_pass is False


def test_report_json_has_no_paths_and_no_audio_magic() -> None:
    harness = _load()
    report = _report(harness)
    payload = report.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ticket"] == "S4.3"
    assert payload["test_basis"] == "L-S4-SG-01"
    assert payload["aggregate"]["per_slot_reservation_bytes"] == 512 * 1024 * 1024
    assert payload["limits"]["approved_200_mib"] is False
    assert payload["limits"]["limits_validated"] is False
    assert str(_SCRIPT.parent) not in serialized
    assert str(Path(tempfile.gettempdir())) not in serialized
    assert "RIFF" not in serialized
    assert "WAVE" not in serialized


def test_table_names_every_boolean_and_the_tmpfs_fidelity() -> None:
    harness = _load()
    table = _report(harness).render_table()

    assert "zero audio persistence=yes" in table
    assert "zero orphan temporaries=yes" in table
    assert "RAM within per-slot reservation=yes" in table
    assert "kill=yes" in table and "timeout=yes" in table
    assert "tmpfs full=real" in table
    assert "G1 re-verified (strict, real full tmpfs): yes" in table
    assert "200 MiB and 3 h are not approved" in table


def test_find_tmpfs_refuses_a_directory_without_a_tmpfs_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _load()
    monkeypatch.setattr(harness.hardening, "parse_mountinfo", lambda _text: ())

    with pytest.raises(RuntimeError, match="tmpfs"):
        harness.find_tmpfs_base(tmp_path)


def test_mount_reports_unavailable_without_the_binary(tmp_path: Path) -> None:
    harness = _load()
    point, reason = harness.mount_bounded_tmpfs(
        tmp_path, 64, mount_binary="/nonexistent/mount-binary"
    )

    assert point is None
    assert reason == "mount_unavailable"
    assert not any(tmp_path.iterdir())


def test_worker_command_prefixes_unshare_only_when_isolated(tmp_path: Path) -> None:
    harness = _load()
    options = harness.SlotOptions(
        attempt=0,
        wave=0,
        slot=0,
        scenario="success",
        tmp_dir=tmp_path,
        clip_seconds=6.0,
        fragment_seconds=30,
        timeout_s=1.0,
    )

    shared = harness._worker_command(options, "shared")
    isolated = harness._worker_command(options, "pid_namespace", unshare_binary="/usr/bin/unshare")

    assert shared[0] == sys.executable
    assert isolated[0] == "/usr/bin/unshare"
    assert isolated[1:7] == list(harness._ISOLATION_ARGV)
    assert str(tmp_path) in isolated
    assert str(tmp_path) in shared
    assert isolated[-1] == shared[-1]


def test_run_load_rejects_configurations_that_cannot_hit_enospc() -> None:
    harness = _load()

    with pytest.raises(ValueError, match="bounded tmpfs"):
        harness.run_load(clip_seconds=2.0, full_tmpfs_kib=4096, host_sweep=False)
    with pytest.raises(ValueError, match="fail_after_bytes"):
        harness.run_load(
            clip_seconds=2.0, full_tmpfs_kib=16, fail_after_bytes=10**6, host_sweep=False
        )
    with pytest.raises(ValueError, match="attempts"):
        harness.run_load(attempts=0)
    with pytest.raises(ValueError, match="slots"):
        harness.run_load(slots=99)


def test_last_json_line_ignores_warnings() -> None:
    harness = _load()

    assert harness._last_json_line('warning\n{"ok": true}\n') == {"ok": True}
    with pytest.raises(ValueError, match="no JSON"):
        harness._last_json_line("not json")


@pytest.mark.load
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no disponible")
def test_concurrent_injected_failures_leave_no_audio() -> None:
    """Real reception/probe/ASR-child/cleanup path with synthetic RAM audio only."""
    harness = _load()
    report = harness.run_load(
        attempts=4,
        slots=2,
        clip_seconds=2.0,
        timeout_s=0.4,
        full_tmpfs_kib=32,
        host_sweep=False,
        worker_isolation="auto",
    )

    assert report.attempts == 4
    assert [row.scenario for row in report.measurements] == list(harness.SCENARIOS)
    for row in report.measurements:
        assert row.filesystem_clean is True
        if row.unreadable_processes == 0:
            assert row.cleanup_verified is True
    assert report.zero_audio_persistence is True
    assert report.zero_orphan_temporaries is True
    assert report.ram_within_per_slot_reservation is True
    assert report.injections_covered is True
    assert report.tmpfs_full_evidence in {"real", "injected"}
    assert math.isfinite(report.measurements[0].wall_seconds)
