"""Thermos G4 regressions: no ffmpeg fallback or Riva upload after a cancel, bounded drain."""
from __future__ import annotations

import asyncio
import signal
import threading
import uuid
import wave
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from maulwurf_ingest import asr_job, pipeline, supervisor
from maulwurf_ingest import lease as leases
from maulwurf_ingest.asr.riva_client import NvidiaRivaTranscriptionService, Transcript
from maulwurf_ingest.audio.ffmpeg import DEFAULT_LIMITS, FfmpegFailed
from maulwurf_ingest.audio.fragmenter import FragmentSpan
from maulwurf_ingest.pipeline import FragmentWords


def _silent_wav(path: Path, seconds: float = 0.2) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(b"\x00\x00" * int(16_000 * seconds))


@pytest.mark.parametrize(
    ("returncode", "signalled"), [(255, True), (-15, True), (-9, True), (1, False), (234, False)],
)
def test_ffmpeg_failed_keeps_message_and_flags_signal_exits(
    returncode: int, signalled: bool,
) -> None:
    exc = FfmpegFailed(returncode)
    assert isinstance(exc, RuntimeError)
    assert str(exc) == "ffmpeg_failed"
    assert exc.signalled is signalled


def _counting_ffmpeg(
    monkeypatch: pytest.MonkeyPatch, returncode: int,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake(argv: list[str], **_kw: object) -> bytes:
        calls.append(list(argv))
        raise FfmpegFailed(returncode)

    monkeypatch.setattr(pipeline, "run_ffmpeg", _fake)
    return calls


def test_extract_does_not_fall_back_after_a_signalled_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting_ffmpeg(monkeypatch, 255)
    dest = tmp_path / "frag-0000.wav"
    with pytest.raises(FfmpegFailed):
        pipeline._extract(
            tmp_path / "original", dest, FragmentSpan(0.0, 1.0),
            limits=DEFAULT_LIMITS, realtime=False,
        )
    assert len(calls) == 1
    assert not dest.exists()


def test_extract_does_not_fall_back_when_the_job_is_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting_ffmpeg(monkeypatch, 1)
    with pytest.raises(FfmpegFailed):
        pipeline._extract(
            tmp_path / "original", tmp_path / "frag-0000.wav", FragmentSpan(0.0, 1.0),
            limits=DEFAULT_LIMITS, realtime=False, should_stop=lambda: True,
        )
    assert len(calls) == 1


def test_extract_still_falls_back_on_an_ordinary_ffmpeg_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting_ffmpeg(monkeypatch, 1)
    with pytest.raises(FfmpegFailed):
        pipeline._extract(
            tmp_path / "original", tmp_path / "frag-0000.wav", FragmentSpan(0.0, 1.0),
            limits=DEFAULT_LIMITS, realtime=False, should_stop=lambda: False,
        )
    assert len(calls) == 2


class _Probe:
    duration_s = 4.0


@pytest.mark.parametrize(
    ("returncode", "stop"), [(255, False), (-15, False), (1, True)],
)
def test_silence_detection_failure_is_not_swallowed_after_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, stop: bool,
) -> None:
    def _detect(*_a: object, **_kw: object) -> list[tuple[float, float]]:
        raise FfmpegFailed(returncode)

    monkeypatch.setattr(pipeline, "probe_file", lambda _path: _Probe())
    monkeypatch.setattr(pipeline, "detect_silences", _detect)
    extracted: list[Path] = []

    def _extract(_source: Path, dest: Path, *_a: object, **_kw: object) -> bool:
        extracted.append(dest)
        return True

    monkeypatch.setattr(pipeline, "_extract", _extract)
    with pytest.raises(FfmpegFailed):
        pipeline.process(
            tmp_path / "original", tmp_path, fragment_seconds=30.0, overlap_s=0.25,
            transcribe=lambda _p: FragmentWords(), should_stop=lambda: stop,
        )
    assert extracted == []


class _Call:
    def result(self, timeout: float) -> Transcript:
        return Transcript(text="hola", words=(), precision="none")

    def cancel(self) -> None:
        return None


class _RecordingTransport:
    def __init__(self) -> None:
        self.starts = 0

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> _Call:
        self.starts += 1
        return _Call()


ProcessBody = Callable[[Path, Callable[[Path], FragmentWords], Callable[[], bool]], None]


def _run_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: ProcessBody,
) -> tuple[_RecordingTransport, dict[str, Any]]:
    """Run the service path with a fake ``process``; the SIGTERM handler is captured."""
    handlers: dict[str, Any] = {}
    original = signal.signal

    def _capture(signum: int, handler: Any) -> Any:
        # `asr_job.signal` is the shared module: asyncio.run installs SIGINT through it too.
        if signum != signal.SIGTERM:
            return original(signum, handler)
        handlers["term"] = handler
        return None

    class _Service(NvidiaRivaTranscriptionService):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            handlers["cancel"] = kwargs.get("cancel")
            super().__init__(*args, **kwargs)

    def _process(
        source: Path, workdir: Path, *, transcribe: Callable[[Path], FragmentWords],
        should_stop: Callable[[], bool] | None = None, **_kw: object,
    ) -> pipeline.PipelineResult:
        assert should_stop is not None
        fragment = workdir / "frag-0000.wav"
        _silent_wav(fragment)
        body(fragment, transcribe, lambda: handlers["term"](signal.SIGTERM, None))
        raise AssertionError("body must raise")

    monkeypatch.setattr(signal, "signal", _capture)
    monkeypatch.setattr(asr_job, "NvidiaRivaTranscriptionService", _Service)
    monkeypatch.setattr(asr_job, "process", _process)
    transport = _RecordingTransport()
    with pytest.raises(asr_job._JobError) as raised:
        asr_job.run(
            tmp_path, realtime=False, fragment_seconds=30, language="es", deadline_s=60.0,
            transport=transport,
        )
    handlers["error"] = raised.value
    return transport, handlers


def test_hear_after_sigterm_never_uploads_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _body(
        fragment: Path, transcribe: Callable[[Path], FragmentWords], term: Callable[[], bool],
    ) -> None:
        term()
        transcribe(fragment)

    transport, seen = _run_job(tmp_path, monkeypatch, _body)
    assert transport.starts == 0
    assert seen["error"].code == "rpc_cancelled"
    assert seen["error"].detail == "CANCELLED"


def test_service_shares_the_job_cancel_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _body(
        fragment: Path, transcribe: Callable[[Path], FragmentWords], term: Callable[[], bool],
    ) -> None:
        transcribe(fragment)
        term()
        transcribe(fragment)

    transport, seen = _run_job(tmp_path, monkeypatch, _body)
    assert transport.starts == 1
    cancel = seen["cancel"]
    assert isinstance(cancel, threading.Event)
    assert cancel.is_set()
    assert seen["error"].code == "rpc_cancelled"


def test_killed_ffmpeg_after_sigterm_reports_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _body(
        _fragment: Path, _transcribe: Callable[[Path], FragmentWords], term: Callable[[], bool],
    ) -> None:
        term()
        raise FfmpegFailed(255)

    transport, seen = _run_job(tmp_path, monkeypatch, _body)
    assert transport.starts == 0
    assert seen["error"].code == "rpc_cancelled"


def _reader(*chunks: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


async def test_drain_tolerates_a_line_over_the_reader_limit() -> None:
    error = b'{"error": "asr_failed", "detail": "no_fragments"}\n'
    stderr = await supervisor._drain_stderr(
        _reader(b"x" * 200_000 + b"\n", error), None, None,
    )
    assert supervisor._child_error_info(stderr) == ("asr_failed", "no_fragments")


async def test_drain_tolerates_an_unterminated_overlong_tail() -> None:
    error = b'{"error": "rpc_cancelled", "detail": "CANCELLED"}\n'
    stderr = await supervisor._drain_stderr(_reader(error, b"y" * 200_000), None, None)
    assert supervisor._child_error_info(stderr) == ("rpc_cancelled", "CANCELLED")


def _lease() -> leases.Lease:
    return leases.Lease(uuid.uuid4(), uuid.uuid4(), "test", 1, timedelta(seconds=30))


async def test_stalled_progress_update_does_not_block_the_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = 0

    async def _stall(*_a: object, **_kw: object) -> None:
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(leases, "update_progress", _stall)
    monkeypatch.setattr(supervisor, "PROGRESS_UPDATE_TIMEOUT_S", 0.05)
    lines = b"".join(
        b'{"progress": {"done": %d, "total": 3}}\n' % done for done in range(4)
    ) + b'{"error": "asr_failed"}\n'
    stderr = await asyncio.wait_for(
        supervisor._drain_stderr(_reader(lines), object(), _lease()),
        timeout=5.0,
    )
    assert started == 4
    assert supervisor._child_error_info(stderr) == ("asr_failed", None)


class _FakeProc:
    def __init__(self, stdout: asyncio.StreamReader, stderr: asyncio.StreamReader) -> None:
        self.stdout = stdout
        self.stderr = stderr

    async def wait(self) -> int:
        return 0


async def test_collect_child_cancels_the_sibling_when_the_drain_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _broken(*_a: object, **_kw: object) -> bytes:
        raise RuntimeError("drain")

    monkeypatch.setattr(supervisor, "_drain_stderr", _broken)
    never_ending = asyncio.StreamReader()
    proc = _FakeProc(never_ending, asyncio.StreamReader())
    with pytest.raises(RuntimeError, match="drain"):
        await asyncio.wait_for(
            supervisor._collect_child(proc, None, None),  # type: ignore[arg-type]
            timeout=5.0,
        )
    current = asyncio.current_task()
    assert [task for task in asyncio.all_tasks() if task is not current] == []


def test_lease_constants_have_one_home() -> None:
    from maulwurf_ingest import receptions, reconciler

    assert supervisor.LEASE_TTL_S == receptions.LEASE_TTL_S == leases.LEASE_TTL_S == 30.0
    assert supervisor.HEARTBEAT_INTERVAL_S == leases.HEARTBEAT_INTERVAL_S == 10.0
    assert reconciler.DEFAULT_LEASE_TTL == leases.DEFAULT_LEASE_TTL == timedelta(seconds=30)
    config = supervisor.SupervisorConfig(instance="t")
    assert config.lease_ttl == leases.DEFAULT_LEASE_TTL
