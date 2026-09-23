"""In-RAM synthetic speech (S1.A1): WAV PCM s16 mono via espeak-ng stdout.

Audio never touches the filesystem. A seed only dithers PCM by 1 LSB so the
same text + seed + espeak-ng version is byte-identical; it does not change
the phonetic sequence.
"""

from __future__ import annotations

import array
import io
import random
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass

DEFAULT_PHRASE_ES = "El perro corre en el parque todas las mañanas."

_ESPEAK_CANDIDATES = ("espeak-ng", "espeak")
_ESPEAK_TIMEOUT_S = 30
# Soft RAM bound: fail before dither instead of keeping unbounded PCM in memory.
_MAX_OUTPUT_SECONDS = 300
# Pinned so the same binary version yields a stable waveform before dither.
_ESPEAK_WORDS_PER_MIN = 120
_ESPEAK_PITCH = 50
_ESPEAK_AMPLITUDE = 100


@dataclass(frozen=True)
class SyntheticUtterance:
    wav_bytes: bytes
    expected_text: str
    language_code: str
    sample_rate_hz: int
    seed: int


def generate_synthetic_utterance(
    *,
    text: str = DEFAULT_PHRASE_ES,
    seed: int = 0,
    language_code: str = "es",
) -> SyntheticUtterance:
    """Synthesize `text` to an in-memory PCM WAV. Never writes audio to disk."""
    spoken = text.strip()
    if not spoken:
        raise ValueError("synthetic utterance text must be non-empty")

    raw_wav = _espeak_wav_stdout(spoken, language_code=language_code)
    sample_rate_hz, nframes = _wav_pcm_meta(raw_wav)
    seconds = nframes / sample_rate_hz if sample_rate_hz else 0.0
    if seconds > _MAX_OUTPUT_SECONDS:
        raise RuntimeError(
            f"synthetic utterance too long ({seconds:.0f}s > {_MAX_OUTPUT_SECONDS}s); "
            "acota el texto de entrada"
        )
    dithered = _apply_seeded_dither(raw_wav, seed)
    return SyntheticUtterance(
        wav_bytes=dithered,
        expected_text=spoken,
        language_code=language_code,
        sample_rate_hz=sample_rate_hz,
        seed=seed,
    )


def _espeak_binary() -> str:
    for name in _ESPEAK_CANDIDATES:
        path = shutil.which(name)
        if path is not None:
            return path
    raise RuntimeError(
        "espeak-ng is required for synthetic audio (S1.A1). Install the system "
        "package (espeak-ng on Linux/macOS; espeak-ng on Windows) and ensure it "
        "is on PATH."
    )


def _voice_for_language(language_code: str) -> str:
    code = language_code.strip().lower()
    if not code:
        raise ValueError("language_code must be non-empty")
    # espeak-ng voice names match BCP-47 primary tags for es/en; pass through.
    return code.split("-", maxsplit=1)[0]


def _espeak_wav_stdout(text: str, *, language_code: str) -> bytes:
    binary = _espeak_binary()
    voice = _voice_for_language(language_code)
    args = [
        binary,
        "--stdout",
        "-v",
        voice,
        "-s",
        str(_ESPEAK_WORDS_PER_MIN),
        "-p",
        str(_ESPEAK_PITCH),
        "-a",
        str(_ESPEAK_AMPLITUDE),
        "--",
        text,
    ]
    try:
        if sys.platform == "win32":
            completed = subprocess.run(  # noqa: S603 — argv list, no shell
                args,
                capture_output=True,
                check=False,
                shell=False,
                timeout=_ESPEAK_TIMEOUT_S,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            completed = subprocess.run(  # noqa: S603 — argv list, no shell
                args,
                capture_output=True,
                check=False,
                shell=False,
                timeout=_ESPEAK_TIMEOUT_S,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("espeak-ng timed out while synthesizing speech") from exc

    if completed.returncode != 0:
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        detail = err or f"exit {completed.returncode}"
        raise RuntimeError(f"espeak-ng failed: {detail}")

    wav_bytes = completed.stdout
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise RuntimeError("espeak-ng did not write a WAV RIFF blob to stdout")
    return _fix_streaming_wav_sizes(wav_bytes)


def _fix_streaming_wav_sizes(wav_bytes: bytes) -> bytes:
    """Rewrite RIFF/data sizes. espeak-ng --stdout emits a placeholder length."""
    buf = bytearray(wav_bytes)
    struct.pack_into("<I", buf, 4, len(buf) - 8)
    offset = 12
    while offset + 8 <= len(buf):
        chunk_id = bytes(buf[offset : offset + 4])
        declared = struct.unpack_from("<I", buf, offset + 4)[0]
        payload_start = offset + 8
        remaining = len(buf) - payload_start
        if chunk_id == b"data" or declared > remaining:
            struct.pack_into("<I", buf, offset + 4, remaining)
            break
        offset = payload_start + declared
        if declared % 2:
            offset += 1
    return bytes(buf)


def _apply_seeded_dither(wav_bytes: bytes, seed: int) -> bytes:
    src = io.BytesIO(wav_bytes)
    with wave.open(src, "rb") as reader:
        nchannels = reader.getnchannels()
        sampwidth = reader.getsampwidth()
        framerate = reader.getframerate()
        nframes = reader.getnframes()
        frames = reader.readframes(nframes)

    if nchannels != 1:
        raise RuntimeError(f"expected mono WAV from espeak-ng, got {nchannels} channels")
    if sampwidth != 2:
        raise RuntimeError(f"expected 16-bit PCM from espeak-ng, got sampwidth={sampwidth}")
    if nframes <= 0:
        raise RuntimeError("espeak-ng produced an empty WAV")

    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()

    rng = random.Random(seed)  # noqa: S311 — dither only; not cryptographic
    for i in range(len(samples)):
        delta = rng.choice((-1, 0, 1))
        samples[i] = max(-32768, min(32767, samples[i] + delta))

    if sys.byteorder != "little":
        samples.byteswap()

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(framerate)
        writer.writeframes(samples.tobytes())
    return out.getvalue()


def _wav_pcm_meta(wav_bytes: bytes) -> tuple[int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
        return reader.getframerate(), reader.getnframes()
