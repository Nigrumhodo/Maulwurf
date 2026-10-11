"""U-S2-SG-03: argv de ffmpeg sin shell y con límites de CPU, memoria, salida y tiempo."""

from __future__ import annotations

import os
import subprocess

import pytest

from maulwurf_ingest.audio import ffmpeg as ffmpeg_mod
from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS, PROTOCOL_WHITELIST, bounded_argv


def test_argv_is_a_list_without_network_or_shell() -> None:
    argv = bounded_argv(
        ["-i", "in.wav", "-ac", "1", "-f", "wav"],
        output="out.wav",
        limits=DEFAULT_LIMITS,
    )
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert argv[0] == "ffmpeg"
    assert "-nostdin" in argv
    whitelist = argv[argv.index("-protocol_whitelist") + 1]
    assert whitelist == PROTOCOL_WHITELIST
    assert "http" not in whitelist
    assert "ftp" not in whitelist
    assert argv[argv.index("-timelimit") + 1] == str(DEFAULT_LIMITS.time_limit_s)
    assert argv[argv.index("-fs") + 1] == str(DEFAULT_LIMITS.output_bytes)
    assert DEFAULT_LIMITS.cpu_seconds > 0
    assert DEFAULT_LIMITS.address_space_bytes > 0
    assert DEFAULT_LIMITS.output_bytes > 0
    assert DEFAULT_LIMITS.time_limit_s > 0
    assert DEFAULT_LIMITS.wall_timeout_s > 0
    with pytest.raises(ValueError, match="network_input"):
        bounded_argv(
            ["-i", "https://example.invalid/a.mp3"], output="out.wav", limits=DEFAULT_LIMITS
        )


def test_run_applies_timeout_and_does_not_use_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")  # type: ignore[attr-defined]
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stderr=b"")

    monkeypatch.setattr(ffmpeg_mod.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    argv = bounded_argv(["-i", "in.wav", "-f", "wav"], output="out.wav", limits=DEFAULT_LIMITS)
    assert ffmpeg_mod.run(argv, limits=DEFAULT_LIMITS) == b""

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == DEFAULT_LIMITS.wall_timeout_s
    assert captured["argv"] == ["/usr/bin/ffmpeg", *argv[1:]]
    preexec = kwargs.get("preexec_fn")
    if os.name == "posix":
        assert callable(preexec)
    else:
        assert preexec is None
