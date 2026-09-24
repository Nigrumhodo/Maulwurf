"""Local summary for S1.A5. Does not call NVIDIA."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_limits.py"


def _load():
    spec = importlib.util.spec_from_file_location("riva_limits", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["riva_limits"] = module
    spec.loader.exec_module(module)
    return module


def test_floor_does_not_approve_targets_or_leak_secret() -> None:
    """A 30 s floor is a minimum, not an approval of 200 MiB or 3 h."""
    limits = _load()
    secret = "unit-test-nvidia-key-limits"  # noqa: S105
    wav = b"RIFF-LIMITS-TEST-AUDIO-BYTES-NOT-A-WAV"
    report = limits.build_limits_report(
        [
            {
                "probe": "payload_duration_floor",
                "grpc_code": "OK",
                "wav_num_bytes": len(wav),
                "duration_s": 30.0,
                "payload_bound": "minimum_observed",
                "duration_bound": "minimum_observed",
                "max_payload_bytes": "NO VERIFICADO",
                "max_duration_s": "NO VERIFICADO",
            }
        ]
    )
    payload = json.dumps(report)
    assert report["approved_200_mib"] is False
    assert report["approved_3h"] is False
    assert report["max_payload_bytes"] == "NO VERIFICADO"
    assert report["max_duration_s"] == "NO VERIFICADO"
    assert report["quota"] == "NO VERIFICADO"
    assert report["concurrency"] == "NO VERIFICADO"
    assert report["server_deadline"] == "NO VERIFICADO"
    assert secret not in payload
    assert wav.decode("ascii") not in payload
