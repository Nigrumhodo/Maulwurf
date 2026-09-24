"""Local guard for the S1.A2 report. Does not call NVIDIA (not marker ``provider``)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "provider" / "riva_spike.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("riva_spike", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["riva_spike"] = module
    spec.loader.exec_module(module)
    return module


def test_future_timeout_error_is_not_builtin_timeout() -> None:
    """grpcio 1.84 FutureTimeoutError does not inherit TimeoutError."""
    import grpc

    error = grpc.FutureTimeoutError()
    assert not isinstance(error, TimeoutError)


def test_future_timeout_records_deadline_and_cancels() -> None:
    """A local gRPC wait timeout becomes DEADLINE_EXCEEDED and cancels the call."""
    import grpc

    class _Call:
        def __init__(self) -> None:
            self.cancelled = False

        def result(self, timeout: float | None = None) -> object:
            raise grpc.FutureTimeoutError()

        def cancel(self) -> None:
            self.cancelled = True

    spike = _load()
    call = _Call()
    outcome = spike.finish_recognize_call(call, "unit-test-nvidia-key-timeout")  # noqa: S106
    assert outcome["grpc_code"] == "DEADLINE_EXCEEDED"
    assert outcome["hypothesis"] == ""
    assert call.cancelled is True


def test_report_omits_secret_bearer_and_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    """F0.1 redaction: the printed report has no key, Bearer, or audio bytes."""
    secret = "unit-test-nvidia-key-9f3c2a"  # noqa: S105
    monkeypatch.setenv("NVIDIA_API_KEY", secret)
    wav = b"RIFF-UNIT-TEST-AUDIO-BYTES-NOT-A-WAV"
    spike = _load()
    report = spike.build_redacted_report(
        client_versions={
            "nvidia-riva-client": "2.27.0",
            "grpcio": "0",
            "protobuf": "0",
        },
        server_version="NO VERIFICADO",
        endpoint="grpc.nvcf.nvidia.com:443",
        tls=True,
        function_id="b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
        sample_rate_hz=22050,
        wav_num_bytes=len(wav),
        language_code="es",
        expected_text="El perro corre en el parque todas las mañanas.",
        grpc_code="OK",
        hypothesis="el perro corre",
    )
    payload = spike.dumps_report(report, api_key=os.environ["NVIDIA_API_KEY"], wav_bytes=wav)
    assert secret not in payload
    assert "Bearer" not in payload
    assert "authorization" not in payload.casefold()
    assert wav.decode("ascii") not in payload
    assert report["max_message_length_client"] == 1073741824
    assert report["max_message_length_scope"] == "client_channel_only"
    assert report["server_version"] == "NO VERIFICADO"
    assert report["audio"]["wav_num_bytes"] == len(wav)
    assert isinstance(report["audio"]["wav_num_bytes"], int)


def test_blank_key_comment_is_empty() -> None:
    spike = _load()
    assert spike._parse_env_line("NVIDIA_API_KEY=   # necesario") == ("NVIDIA_API_KEY", "")


def test_missing_key_names_variable_and_skips_the_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("NVIDIA_API_KEY=   # necesario\n", encoding="utf-8")
    spike = _load()
    code = spike.main(["riva_spike.py"], dotenv_path=env_file)
    captured = capsys.readouterr()
    assert code == 2
    assert "NVIDIA_API_KEY" in captured.err
    assert "Bearer" not in captured.err
    assert captured.out == ""


def test_argv_secret_is_rejected_without_echo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "unit-test-nvidia-key-argv"  # noqa: S105
    spike = _load()
    code = spike.main(["riva_spike.py", secret], dotenv_path=tmp_path / "absent.env")
    captured = capsys.readouterr()
    assert code == 2
    assert secret not in captured.out
    assert secret not in captured.err


def test_dotenv_replaces_blank_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "   ")
    env_file = tmp_path / ".env"
    env_file.write_text("NVIDIA_API_KEY=from-file\n", encoding="utf-8")
    spike = _load()
    spike.load_dotenv(env_file)
    assert os.environ["NVIDIA_API_KEY"] == "from-file"


def test_dotenv_does_not_override_existing_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "already-exported")
    previous_server = os.environ.pop("RIVA_SERVER", None)
    env_file = tmp_path / ".env"
    env_file.write_text("NVIDIA_API_KEY=from-file\nRIVA_SERVER=from-file:443\n", encoding="utf-8")
    spike = _load()
    try:
        spike.load_dotenv(env_file)
        assert os.environ["NVIDIA_API_KEY"] == "already-exported"
        assert os.environ["RIVA_SERVER"] == "from-file:443"
    finally:
        if previous_server is None:
            os.environ.pop("RIVA_SERVER", None)
        else:
            os.environ["RIVA_SERVER"] = previous_server


def test_server_version_comes_from_initial_metadata() -> None:
    class _Call:
        def initial_metadata(self) -> tuple[tuple[str, str], ...]:
            return (("x-server-version", "observed-1"),)

        def trailing_metadata(self) -> tuple[tuple[str, str], ...]:
            return (("authorization", "Bearer hidden"),)

    spike = _load()
    assert spike.server_version_from_metadata(_Call(), "hidden") == "observed-1"
    assert spike.observed_metadata_keys(_Call(), "hidden") == ["x-server-version"]


def test_language_must_be_explicit() -> None:
    spike = _load()
    with pytest.raises(ValueError, match="multi"):
        spike.recognition_settings("multi")
    settings = spike.recognition_settings("es")
    assert settings["language_code"] == "es"
    assert settings["enable_word_time_offsets"] is False
    assert settings["custom_configuration"] == {}
