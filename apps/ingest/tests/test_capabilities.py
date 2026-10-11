"""S2.1: el snapshot del spike no publica máximos que D4 no aprobó."""

from __future__ import annotations

from collections.abc import Mapping

from maulwurf_ingest.capabilities import spike_capabilities


def _numbers(value: object) -> list[float]:
    found: list[float] = []
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, int | float):
        found.append(float(value))
        return found
    if isinstance(value, str):
        return found
    if isinstance(value, Mapping):
        for item in value.values():
            found.extend(_numbers(item))
        return found
    if isinstance(value, list | tuple):
        for item in value:
            found.extend(_numbers(item))
    return found


def test_unapproved_limits_stay_unpublished() -> None:
    payload = spike_capabilities()
    formats = payload["accepted_input_formats"]
    assert isinstance(formats, list)

    assert payload["limits_validated"] is False
    assert payload["max_upload_bytes"] is None
    assert payload["max_duration_seconds"] is None
    assert payload["languages"] == ["es", "en", "fr"]
    assert "m4a" not in formats
    assert "wav" in formats
    assert {"mp3", "ogg", "opus", "flac", "webm"} <= set(formats)
    asr = payload["asr"]
    assert isinstance(asr, dict)
    assert asr["timestamp_precision"] is None
    assert 600 not in _numbers(payload)
    assert 10_800 not in _numbers(payload)
