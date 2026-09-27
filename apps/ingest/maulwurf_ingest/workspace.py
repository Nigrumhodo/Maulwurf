"""Directorio efímero por intento y verificación de limpieza (S1.B3, I-S1-SG-04/06).

El directorio vive en el tmpfs del proceso (`TMPDIR`). Borrarlo no basta como prueba:
un fichero borrado sigue ocupando RAM y siendo legible mientras alguien tenga un
descriptor abierto, y un nieto huérfano (ffmpeg) puede seguir escribiendo. Por eso la
verificación inspecciona `/proc`: descriptores, procesos del grupo y mounts, además de
comprobar que la ruta ya no existe.

La evidencia contiene solo booleanos y conteos, nunca rutas de ficheros ni contenido.
"""
from __future__ import annotations

import os
import shutil
import signal
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from maulwurf_ingest import hardening

PROC = Path("/proc")


@dataclass(frozen=True)
class CleanupEvidence:
    workdir_absent: bool
    tmp_is_tmpfs: bool
    live_processes: int  # procesos (no zombis) que quedan en el grupo del trabajo ASR
    open_descriptors: int  # descriptores abiertos a rutas bajo el directorio, en cualquier proceso
    mounts_under_workdir: int
    unreadable_processes: int  # procesos cuyo /proc/<pid>/fd no se pudo leer (otro UID)

    @property
    def verified(self) -> bool:
        return (
            self.workdir_absent
            and self.tmp_is_tmpfs
            and self.live_processes == 0
            and self.open_descriptors == 0
            and self.mounts_under_workdir == 0
        )

    def as_json(self) -> dict[str, object]:
        return {**asdict(self), "verified": self.verified}


def create_workdir(attempt_id: str, tmp_dir: str | None = None) -> Path:
    """Crea `<tmpfs>/attempt-<id>-XXXX` con permisos 0700."""
    return Path(tempfile.mkdtemp(prefix=f"attempt-{attempt_id}-", dir=tmp_dir))


def write_private(path: Path, data: bytes) -> None:
    # O_EXCL: nunca reutiliza un fichero existente; 0600: solo el proceso de ingesta.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def kill_group(pgid: int) -> None:
    """SIGKILL a todo el grupo del trabajo ASR (hijo y nietos como ffmpeg)."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _pids() -> list[int]:
    return [int(entry.name) for entry in PROC.iterdir() if entry.name.isdigit()]


def live_group_members(pgid: int) -> int:
    count = 0
    for pid in _pids():
        try:
            stat = (PROC / str(pid) / "stat").read_text()
        except OSError:
            continue
        # `comm` va entre paréntesis y puede contener espacios: se parte tras el último `)`.
        fields = stat[stat.rindex(")") + 2 :].split()
        state, pgrp = fields[0], int(fields[2])
        if pgrp == pgid and state not in {"Z", "X"}:  # un zombi ya no tiene descriptores
            count += 1
    return count


def open_descriptors_under(root: Path) -> tuple[int, int]:
    """(descriptores bajo `root`, procesos ilegibles) en todos los procesos visibles."""
    prefix = str(root).rstrip("/") + "/"
    found = unreadable = 0
    for pid in _pids():
        fd_dir = PROC / str(pid) / "fd"
        try:
            entries = list(fd_dir.iterdir())
        except PermissionError:
            unreadable += 1
            continue
        except OSError:
            continue  # el proceso terminó durante el recorrido
        for entry in entries:
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            # Un fichero borrado aparece como "<ruta> (deleted)": sigue contando.
            if target == str(root) or target.startswith(prefix):
                found += 1
    return found, unreadable


def destroy_and_verify(
    workdir: Path, pgid: int | None, *, settle_s: float = 5.0
) -> CleanupEvidence:
    """Mata el grupo, espera a que muera, borra el directorio y verifica en `/proc`.

    Bloqueante (lee `/proc` y duerme): el supervisor lo ejecuta en un hilo.
    """
    if pgid is not None:
        kill_group(pgid)
        # SIGKILL es asíncrono: un nieto puede seguir vivo unos milisegundos y escribir.
        deadline = time.monotonic() + settle_s
        while live_group_members(pgid) and time.monotonic() < deadline:
            time.sleep(0.05)
    shutil.rmtree(workdir, ignore_errors=True)

    mounts = hardening.parse_mountinfo(hardening.MOUNTINFO.read_text())
    tmp_mount = hardening.mount_for(mounts, str(workdir.parent))
    prefix = str(workdir).rstrip("/") + "/"
    descriptors, unreadable = open_descriptors_under(workdir)
    return CleanupEvidence(
        workdir_absent=not os.path.lexists(workdir),
        tmp_is_tmpfs=tmp_mount is not None and tmp_mount.fstype == "tmpfs",
        live_processes=live_group_members(pgid) if pgid is not None else 0,
        open_descriptors=descriptors,
        mounts_under_workdir=sum(
            1 for m in mounts if m.mount_point == str(workdir) or m.mount_point.startswith(prefix)
        ),
        unreadable_processes=unreadable,
    )
