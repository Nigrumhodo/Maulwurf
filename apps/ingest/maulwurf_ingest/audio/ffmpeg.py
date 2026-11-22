"""Invocación de ffmpeg acotada (S2.2).

El argv es siempre una lista: no hay shell ni interpolación. La red queda fuera
del protocol whitelist. CPU y memoria van por rlimit en POSIX; el tamaño de
salida y el tiempo van en el propio argv (`-fs`, `-timelimit`) y en el timeout
del proceso. stderr no se registra.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from maulwurf_ingest.config import settings

# Local tmpfs inputs only. `crypto` is not required for wav/mp3/m4a/ogg/webm and
# would let a nested protocol wrap a file the whitelist already allows.
PROTOCOL_WHITELIST = "file,pipe"
CANDIDATE_SAMPLE_RATE_HZ = 16_000

# Techo de salida por invocación: el tmpfs provisional del slot (F0.2), no un
# máximo de Riva. El timeout de pared conserva el de S1.B3.
_OUTPUT_BYTES = 256 * 1024 * 1024
_CPU_SECONDS = 300
# Matches the ingest container mem_limit (1 GiB). A 4 GiB RLIMIT_AS let one
# ffmpeg mapping push the cgroup OOM killer into killing the whole service.
_ADDRESS_SPACE_BYTES = settings.ffmpeg_address_space_bytes
_TIME_LIMIT_S = 300
_WALL_TIMEOUT_S = 600.0
_FFMPEG_THREADS = 2


@dataclass(frozen=True)
class FfmpegLimits:
    cpu_seconds: int
    address_space_bytes: int
    output_bytes: int
    time_limit_s: int
    wall_timeout_s: float


DEFAULT_LIMITS = FfmpegLimits(
    cpu_seconds=_CPU_SECONDS,
    address_space_bytes=_ADDRESS_SPACE_BYTES,
    output_bytes=_OUTPUT_BYTES,
    time_limit_s=_TIME_LIMIT_S,
    wall_timeout_s=_WALL_TIMEOUT_S,
)


def bounded_argv(
    operation: Sequence[str],
    *,
    output: str,
    limits: FfmpegLimits,
    loglevel: str = "error",
) -> list[str]:
    """Arma el argv. `operation` no incluye el binario ni el fichero de salida."""
    if loglevel not in {"error", "info"}:
        raise ValueError("loglevel")
    tokens = (*operation, output)
    if any("\x00" in token for token in tokens):
        raise ValueError("nul_in_argv")
    for token in tokens:
        lowered = token.lower()
        if lowered.startswith(("http://", "https://", "ftp://")):
            raise ValueError("network_input")
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        loglevel,
        "-threads",
        str(_FFMPEG_THREADS),
        "-protocol_whitelist",
        PROTOCOL_WHITELIST,
        "-timelimit",
        str(limits.time_limit_s),
        "-y",
        *operation,
        "-fs",
        str(limits.output_bytes),
        output,
    ]


def limited_command(argv: Sequence[str], limits: FfmpegLimits) -> list[str]:
    """Run `argv` under RLIMIT_CPU/AS/FSIZE without `preexec_fn`.

    `preexec_fn` runs between fork and exec in the child while other threads
    exist (the event loop, gRPC, the lease heartbeat) and can deadlock on a
    libc lock. `prlimit` applies the limits and execs. When it is not installed,
    a fresh interpreter applies `setrlimit` and execs — still no preexec hook.
    """
    if os.name != "posix":
        return list(argv)
    prlimit = shutil.which("prlimit")
    if prlimit is not None:
        return [
            prlimit,
            f"--cpu={limits.cpu_seconds}",
            f"--as={limits.address_space_bytes}",
            f"--fsize={limits.output_bytes}",
            "--",
            *argv,
        ]
    return [
        sys.executable,
        "-m",
        "maulwurf_ingest.audio.limits_exec",
        "--cpu",
        str(limits.cpu_seconds),
        "--as",
        str(limits.address_space_bytes),
        "--fsize",
        str(limits.output_bytes),
        "--",
        *argv,
    ]


def _child_env() -> dict[str, str]:
    # Two glibc arenas keep ffmpeg's virtual size inside the 1 GiB cap.
    env = os.environ.copy()
    env["MALLOC_ARENA_MAX"] = "2"
    return env


def run(argv: Sequence[str], *, limits: FfmpegLimits, capture_stderr: bool = False) -> bytes:
    """Ejecuta `argv` sin shell. Con `capture_stderr`, devuelve stderr y no lo loguea."""
    resolved = _resolve(argv)
    command = limited_command(resolved, limits)
    stderr_mode: int | None = subprocess.PIPE if capture_stderr else subprocess.DEVNULL
    env = _child_env()
    if os.name == "posix":
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
            env=env,
        )
    elif sys.platform == "win32":
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=env,
        )
    else:
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            command,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
            env=env,
        )
    if completed.returncode != 0:
        raise RuntimeError("ffmpeg_failed")
    if not capture_stderr:
        return b""
    captured = completed.stderr
    return captured if isinstance(captured, bytes) else b""


def _resolve(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise ValueError("empty_argv")
    resolved = list(argv)
    if resolved[0] != "ffmpeg":
        return resolved
    found = shutil.which("ffmpeg")
    if found is None:
        raise RuntimeError("ffmpeg_unavailable")
    resolved[0] = found
    return resolved


# s16 mono al sample rate candidato. El margen cubre la cabecera RIFF.
_PCM_BYTES_PER_SECOND = CANDIDATE_SAMPLE_RATE_HZ * 2
_HEADER_BYTES = 4096


def ensure_pcm_fits(duration_s: float, output_bytes: int) -> None:
    """`-fs` trunca con código 0. Si el PCM no cabe, hay que fallar antes de convertir."""
    if not math.isfinite(duration_s) or duration_s <= 0 or output_bytes < 1:
        raise RuntimeError("normalization_duration_unknown")
    expected = math.ceil(duration_s * _PCM_BYTES_PER_SECOND) + _HEADER_BYTES
    if expected > output_bytes:
        raise RuntimeError("normalization_output_overflow")


def ensure_conversion_complete(source_s: float, converted_s: float) -> None:
    """El WAV escrito no puede quedar corto respecto de la duración pedida.

    Un segundo, o el 1 % si el tramo es más largo, absorbe el error de un VBR
    sin cabecera. Un corte de `-fs` es mucho mayor y no pasa.
    """
    if not math.isfinite(source_s) or not math.isfinite(converted_s) or source_s <= 0:
        raise RuntimeError("normalization_duration_unknown")
    if converted_s + max(1.0, 0.01 * source_s) < source_s:
        raise RuntimeError("normalization_truncated")


