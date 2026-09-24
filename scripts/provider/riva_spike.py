"""P-S1-SG-07 (S1.A2): one offline Recognize against NVIDIA Riva.

Bearer is built from ``NVIDIA_API_KEY`` in this process. The script reads the
repo-root ``.env`` without overriding variables already set, and never prints
the key, the Authorization metadata, or WAV bytes.

``grpc.max_*_message_length`` is set to 1073741824 as a client channel cap.
That does not mean the endpoint accepts 1 GiB.

Run from the ingest environment (client pin ``nvidia-riva-client==2.27.0``)::

    cd apps/ingest && uv run python ../../scripts/provider/riva_spike.py
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any

CLIENT_MAX_MESSAGE_LENGTH = 1073741824
CLIENT_DEADLINE_S = 60
DEFAULT_SERVER = "grpc.nvcf.nvidia.com:443"
DEFAULT_FUNCTION_ID = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"
NOT_VERIFIED = "NO VERIFICADO"
_MAX_MESSAGE_SCOPE = "client_channel_only"


class SpikeConfigError(Exception):
    """Local configuration problem. The message must not contain a secret."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, _, raw_value = stripped.partition("=")
    key = key.strip()
    if not key or key[0].isdigit() or not key.replace("_", "").isalnum():
        return None
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    elif value.startswith("#"):
        value = ""
    else:
        comment_at = value.find(" #")
        if comment_at != -1:
            value = value[:comment_at].rstrip()
    return key, value


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs. Skip empty values. Do not override existing vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if not value or os.environ.get(key, "").strip():
            continue
        os.environ[key] = value


def require_api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise SpikeConfigError(
            "NVIDIA_API_KEY is required in the environment or the repo-root .env"
        )
    return key


def distribution_version(dist_name: str) -> str:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return NOT_VERIFIED


def recognition_settings(language_code: str) -> dict[str, Any]:
    """Explicit transcription settings. No translate task and no ``multi``."""
    code = language_code.strip()
    if not code or code.casefold() == "multi":
        raise ValueError("language_code must be explicit and must not be multi")
    if "translate" in code.casefold():
        raise ValueError("task:translate is not part of S1.A2")
    return {
        "language_code": code,
        "max_alternatives": 1,
        "enable_automatic_punctuation": False,
        "enable_word_time_offsets": False,
        "custom_configuration": {},
    }


def _pairs_from(call: object, method_name: str) -> list[tuple[str, str]]:
    method = getattr(call, method_name, None)
    if not callable(method):
        return []
    try:
        raw = method()
    except Exception:
        return []
    if not raw:
        return []
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, tuple) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


def _metadata_pairs(call: object) -> list[tuple[str, str]]:
    return _pairs_from(call, "initial_metadata") + _pairs_from(call, "trailing_metadata")


def _metadata_is_sensitive(key: str, value: str, api_key: str) -> bool:
    lowered = key.casefold()
    if "authorization" in lowered or "bearer" in value.casefold():
        return True
    return bool(api_key and api_key in value)


def server_version_from_metadata(call: object, api_key: str) -> str:
    """Return a server version from initial or trailing metadata, or ``NO VERIFICADO``."""
    for key, value in _metadata_pairs(call):
        if _metadata_is_sensitive(key, value, api_key):
            continue
        lowered = key.casefold()
        if lowered in {"server-version", "x-server-version", "version"} or lowered.endswith(
            ("-version", "_version")
        ):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return NOT_VERIFIED


def observed_metadata_keys(call: object, api_key: str) -> list[str]:
    """Key names only. Values stay out of the report."""
    keys: list[str] = []
    for key, value in _metadata_pairs(call):
        if _metadata_is_sensitive(key, value, api_key):
            continue
        if key not in keys:
            keys.append(key)
    return keys


def grpc_status_name(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            status = code()
        except Exception:
            status = None
        name = getattr(status, "name", None)
        if isinstance(name, str) and name:
            return name
    if isinstance(exc, TimeoutError):
        return "DEADLINE_EXCEEDED"
    return "UNKNOWN"


def finish_recognize_call(call: object, api_key: str) -> dict[str, Any]:
    """Read one RPC result. A local gRPC wait timeout is ``DEADLINE_EXCEEDED``.

    ``grpc.FutureTimeoutError`` does not inherit from the builtin ``TimeoutError``
    on grpcio 1.84, so it is handled on its own. The call is cancelled and not retried.
    """
    import grpc

    try:
        response = call.result(timeout=CLIENT_DEADLINE_S)  # type: ignore[attr-defined]
    except grpc.FutureTimeoutError:
        cancel = getattr(call, "cancel", None)
        if callable(cancel):
            cancel()
        return {
            "grpc_code": "DEADLINE_EXCEEDED",
            "hypothesis": "",
            "server_version": NOT_VERIFIED,
            "metadata_keys": [],
        }
    except grpc.RpcError as exc:
        return {
            "grpc_code": grpc_status_name(exc),
            "hypothesis": "",
            "server_version": server_version_from_metadata(exc, api_key),
            "metadata_keys": observed_metadata_keys(exc, api_key),
        }
    except TimeoutError as exc:
        return {
            "grpc_code": grpc_status_name(exc),
            "hypothesis": "",
            "server_version": server_version_from_metadata(exc, api_key),
            "metadata_keys": observed_metadata_keys(exc, api_key),
        }
    return {
        "grpc_code": "OK",
        "hypothesis": hypothesis_text(response),
        "server_version": server_version_from_metadata(call, api_key),
        "metadata_keys": observed_metadata_keys(call, api_key),
    }


def hypothesis_text(response: object) -> str:
    results = getattr(response, "results", None) or []
    parts: list[str] = []
    for result in results:
        alternatives = getattr(result, "alternatives", None) or []
        if alternatives:
            parts.append(str(getattr(alternatives[0], "transcript", "") or ""))
    return "".join(parts)


def expected_text_in_hypothesis(hypothesis: str, expected: str) -> bool:
    needle = " ".join(expected.casefold().split())
    haystack = " ".join(hypothesis.casefold().split())
    return bool(needle) and needle in haystack


def build_redacted_report(
    *,
    client_versions: dict[str, str],
    server_version: str,
    endpoint: str,
    tls: bool,
    function_id: str,
    sample_rate_hz: int,
    wav_num_bytes: int,
    language_code: str,
    expected_text: str,
    grpc_code: str,
    hypothesis: str,
    metadata_keys: list[str] | None = None,
) -> dict[str, Any]:
    """JSON-ready report. Callers must not pass the API key or WAV bytes."""
    return {
        "test_id": "P-S1-SG-07",
        "ticket": "S1.A2",
        "client": client_versions,
        "server_version": server_version,
        "endpoint": endpoint,
        "tls": tls,
        "function_id": function_id,
        "max_message_length_client": CLIENT_MAX_MESSAGE_LENGTH,
        "max_message_length_scope": _MAX_MESSAGE_SCOPE,
        "client_deadline_s": CLIENT_DEADLINE_S,
        "audio": {
            "source": "S1.A1",
            "sample_rate_hz": sample_rate_hz,
            "wav_num_bytes": wav_num_bytes,
            "language_code": language_code,
            "expected_text": expected_text,
        },
        "grpc_code": grpc_code,
        "hypothesis_char_len": len(hypothesis),
        "expected_text_in_hypothesis": expected_text_in_hypothesis(hypothesis, expected_text),
        "metadata_keys": metadata_keys or [],
    }


def report_is_redacted(payload: str, *, api_key: str, wav_bytes: bytes) -> bool:
    if api_key and api_key in payload:
        return False
    lowered = payload.casefold()
    if "bearer " in lowered or "bearer\t" in lowered:
        return False
    if "authorization" in lowered:
        return False
    try:
        header = wav_bytes[:48].decode("ascii")
    except UnicodeDecodeError:
        header = ""
    return not (len(header) >= 16 and header in payload)


def dumps_report(report: dict[str, Any], *, api_key: str, wav_bytes: bytes) -> str:
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if not report_is_redacted(payload, api_key=api_key, wav_bytes=wav_bytes):
        raise SpikeConfigError("refusing to print a report that contains a secret or audio")
    return payload


def _use_ssl() -> bool:
    raw = os.environ.get("RIVA_USE_SSL", "true").strip().casefold()
    return raw in {"1", "true", "yes"}


def _endpoint() -> str:
    return os.environ.get("RIVA_SERVER", DEFAULT_SERVER).strip() or DEFAULT_SERVER


def _function_id() -> str:
    return os.environ.get("RIVA_FUNCTION_ID", DEFAULT_FUNCTION_ID).strip() or DEFAULT_FUNCTION_ID


def _client_versions() -> dict[str, str]:
    return {
        "nvidia-riva-client": distribution_version("nvidia-riva-client"),
        "grpcio": distribution_version("grpcio"),
        "protobuf": distribution_version("protobuf"),
    }


def language_for_request(language_code: str, *, allow_empty: bool = False) -> str:
    """Explicit code only. Empty is sent solely when the caller is probing that case."""
    code = language_code.strip()
    folded = code.casefold()
    if folded == "multi" or "translate" in folded:
        raise SpikeConfigError("refusing multi or task:translate")
    if not code:
        if not allow_empty:
            raise SpikeConfigError("language_code must be explicit")
        return ""
    return str(recognition_settings(code)["language_code"])


def recognize_wav(
    api_key: str,
    *,
    wav_bytes: bytes,
    language_code: str,
    sample_rate_hz: int,
    expected_text: str,
    allow_empty_language: bool = False,
) -> dict[str, Any]:
    """One offline Recognize. The result omits the hypothesis text and the WAV."""
    import riva.client

    if not _use_ssl():
        raise SpikeConfigError("RIVA_USE_SSL must be true")
    sent = language_for_request(language_code, allow_empty=allow_empty_language)
    endpoint = _endpoint()
    function_id = _function_id()
    config = riva.client.RecognitionConfig(
        language_code=sent,
        max_alternatives=1,
        profanity_filter=False,
        enable_automatic_punctuation=False,
        verbatim_transcripts=True,
        enable_word_time_offsets=False,
    )
    if config.custom_configuration:
        raise SpikeConfigError("custom_configuration is not sent")

    auth = riva.client.Auth(
        use_ssl=True,
        uri=endpoint,
        metadata_args=[
            ["function-id", function_id],
            ["authorization", "Bearer " + api_key],
        ],
        options=[
            ("grpc.max_send_message_length", CLIENT_MAX_MESSAGE_LENGTH),
            ("grpc.max_receive_message_length", CLIENT_MAX_MESSAGE_LENGTH),
        ],
    )
    hypothesis = ""
    grpc_code = "UNKNOWN"
    server_version = NOT_VERIFIED
    metadata_keys: list[str] = []
    try:
        asr = riva.client.ASRService(auth)
        call = asr.offline_recognize(wav_bytes, config, future=True)
        outcome = finish_recognize_call(call, api_key)
        grpc_code = str(outcome["grpc_code"])
        hypothesis = str(outcome["hypothesis"])
        server_version = str(outcome["server_version"])
        metadata_keys = list(outcome["metadata_keys"])
    finally:
        close = getattr(auth.channel, "close", None)
        if callable(close):
            close()

    return {
        "client": _client_versions(),
        "server_version": server_version,
        "endpoint": endpoint,
        "tls": True,
        "function_id": function_id,
        "language_sent": sent,
        "sample_rate_hz": sample_rate_hz,
        "wav_num_bytes": len(wav_bytes),
        "expected_text": expected_text,
        "grpc_code": grpc_code,
        "hypothesis_char_len": len(hypothesis),
        "expected_text_in_hypothesis": expected_text_in_hypothesis(hypothesis, expected_text),
        "metadata_keys": metadata_keys,
        "max_message_length_client": CLIENT_MAX_MESSAGE_LENGTH,
        "max_message_length_scope": _MAX_MESSAGE_SCOPE,
    }


def run_recognize(api_key: str) -> tuple[dict[str, Any], bytes]:
    """One offline Recognize for S1.A2. Returns the redacted report and the in-RAM WAV."""
    ingest_root = repo_root() / "apps" / "ingest"
    ingest_path = str(ingest_root)
    if ingest_path not in sys.path:
        sys.path.insert(0, ingest_path)
    from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance

    utterance = generate_synthetic_utterance()
    observed = recognize_wav(
        api_key,
        wav_bytes=utterance.wav_bytes,
        language_code=str(utterance.language_code),
        sample_rate_hz=int(utterance.sample_rate_hz),
        expected_text=str(utterance.expected_text),
    )
    report = build_redacted_report(
        client_versions=observed["client"],
        server_version=str(observed["server_version"]),
        endpoint=str(observed["endpoint"]),
        tls=bool(observed["tls"]),
        function_id=str(observed["function_id"]),
        sample_rate_hz=int(observed["sample_rate_hz"]),
        wav_num_bytes=int(observed["wav_num_bytes"]),
        language_code=str(observed["language_sent"]),
        expected_text=str(observed["expected_text"]),
        grpc_code=str(observed["grpc_code"]),
        hypothesis="x" * int(observed["hypothesis_char_len"]),
        metadata_keys=list(observed["metadata_keys"]),
    )
    report["expected_text_in_hypothesis"] = observed["expected_text_in_hypothesis"]
    report["hypothesis_char_len"] = observed["hypothesis_char_len"]
    return report, utterance.wav_bytes


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_spike.py accepts no arguments; set NVIDIA_API_KEY in the environment",
            file=sys.stderr,
        )
        return 2
    load_dotenv(repo_root() / ".env" if dotenv_path is None else dotenv_path)
    try:
        api_key = require_api_key()
    except SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report, wav_bytes = run_recognize(api_key)
        print(dumps_report(report, api_key=api_key, wav_bytes=wav_bytes))
    except SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Riva call failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0 if report["grpc_code"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
