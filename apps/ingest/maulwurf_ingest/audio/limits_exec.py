"""Apply RLIMIT_* and exec the requested binary.

Fallback for hosts without ``prlimit``. The parent spawns a fresh interpreter,
so the multithreaded ingest process never runs code between fork and exec.
"""

from __future__ import annotations

import os
import sys


def parse_limits(argv: list[str]) -> tuple[int, int, int, list[str]]:
    """Split ``--cpu N --as N --fsize N -- command...``."""
    if "--" not in argv:
        raise SystemExit(2)
    split = argv.index("--")
    flags = argv[:split]
    command = argv[split + 1 :]
    if not command or len(flags) != 6:
        raise SystemExit(2)
    values: dict[str, int] = {}
    index = 0
    while index < len(flags):
        name = flags[index]
        if name not in {"--cpu", "--as", "--fsize"} or index + 1 >= len(flags):
            raise SystemExit(2)
        try:
            parsed = int(flags[index + 1])
        except ValueError:
            raise SystemExit(2) from None
        if parsed < 1:
            raise SystemExit(2)
        values[name] = parsed
        index += 2
    if set(values) != {"--cpu", "--as", "--fsize"}:
        raise SystemExit(2)
    return values["--cpu"], values["--as"], values["--fsize"], command


def main(argv: list[str]) -> None:
    cpu, space, fsize, command = parse_limits(argv)
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (space, space))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    os.execv(command[0], command)  # noqa: S606 — exec of the already-built ffmpeg argv


if __name__ == "__main__":
    main(sys.argv[1:])
