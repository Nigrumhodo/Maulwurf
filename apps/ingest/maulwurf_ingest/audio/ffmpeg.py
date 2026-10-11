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
from collections.abc import Callable, Sequence
from dataclasses import dataclass

PROTOCOL_WHITELIST = "file,pipe,crypto"
CANDIDATE_SAMPLE_RATE_HZ = 16_000

# Techo de salida por invocación: el tmpfs provisional del slot (F0.2), no un
# máximo de Riva. El timeout de pared conserva el de S1.B3.
_OUTPUT_BYTES = 256 * 1024 * 1024
_CPU_SECONDS = 120
# 512 MiB deja a ffmpeg sin poder mapear sus buffers (ENOMEM) en un WAV corto.
_ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024
_TIME_LIMIT_S = 120
_WALL_TIMEOUT_S = 600.0


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


def run(argv: Sequence[str], *, limits: FfmpegLimits, capture_stderr: bool = False) -> bytes:
    """Ejecuta `argv` sin shell. Con `capture_stderr`, devuelve stderr y no lo loguea."""
    resolved = _resolve(argv)
    stderr_mode: int | None = subprocess.PIPE if capture_stderr else subprocess.DEVNULL
    if os.name == "posix":
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            resolved,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
            preexec_fn=_limit_child(limits),
        )
    elif sys.platform == "win32":
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            resolved,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        completed = subprocess.run(  # noqa: S603 — argv lista, sin shell
            resolved,
            stdout=subprocess.DEVNULL,
            stderr=stderr_mode,
            timeout=limits.wall_timeout_s,
            check=False,
            shell=False,
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


def _limit_child(limits: FfmpegLimits) -> Callable[[], None]:
    def _apply() -> None:
        import resource

        cpu = (limits.cpu_seconds, limits.cpu_seconds)
        space = (limits.address_space_bytes, limits.address_space_bytes)
        resource.setrlimit(resource.RLIMIT_CPU, cpu)
        resource.setrlimit(resource.RLIMIT_AS, space)

    return _apply
