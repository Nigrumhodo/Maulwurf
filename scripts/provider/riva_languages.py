"""P-S1-SG-07 (S1.A3): one Recognize per candidate language, plus three negatives.

No ``task:translate`` and no ``multi``. Bearer stays in-process. Audio stays in RAM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_spike  # noqa: E402

PHRASE_EN = "The dog runs in the park every morning."
PHRASE_FR = "Le chien court dans le parc tous les matins."
NOT_VERIFIED = riva_spike.NOT_VERIFIED


def propose_allowlist(rows: list[dict[str, Any]]) -> list[str]:
    """Positive rows only, with a non-empty hypothesis. Never ``multi`` or empty."""
    allowed: list[str] = []
    for row in rows:
        code = str(row.get("language_sent", "")).strip()
        if row.get("kind") != "positive":
            continue
        if row.get("grpc_code") != "OK" or int(row.get("hypothesis_char_len", 0)) <= 0:
            continue
        if not code or code.casefold() == "multi":
            continue
        if code != str(row.get("audio_language", "")).strip():
            continue
        if code not in allowed:
            allowed.append(code)
    return allowed


def _unverified_row(
    case: str,
    *,
    language_sent: str,
    audio_language: str,
    note: str,
) -> dict[str, Any]:
    return {
        "case": case,
        "kind": "positive" if case.startswith("positive_") else "negative",
        "language_sent": language_sent,
        "audio_language": audio_language,
        "grpc_code": NOT_VERIFIED,
        "hypothesis_char_len": 0,
        "expected_text_in_hypothesis": False,
        "note": note,
    }


def _row_from_call(
    case: str,
    *,
    kind: str,
    audio_language: str,
    observed: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case": case,
        "kind": kind,
        "language_sent": observed["language_sent"],
        "audio_language": audio_language,
        "grpc_code": observed["grpc_code"],
        "hypothesis_char_len": observed["hypothesis_char_len"],
        "expected_text_in_hypothesis": observed["expected_text_in_hypothesis"],
        "sample_rate_hz": observed["sample_rate_hz"],
        "wav_num_bytes": observed["wav_num_bytes"],
        "note": "",
    }


def run_language_matrix(api_key: str) -> tuple[dict[str, Any], list[bytes]]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from maulwurf_ingest.audio.synthetic import DEFAULT_PHRASE_ES, generate_synthetic_utterance

    plan: list[tuple[str, str, str, str, bool]] = [
        ("positive_es", "positive", "es", DEFAULT_PHRASE_ES, False),
        ("positive_en", "positive", "en", PHRASE_EN, False),
        ("positive_fr", "positive", "fr", PHRASE_FR, False),
        ("invalid_code", "negative", "es", DEFAULT_PHRASE_ES, False),
        ("absent_language", "negative", "es", DEFAULT_PHRASE_ES, True),
        ("mismatch_es_as_en", "negative", "es", DEFAULT_PHRASE_ES, False),
    ]
    sent_for = {
        "positive_es": "es",
        "positive_en": "en",
        "positive_fr": "fr",
        "invalid_code": "zz",
        "absent_language": "",
        "mismatch_es_as_en": "en",
    }
    rows: list[dict[str, Any]] = []
    wavs: list[bytes] = []
    for case, kind, audio_language, text, allow_empty in plan:
        language_sent = sent_for[case]
        try:
            utterance = generate_synthetic_utterance(text=text, language_code=audio_language)
        except Exception as exc:
            rows.append(
                _unverified_row(
                    case,
                    language_sent=language_sent,
                    audio_language=audio_language,
                    note=type(exc).__name__,
                )
            )
            continue
        wavs.append(utterance.wav_bytes)
        try:
            observed = riva_spike.recognize_wav(
                api_key,
                wav_bytes=utterance.wav_bytes,
                language_code=language_sent,
                sample_rate_hz=int(utterance.sample_rate_hz),
                expected_text=str(utterance.expected_text),
                allow_empty_language=allow_empty,
            )
        except riva_spike.SpikeConfigError as exc:
            rows.append(
                _unverified_row(
                    case,
                    language_sent=language_sent,
                    audio_language=audio_language,
                    note=type(exc).__name__,
                )
            )
            continue
        except Exception as exc:
            rows.append(
                _unverified_row(
                    case,
                    language_sent=language_sent,
                    audio_language=audio_language,
                    note=type(exc).__name__,
                )
            )
            continue
        rows.append(
            _row_from_call(case, kind=kind, audio_language=audio_language, observed=observed)
        )
    report = {
        "test_id": "P-S1-SG-07",
        "ticket": "S1.A3",
        "client": riva_spike._client_versions(),
        "rows": rows,
        "proposed_allowlist": propose_allowlist(rows),
    }
    return report, wavs


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_languages.py accepts no arguments; set NVIDIA_API_KEY in the environment",
            file=sys.stderr,
        )
        return 2
    env_path = riva_spike.repo_root() / ".env" if dotenv_path is None else dotenv_path
    riva_spike.load_dotenv(env_path)
    try:
        api_key = riva_spike.require_api_key()
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        report, wavs = run_language_matrix(api_key)
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        sample = wavs[0] if wavs else b""
        if not riva_spike.report_is_redacted(payload, api_key=api_key, wav_bytes=sample):
            print("refusing to print a report that contains a secret or audio", file=sys.stderr)
            return 2
        print(payload)
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Riva language matrix failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
