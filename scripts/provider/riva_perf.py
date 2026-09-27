"""P-S1-SG-08 (S1.A7): latency, SDK RSS, and PCM expansion.

Three serial Recognize calls, no retries. A 3 h 16 kHz mono s16 buffer is
allocated in RAM to measure RSS and then released. It is not uploaded.
Cost per minute and per hour stays ``NO VERIFICADO``.
"""

from __future__ import annotations

import ctypes
import gc
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any

_PROVIDER = Path(__file__).resolve().parent
if str(_PROVIDER) not in sys.path:
    sys.path.insert(0, str(_PROVIDER))

import riva_limits  # noqa: E402
import riva_spike  # noqa: E402

PCM_3H_16K_S16_BYTES = 3 * 3600 * 16000 * 2
PCM_PAGE_BYTES = 4096
EN_PHRASE = "The dog runs in the park every morning."
NOT_VERIFIED = riva_spike.NOT_VERIFIED
_UNMEASURED = (
    {"language_code": "en", "kind": "floor_30s", "latency_s": NOT_VERIFIED},
    {"language_code": "fr", "kind": "short", "latency_s": NOT_VERIFIED},
    {"language_code": "fr", "kind": "floor_30s", "latency_s": NOT_VERIFIED},
)


def pcm_mebibytes(num_bytes: int) -> float:
    return num_bytes / (1024 * 1024)


def rss_bytes_from_status(text: str) -> int:
    """Parse current ``VmRSS`` kilobytes from ``/proc/self/status``."""
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) < 2:
                break
            return int(parts[1]) * 1024
    raise OSError("VmRSS missing")


def process_rss_bytes() -> int | None:
    """Current resident set size, or ``None`` when the OS call fails.

    Linux uses ``VmRSS``, not ``ru_maxrss``. The max-RSS counter never drops,
    so it cannot separate the SDK from a later PCM allocation.
    """
    try:
        if sys.platform == "win32":
            return _windows_rss_bytes()
        if sys.platform == "darwin":
            import resource

            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return rss_bytes_from_status(Path("/proc/self/status").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None


def _windows_rss_bytes() -> int:
    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    ok = psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize)


def measure_pcm_rss(num_bytes: int) -> dict[str, int] | None:
    """Touch a RAM buffer and report RSS before and at the peak. Nothing is written."""
    if num_bytes < 0:
        raise ValueError("num_bytes must be non-negative")
    gc.collect()
    before = process_rss_bytes()
    if before is None:
        return None
    buffer = bytearray(num_bytes)
    if num_bytes:
        for index in range(0, num_bytes, PCM_PAGE_BYTES):
            buffer[index] = 0
        buffer[-1] = 0
    peak = process_rss_bytes()
    del buffer
    if peak is None:
        return None
    return {
        "rss_before_bytes": before,
        "rss_peak_bytes": peak,
        "rss_delta_bytes": peak - before,
    }


def measure_sdk_rss() -> dict[str, Any]:
    """RSS around importing ``riva.client``. A missing package is not a crash."""
    gc.collect()
    before = process_rss_bytes()
    imported = False
    try:
        importlib.import_module("riva.client")
        imported = True
    except ImportError:
        imported = False
    after = process_rss_bytes()
    return {
        "imported": imported,
        "rss_before_bytes": before if before is not None else NOT_VERIFIED,
        "rss_after_bytes": after if after is not None else NOT_VERIFIED,
    }


def build_perf_report(
    rows: list[dict[str, Any]],
    *,
    pcm_rss: dict[str, int] | None,
    sdk_rss: dict[str, Any],
    observed_audio_s: float,
) -> dict[str, Any]:
    """G8 budget row. 200 MiB, 3 h, and any price stay unapproved."""
    return {
        "test_id": "P-S1-SG-08",
        "ticket": "S1.A7",
        "approved_200_mib": False,
        "approved_3h": False,
        "cost_per_min": NOT_VERIFIED,
        "cost_per_hour": NOT_VERIFIED,
        "pcm_3h_16k_s16": {
            "bytes": PCM_3H_16K_S16_BYTES,
            "mebibytes": round(pcm_mebibytes(PCM_3H_16K_S16_BYTES), 3),
            "rate_hz": 16000,
            "channels": 1,
            "sample_width_bytes": 2,
            "duration_h": 3,
            "uploaded": False,
            "rss": pcm_rss if pcm_rss is not None else NOT_VERIFIED,
        },
        "sdk_rss": sdk_rss,
        "rows": rows,
        "unmeasured": [dict(item) for item in _UNMEASURED],
        "g8_budget_row": {
            "observed_audio_s": round(observed_audio_s, 3),
            "cost_per_min": NOT_VERIFIED,
            "cost_per_hour": NOT_VERIFIED,
            "pcm_3h_bytes": PCM_3H_16K_S16_BYTES,
        },
    }


def _latency_row(
    probe: str,
    *,
    language_code: str,
    kind: str,
    observed: dict[str, Any],
    duration_s: float,
    latency_s: float,
) -> dict[str, Any]:
    return {
        "probe": probe,
        "language_code": language_code,
        "kind": kind,
        "duration_s": round(duration_s, 3),
        "latency_s": round(latency_s, 3),
        "grpc_code": observed["grpc_code"],
        "wav_num_bytes": observed["wav_num_bytes"],
        "sample_rate_hz": observed["sample_rate_hz"],
        "hypothesis_char_len": observed["hypothesis_char_len"],
    }


def _recognize_timed(
    api_key: str,
    *,
    wav_bytes: bytes,
    language_code: str,
    sample_rate_hz: int,
    expected_text: str,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    observed = riva_spike.recognize_wav(
        api_key,
        wav_bytes=wav_bytes,
        language_code=language_code,
        sample_rate_hz=sample_rate_hz,
        expected_text=expected_text,
    )
    return observed, time.perf_counter() - started


def run_perf_probes(api_key: str) -> tuple[dict[str, Any], list[bytes]]:
    ingest = str(riva_spike.repo_root() / "apps" / "ingest")
    if ingest not in sys.path:
        sys.path.insert(0, ingest)
    from riva_formats import resample_wav_pcm_s16_mono

    from maulwurf_ingest.audio.synthetic import generate_synthetic_utterance

    sdk_rss = measure_sdk_rss()
    pcm_rss: dict[str, int] | None
    try:
        pcm_rss = measure_pcm_rss(PCM_3H_16K_S16_BYTES)
    except MemoryError:
        pcm_rss = None

    rows: list[dict[str, Any]] = []
    wavs: list[bytes] = []
    observed_audio_s = 0.0

    spanish = generate_synthetic_utterance()
    short = spanish.wav_bytes
    wavs.append(short)
    short_s = riva_limits.wav_duration_s(short)
    observed_audio_s += short_s
    short_obs, short_latency = _recognize_timed(
        api_key,
        wav_bytes=short,
        language_code=str(spanish.language_code),
        sample_rate_hz=int(spanish.sample_rate_hz),
        expected_text=str(spanish.expected_text),
    )
    rows.append(
        _latency_row(
            "es_short",
            language_code="es",
            kind="short",
            observed=short_obs,
            duration_s=short_s,
            latency_s=short_latency,
        )
    )

    floor = riva_limits.pad_wav_with_silence(
        resample_wav_pcm_s16_mono(short, riva_limits.FLOOR_HZ),
        riva_limits.FLOOR_SECONDS,
    )
    wavs.append(floor)
    floor_s = riva_limits.wav_duration_s(floor)
    observed_audio_s += floor_s
    floor_obs, floor_latency = _recognize_timed(
        api_key,
        wav_bytes=floor,
        language_code="es",
        sample_rate_hz=riva_limits.FLOOR_HZ,
        expected_text=str(spanish.expected_text),
    )
    rows.append(
        _latency_row(
            "es_floor_30s",
            language_code="es",
            kind="floor_30s",
            observed=floor_obs,
            duration_s=floor_s,
            latency_s=floor_latency,
        )
    )

    english = generate_synthetic_utterance(text=EN_PHRASE, language_code="en")
    en_wav = english.wav_bytes
    wavs.append(en_wav)
    en_s = riva_limits.wav_duration_s(en_wav)
    observed_audio_s += en_s
    en_obs, en_latency = _recognize_timed(
        api_key,
        wav_bytes=en_wav,
        language_code="en",
        sample_rate_hz=int(english.sample_rate_hz),
        expected_text=EN_PHRASE,
    )
    rows.append(
        _latency_row(
            "en_short",
            language_code="en",
            kind="short",
            observed=en_obs,
            duration_s=en_s,
            latency_s=en_latency,
        )
    )
    return (
        build_perf_report(
            rows,
            pcm_rss=pcm_rss,
            sdk_rss=sdk_rss,
            observed_audio_s=observed_audio_s,
        ),
        wavs,
    )


def main(argv: list[str] | None = None, dotenv_path: Path | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print(
            "riva_perf.py accepts no arguments; set NVIDIA_API_KEY in the environment",
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
        report, wavs = run_perf_probes(api_key)
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if any(
            not riva_spike.report_is_redacted(payload, api_key=api_key, wav_bytes=wav)
            for wav in wavs
        ):
            print("refusing to print a report that contains a secret or audio", file=sys.stderr)
            return 2
        print(payload)
    except riva_spike.SpikeConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Riva perf probe failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
