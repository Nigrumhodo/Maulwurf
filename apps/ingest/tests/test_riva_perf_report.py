"""Local summary for S1.A7. Does not call NVIDIA and does not allocate 3 h of PCM."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_perf.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("riva_perf", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["riva_perf"] = module
    spec.loader.exec_module(module)
    return module


def test_pcm_size_and_cost_stay_unapproved() -> None:
    perf = _load()
    secret = "unit-test-nvidia-key-perf"  # noqa: S105
    wav = b"RIFF-PERF-TEST-AUDIO-BYTES-NOT-A-WAV"
    assert perf.PCM_3H_16K_S16_BYTES == 345_600_000
    assert perf.pcm_mebibytes(perf.PCM_3H_16K_S16_BYTES) == 345_600_000 / (1024 * 1024)
    report = perf.build_perf_report(
        [
            {
                "probe": "es_short",
                "language_code": "es",
                "kind": "short",
                "duration_s": 1.5,
                "latency_s": 0.4,
                "grpc_code": "OK",
                "wav_num_bytes": len(wav),
                "sample_rate_hz": 22050,
                "hypothesis_char_len": 3,
            }
        ],
        pcm_rss={"rss_before_bytes": 1, "rss_peak_bytes": 2, "rss_delta_bytes": 1},
        sdk_rss={"imported": False, "rss_before_bytes": 1, "rss_after_bytes": 1},
        observed_audio_s=1.5,
    )
    payload = json.dumps(report)
    assert report["approved_200_mib"] is False
    assert report["approved_3h"] is False
    assert report["cost_per_min"] == "NO VERIFICADO"
    assert report["cost_per_hour"] == "NO VERIFICADO"
    assert report["g8_budget_row"]["cost_per_min"] == "NO VERIFICADO"
    assert report["g8_budget_row"]["cost_per_hour"] == "NO VERIFICADO"
    assert report["pcm_3h_16k_s16"]["uploaded"] is False
    assert report["pcm_3h_16k_s16"]["bytes"] == 345_600_000
    assert report["unmeasured"][1]["language_code"] == "fr"
    assert secret not in payload
    assert wav.decode("ascii") not in payload


def test_vmrss_parser_reads_current_kilobytes() -> None:
    perf = _load()
    assert perf.rss_bytes_from_status("Name:\tpython\nVmRSS:\t   2048 kB\n") == 2048 * 1024


def test_small_pcm_probe_stays_in_memory() -> None:
    """A tiny buffer is enough to exercise RSS. The 3 h allocation is the script's job."""
    perf = _load()
    measured = perf.measure_pcm_rss(64 * 1024)
    if measured is None:
        assert perf.process_rss_bytes() is None
        return
    assert measured["rss_peak_bytes"] >= 0
    assert measured["rss_before_bytes"] >= 0
    assert "rss_delta_bytes" in measured
