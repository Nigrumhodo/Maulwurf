"""U-S2-SG-05: reintentos gRPC clasificados, sin canal real."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from maulwurf_ingest.asr.riva_client import (
    AsrRejected,
    AsrReupload,
    GrpcRivaTransport,
    NvidiaRivaTranscriptionService,
    Transcript,
    TransportError,
    _transcript_from,
    riva_transport_from_env,
)

_WAV = b"RIFF"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Call:
    def __init__(self, outcome: Transcript | TransportError) -> None:
        self._outcome = outcome
        self.cancelled = False

    def result(self, timeout: float) -> Transcript:
        if isinstance(self._outcome, TransportError):
            raise self._outcome
        return self._outcome

    def cancel(self) -> None:
        self.cancelled = True


class _Script:
    def __init__(self, outcomes: list[Transcript | TransportError]) -> None:
        self.outcomes = outcomes
        self.deadlines: list[float] = []
        self.calls: list[_Call] = []

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> _Call:
        assert wav == _WAV
        assert language_code == "es"
        self.deadlines.append(deadline_s)
        outcome = self.outcomes.pop(0)
        call = _Call(outcome)
        self.calls.append(call)
        return call


def _service(
    script: _Script,
    *,
    deadline_s: float = 30.0,
    clock: _Clock | None = None,
    sleeps: list[float] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[NvidiaRivaTranscriptionService, _Clock]:
    used = _Clock() if clock is None else clock
    recorded = [] if sleeps is None else sleeps

    def _sleep(delay: float) -> None:
        recorded.append(delay)
        used.now += delay

    service = NvidiaRivaTranscriptionService(
        script,
        sleeper=_sleep,
        clock=used,
        jitter=lambda: 0.0,
        cancel=cancel,
    )
    return service, used


def test_transient_retries_at_most_three_times_with_backoff() -> None:
    script = _Script([TransportError("UNAVAILABLE")] * 3)
    sleeps: list[float] = []
    service, _clock = _service(script, sleeps=sleeps)
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=30.0)
    assert len(script.calls) == 3
    assert sleeps == [0.2, 0.4]


def test_the_third_transient_attempt_can_succeed() -> None:
    ok = Transcript("listo", (), "none")
    script = _Script(
        [TransportError("DEADLINE_EXCEEDED"), TransportError("RESOURCE_EXHAUSTED"), ok]
    )
    service, _clock = _service(script)
    assert service.transcribe(_WAV, language_code="es", deadline_s=30.0) is ok
    assert len(script.calls) == 3
    assert script.deadlines[1] < script.deadlines[0]


@pytest.mark.parametrize("code", ["UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT"])
def test_auth_language_and_format_do_not_retry(code: str) -> None:
    script = _Script([TransportError(code), TransportError(code)])
    service, _clock = _service(script)
    with pytest.raises(AsrRejected, match=code):
        service.transcribe(_WAV, language_code="es", deadline_s=30.0)
    assert len(script.calls) == 1


@pytest.mark.parametrize("language", ["", "  ", "multi", "MULTI", "task:translate"])
def test_rejected_language_does_not_call(language: str) -> None:
    script = _Script([])
    service, _clock = _service(script)
    with pytest.raises(AsrRejected, match="invalid_language"):
        service.transcribe(_WAV, language_code=language, deadline_s=30.0)
    assert script.calls == []


def test_backoff_that_does_not_fit_the_deadline_stops() -> None:
    script = _Script([TransportError("UNAVAILABLE"), TransportError("UNAVAILABLE")])
    service, _clock = _service(script, deadline_s=0.1)
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=0.1)
    assert len(script.calls) == 1


def test_a_spent_deadline_does_not_call() -> None:
    script = _Script([Transcript("x", (), "none")])
    service, _clock = _service(script)
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=0.0)
    assert script.calls == []


def test_cancel_aborts_the_future_and_does_not_retry() -> None:
    script = _Script([Transcript("x", (), "none")])
    cancel = threading.Event()

    def _start(wav: bytes, *, language_code: str, deadline_s: float) -> _Call:
        cancel.set()
        return script.start(wav, language_code=language_code, deadline_s=deadline_s)

    service = NvidiaRivaTranscriptionService(
        _Start(_start),
        sleeper=lambda _delay: None,
        clock=_Clock(),
        jitter=lambda: 0.0,
        cancel=cancel,
    )
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=30.0)
    assert len(script.calls) == 1
    assert script.calls[0].cancelled is True


def test_a_cancelled_result_does_not_retry() -> None:
    script = _Script([TransportError("CANCELLED"), TransportError("UNAVAILABLE")])
    service, _clock = _service(script)
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=30.0)
    assert len(script.calls) == 1


async def test_blocking_recognize_stays_off_the_event_loop() -> None:
    seen: dict[str, str] = {}

    class _Blocking:
        def result(self, timeout: float) -> Transcript:
            seen["thread"] = threading.current_thread().name
            time.sleep(0.2)
            return Transcript("ok", (), "none")

        def cancel(self) -> None:
            return None

    class _Slow:
        def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> _Blocking:
            return _Blocking()

    service = NvidiaRivaTranscriptionService(_Slow())
    ticks: list[int] = []

    async def _tick() -> None:
        for _ in range(20):
            ticks.append(1)
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(_tick())
    result = await service.transcribe_async(_WAV, language_code="es", deadline_s=5.0)
    await ticker
    assert result.text == "ok"
    assert seen["thread"] != threading.main_thread().name
    assert len(ticks) >= 2


@pytest.mark.parametrize("code", ["INTERNAL", "UNKNOWN"])
def test_unknown_service_codes_ask_for_reupload(code: str) -> None:
    script = _Script([TransportError(code), TransportError("UNAVAILABLE")])
    service, _clock = _service(script)
    with pytest.raises(AsrReupload, match="requires_reupload"):
        service.transcribe(_WAV, language_code="es", deadline_s=30.0)
    assert len(script.calls) == 1


def test_transcript_separates_result_blocks() -> None:
    class _Word:
        word = "hoy"
        start_time = 0
        end_time = 100

    class _Alt:
        def __init__(self, text: str, words: list[object]) -> None:
            self.transcript = text
            self.words = words

    class _Result:
        def __init__(self, text: str, words: list[object]) -> None:
            self.alternatives = [_Alt(text, words)]

    class _Response:
        results = [_Result("hoy", [_Word()]), _Result("clase", [])]

    transcript = _transcript_from(_Response())
    assert transcript.text == "hoy clase"
    assert transcript.precision == "word"
    assert transcript.words[0].text == "hoy"


async def test_cancelling_the_task_cancels_the_inflight_call() -> None:
    started = threading.Event()
    released = threading.Event()

    class _Blocking:
        def result(self, timeout: float) -> Transcript:
            started.set()
            released.wait(timeout=2)
            raise TransportError("CANCELLED")

        def cancel(self) -> None:
            released.set()

    class _Slow:
        def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> _Blocking:
            return _Blocking()

    service = NvidiaRivaTranscriptionService(_Slow(), jitter=lambda: 0.0)
    task = asyncio.create_task(service.transcribe_async(_WAV, language_code="es", deadline_s=30.0))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()


def test_transport_reuses_one_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    import riva.client as riva_client

    opened: list[str] = []

    class _Channel:
        def close(self) -> None:
            opened.append("closed")

    class _Auth:
        def __init__(self, **_kwargs: object) -> None:
            opened.append("auth")
            self.channel = _Channel()

    class _Call:
        def cancel(self) -> None:
            return None

    class _Asr:
        def __init__(self, _auth: object) -> None:
            opened.append("asr")

        def offline_recognize(self, wav: bytes, config: object, future: bool = True) -> _Call:
            opened.append("recognize")
            return _Call()

    class _Config:
        def __init__(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(riva_client, "Auth", _Auth)
    monkeypatch.setattr(riva_client, "ASRService", _Asr)
    monkeypatch.setattr(riva_client, "RecognitionConfig", _Config)
    transport = GrpcRivaTransport(
        api_key="not-a-real-key", server="grpc.example:443", function_id="fn"
    )
    transport.start(b"a", language_code="es", deadline_s=1.0)
    transport.start(b"b", language_code="es", deadline_s=1.0)
    assert opened.count("auth") == 1
    assert opened.count("asr") == 1
    assert opened.count("recognize") == 2
    transport.close()
    assert opened.count("closed") == 1
    transport.close()
    assert opened.count("closed") == 1


def test_factory_refuses_a_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="riva_unconfigured"):
        riva_transport_from_env()


class _Start:
    def __init__(self, start: object) -> None:
        self._start = start

    def start(self, wav: bytes, *, language_code: str, deadline_s: float) -> _Call:
        started = self._start
        assert callable(started)
        call = started(wav, language_code=language_code, deadline_s=deadline_s)
        assert isinstance(call, _Call)
        return call
