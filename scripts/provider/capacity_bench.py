"""S1.B2: short in-RAM ffmpeg conversion. Prints a slot table. Deletes audio.

Does not approve 200 MiB or 3 h. Does not upload anything and does not keep a WAV.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

_INGEST = Path(__file__).resolve().parents[2] / "apps" / "ingest"
if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))

from maulwurf_ingest.capacity import (  # noqa: E402
    APPROVED_3H,
    APPROVED_200_MIB,
    FRAGMENT_1S_16K_S16_BYTES,
    PCM_3H_16K_S16_BYTES,
    PROVISIONAL_MEM_LIMIT_BYTES,
    PROVISIONAL_SLOTS,
    PROVISIONAL_TMPFS_BYTES,
    PROVISIONAL_UPLOAD_BYTES,
    fits_limit,
    instance_bytes,
    slot_bytes,
)

_SECONDS = 2
_RATE = 16000


def process_rss_bytes() -> int | None:
    """Current resident set size, or None when the OS call fails."""
    try:
        if sys.platform == "win32":
            return None
        if sys.platform == "darwin":
            return None
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _silent_wav() -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(_RATE)
        writer.writeframes(b"\x00\x00" * _RATE * _SECONDS)
    return buffer.getvalue()


def _dir_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def measure_short_conversion() -> dict[str, Any]:
    """Convert a 2 s silent WAV on a temp dir and delete it. Peak sizes only."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required")
    wav = _silent_wav()
    before = process_rss_bytes()
    peak_rss = before
    peak_dir = 0
    shm = "/dev/shm" if Path("/dev/shm").is_dir() else None  # noqa: S108
    root = Path(tempfile.mkdtemp(prefix="mw-b2-", dir=shm))
    try:
        original = root / "original"
        original.write_bytes(wav)
        peak_dir = max(peak_dir, _dir_bytes(root))
        completed = subprocess.run(  # noqa: S603 — argv fija, sin shell
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(original), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(root / "converted.wav"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        rss_now = process_rss_bytes()
        if rss_now is not None and (peak_rss is None or rss_now > peak_rss):
            peak_rss = rss_now
        peak_dir = max(peak_dir, _dir_bytes(root))
        if completed.returncode != 0:
            raise RuntimeError("ffmpeg_failed")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        del wav
    return {
        "rss_before_bytes": before if before is not None else "NO VERIFICADO",
        "rss_peak_bytes": peak_rss if peak_rss is not None else "NO VERIFICADO",
        "tmp_peak_bytes": peak_dir,
        "audio_removed": not root.exists(),
    }


def build_report(measured: dict[str, Any]) -> dict[str, Any]:
    one = slot_bytes(
        original_bytes=PROVISIONAL_UPLOAD_BYTES,
        active_fragments=1,
        fragment_bytes=FRAGMENT_1S_16K_S16_BYTES,
        ffmpeg_buffer_bytes=0,
        sdk_buffer_bytes=0,
        margin_bytes=0,
    )
    return {
        "ticket": "S1.B2",
        "approved_200_mib": APPROVED_200_MIB,
        "approved_3h": APPROVED_3H,
        "pcm_3h_bytes_not_in_slot": PCM_3H_16K_S16_BYTES,
        "fragment_1s_bytes": FRAGMENT_1S_16K_S16_BYTES,
        "provisional_one_slot_bytes": one,
        "provisional_instance_bytes": instance_bytes(one, PROVISIONAL_SLOTS),
        "provisional_tmpfs_bytes": PROVISIONAL_TMPFS_BYTES,
        "provisional_mem_limit_bytes": PROVISIONAL_MEM_LIMIT_BYTES,
        "one_slot_fits_tmpfs": fits_limit(one, PROVISIONAL_TMPFS_BYTES),
        "two_slots_fit_tmpfs": fits_limit(
            instance_bytes(one, PROVISIONAL_SLOTS), PROVISIONAL_TMPFS_BYTES
        ),
        "short_conversion": measured,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    if len(args) > 1:
        print("capacity_bench.py accepts no arguments", file=sys.stderr)
        return 2
    try:
        report = build_report(measure_short_conversion())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if "RIFF" in payload:
        print("refusing to print a report that contains audio", file=sys.stderr)
        return 2
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
