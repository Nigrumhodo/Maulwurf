"""Local allowlist rule for S1.A3. Does not call NVIDIA."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_languages.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("riva_languages", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["riva_languages"] = module
    spec.loader.exec_module(module)
    return module


def test_allowlist_keeps_only_positive_matches() -> None:
    languages = _load()
    rows = [
        {
            "kind": "positive",
            "language_sent": "es",
            "audio_language": "es",
            "grpc_code": "OK",
            "hypothesis_char_len": 10,
        },
        {
            "kind": "positive",
            "language_sent": "fr",
            "audio_language": "fr",
            "grpc_code": "INVALID_ARGUMENT",
            "hypothesis_char_len": 0,
        },
        {
            "kind": "positive",
            "language_sent": "multi",
            "audio_language": "multi",
            "grpc_code": "OK",
            "hypothesis_char_len": 8,
        },
        {
            "kind": "negative",
            "language_sent": "",
            "audio_language": "es",
            "grpc_code": "OK",
            "hypothesis_char_len": 12,
        },
        {
            "kind": "negative",
            "language_sent": "en",
            "audio_language": "es",
            "grpc_code": "OK",
            "hypothesis_char_len": 12,
        },
    ]
    assert languages.propose_allowlist(rows) == ["es"]


def test_empty_language_is_opt_in_and_multi_is_refused() -> None:
    languages = _load()
    assert languages.riva_spike.language_for_request("", allow_empty=True) == ""
    with pytest.raises(languages.riva_spike.SpikeConfigError):
        languages.riva_spike.language_for_request("multi")
    with pytest.raises(languages.riva_spike.SpikeConfigError):
        languages.riva_spike.language_for_request("task:translate")
