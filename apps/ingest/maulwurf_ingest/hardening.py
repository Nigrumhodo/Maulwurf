"""Autochequeo del endurecimiento del proceso de ingesta (S1.B1, I-S1-SG-06).

El compose declara el endurecimiento (J1.2), pero lo que protege el audio es lo que el
kernel aplica de verdad al proceso. Este módulo lo lee de `/proc` y del cgroup y devuelve
la lista de fallos; el arranque la usa para no servir si falta alguna garantía.

Garantías comprobadas:

- `/` montado de solo lectura.
- `TMPDIR` es un `tmpfs` con `size=` y `noexec,nosuid,nodev`.
- Ningún mount escribible está respaldado por disco: todo lo escribible vive en RAM.
- El proceso no corre como root.
- `RLIMIT_CORE` es 0 (soft y hard): sin core dumps aunque el host los capture.
- `memory.swap.max` del cgroup es 0: la memoria del contenedor no puede ir a swap.

`memory.swap.max` solo existe en cgroup v2: en v1 no hay garantía forzada de swap cero
(`memsw` acota memoria+swap y `swappiness` es orientativo), así que ahí falla cerrado con
`swap_unknown`; admitir hosts v1 es una decisión pendiente de J4.x.

El host (swap global, `core_pattern`) no se modifica desde aquí; se valida en el
despliegue (J4.x) y queda registrado como excepción en la bitácora.
"""
from __future__ import annotations

import os
import re
import resource
import tempfile
from dataclasses import dataclass
from pathlib import Path

MOUNTINFO = Path("/proc/self/mountinfo")
SWAP_MAX = Path("/sys/fs/cgroup/memory.swap.max")

# Sistemas de archivos sin respaldo en disco: escribir en ellos no deja nada durable.
RAM_BACKED_FS = frozenset(
    {"tmpfs", "ramfs", "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue"}
)
TMP_REQUIRED_OPTIONS = frozenset({"noexec", "nosuid", "nodev"})

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True)
class Mount:
    mount_point: str
    options: frozenset[str]
    fstype: str
    super_options: frozenset[str]

    @property
    def writable(self) -> bool:
        return "rw" in self.options and "ro" not in self.super_options

    @property
    def size_bounded(self) -> bool:
        return any(opt.startswith("size=") for opt in self.super_options)


@dataclass(frozen=True)
class ProcessFacts:
    mounts: tuple[Mount, ...]
    tmp_dir: str
    euid: int
    core_limits: tuple[int, int]
    swap_max: str | None  # None si el cgroup no expone el fichero


def _unescape(field: str) -> str:
    # mountinfo escapa espacio, tab, salto de línea y `\` como octal (`\040`).
    return _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    """Parsea `/proc/<pid>/mountinfo` (proc(5)); ignora líneas mal formadas."""
    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if "-" not in fields:
            continue
        sep = fields.index("-")
        if sep < 6 or len(fields) < sep + 4:
            continue
        mounts.append(
            Mount(
                mount_point=_unescape(fields[4]),
                options=frozenset(fields[5].split(",")),
                fstype=fields[sep + 1],
                super_options=frozenset(fields[sep + 3].split(",")),
            )
        )
    return tuple(mounts)


def mount_for(mounts: tuple[Mount, ...], path: str) -> Mount | None:
    """Mount que contiene `path`: el punto de montaje más largo; a igualdad, el último."""
    target = os.path.normpath(path)
    best: Mount | None = None
    for mount in mounts:
        point = mount.mount_point
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and (best is None or len(point) >= len(best.mount_point)):
            best = mount
    return best


def check(facts: ProcessFacts) -> list[str]:
    """Devuelve los códigos de fallo; lista vacía = proceso endurecido."""
    failures: list[str] = []

    root = mount_for(facts.mounts, "/")
    if root is None or root.writable:
        failures.append("root_writable")

    tmp = mount_for(facts.mounts, facts.tmp_dir)
    if tmp is None or tmp.mount_point == "/" or tmp.fstype != "tmpfs":
        failures.append("tmp_not_tmpfs")
    else:
        if not tmp.size_bounded:
            failures.append("tmp_unbounded")
        if not TMP_REQUIRED_OPTIONS <= tmp.options:
            failures.append("tmp_missing_noexec_nosuid_nodev")

    for mount in facts.mounts:
        if mount.writable and mount.fstype not in RAM_BACKED_FS:
            failures.append(f"disk_backed_writable_mount:{mount.mount_point}")

    if facts.euid == 0:
        failures.append("running_as_root")
    if facts.core_limits != (0, 0):
        failures.append("core_dumps_enabled")
    if facts.swap_max is None:
        failures.append("swap_unknown")
    elif facts.swap_max != "0":
        failures.append("swap_allowed")
    return failures


def collect() -> ProcessFacts:
    """Lee los hechos reales del proceso actual."""
    try:
        swap_max: str | None = SWAP_MAX.read_text().strip()
    except OSError:
        swap_max = None
    return ProcessFacts(
        mounts=parse_mountinfo(MOUNTINFO.read_text()),
        tmp_dir=tempfile.gettempdir(),
        euid=os.geteuid(),
        core_limits=resource.getrlimit(resource.RLIMIT_CORE),
        swap_max=swap_max,
    )
