"""Directorio efímero por intento y verificación de limpieza (S1.B3, I-S1-SG-04/06).

El directorio vive en el tmpfs del proceso (`TMPDIR`). Borrarlo no basta como prueba:
un fichero borrado sigue ocupando RAM y siendo legible mientras alguien tenga un
descriptor abierto, y un nieto huérfano (ffmpeg) puede seguir escribiendo. Por eso la
verificación inspecciona `/proc`: descriptores, procesos del grupo y mounts, además de
comprobar que la ruta ya no existe.

La evidencia contiene solo booleanos y conteos, nunca rutas de ficheros ni contenido.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from maulwurf_ingest import hardening, metrics

PROC = Path("/proc")
logger = logging.getLogger(__name__)

_ATTEMPT_DIR_RE = re.compile(
    r"\Aattempt-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})-"
)

_ABSENCE_FLAGS = ("workdir_absent", "tmp_is_tmpfs")
_ABSENCE_COUNTERS = (
    "live_processes", "open_descriptors", "mounts_under_workdir", "unreadable_processes",
)


# The only cleanup_status=verified writers are destroy evidence and this attestation.
OPERATOR_ATTESTATION = "operator_attestation"


def proven_cleanup(evidence: object) -> bool:
    """True when `verified` was written by destruction evidence or a host-loss attestation."""
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            return False
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("source") == OPERATOR_ATTESTATION:
        return evidence.get("verified") is True
    return absence_verified(evidence)


def absence_verified(evidence: Mapping[str, object]) -> bool:
    """True only when the evidence proves that no temporary survived.

    A deleted file can stay readable through an open descriptor, an orphan ffmpeg can
    keep writing into the tmpfs, and a mount can hide the directory. File names alone
    are not proof, so every descriptor/process/mount check must be present and exact.
    A missing or malformed counter means "unknown", never zero.
    """
    if any(evidence.get(name) is not True for name in _ABSENCE_FLAGS):
        return False
    for name in _ABSENCE_COUNTERS:
        value = evidence.get(name)
        # bool is an int subclass: `False` must not pass as a zero counter.
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            return False
    return True


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
        # Fail-closed: `unreadable_processes > 0` significa que hubo /proc/<pid>/fd
        # ilegibles (otro UID); la ausencia de descriptores NO está probada. En el
        # contenedor endurecido (un solo UID) debe ser 0.
        return (
            self.workdir_absent
            and self.tmp_is_tmpfs
            and self.live_processes == 0
            and self.open_descriptors == 0
            and self.mounts_under_workdir == 0
            and self.unreadable_processes == 0
        )

    def as_json(self) -> dict[str, object]:
        return {**asdict(self), "verified": self.verified}


def log_cleanup_failed(
    attempt_id: str, evidence: CleanupEvidence | None, *, error_type: str | None = None
) -> None:
    """Raise the immediate `cleanup.failed` ERROR alert with counters, never paths or content.

    Admission is closed separately by `admission.halt`; this function only alerts and counts.
    """
    counters: dict[str, object] = (
        evidence.as_json() if evidence is not None else {"verified": False}
    )
    if error_type is not None:
        counters["error"] = error_type
    metrics.record_cleanup_failure()
    logger.error(
        "cleanup.failed attempt_id=%s", attempt_id,
        extra={"attempt_id": attempt_id, "fields": counters},
    )


def attempt_id_from_dirname(name: str) -> uuid.UUID | None:
    """Parse ``attempt-<uuid>-<suffix>``. Other names are not attempt directories."""
    match = _ATTEMPT_DIR_RE.match(name)
    if match is None:
        return None
    try:
        return uuid.UUID(match.group(1))
    except ValueError:
        return None


def list_attempt_dirs(root: Path) -> list[Path]:
    """Real attempt directories under ``root``. Symlinks are ignored."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        if attempt_id_from_dirname(entry.name) is None:
            continue
        found.append(entry)
    return sorted(found)


@dataclass(frozen=True)
class OrphanCleanup:
    attempt_id: uuid.UUID
    evidence: CleanupEvidence | None


async def sweep_orphan_dirs(
    root: Path, *, protected: set[uuid.UUID],
) -> tuple[OrphanCleanup, ...]:
    """Retry verified destruction of attempt directories with no live owner.

    ``protected`` is the set of attempt ids that still hold a live lease or an
    active receiving/transcribing row. Everything else is destroyed again so a
    failed ``rmtree`` does not leave audio in the tmpfs until the container is
    recreated.
    """
    results: list[OrphanCleanup] = []
    for path in list_attempt_dirs(root):
        attempt_id = attempt_id_from_dirname(path.name)
        if attempt_id is None or attempt_id in protected:
            continue
        try:
            evidence = await AttemptDir.destroy_one(path)
        except Exception as exc:  # noqa: BLE001 — one directory must not abort the sweep
            logger.error(
                "cleanup.orphan_sweep_error attempt_id=%s error=%s",
                attempt_id, type(exc).__name__,
            )
            log_cleanup_failed(str(attempt_id), None, error_type=type(exc).__name__)
            results.append(OrphanCleanup(attempt_id, None))
            continue
        if evidence.workdir_absent:
            logger.info(
                "cleanup.orphan_swept attempt_id=%s verified=%s",
                attempt_id, evidence.verified,
            )
        else:
            log_cleanup_failed(str(attempt_id), evidence)
        results.append(OrphanCleanup(attempt_id, evidence))
    return tuple(results)


def create_workdir(attempt_id: str, tmp_dir: str | None = None) -> Path:
    """Crea `<tmpfs>/attempt-<id>-XXXX` con permisos 0700."""
    return Path(tempfile.mkdtemp(prefix=f"attempt-{attempt_id}-", dir=tmp_dir))


def write_private(path: Path, data: bytes) -> None:
    # O_EXCL: nunca reutiliza un fichero existente; 0600: solo el proceso de ingesta.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def start_time(pid: int) -> int | None:
    """Campo `starttime` (22) de `/proc/<pid>/stat`; None si el proceso ya no existe."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2 :].split()
    return int(fields[19])


def kill_group(pgid: int, starttime: int | None = None) -> None:
    """SIGKILL a todo el grupo del trabajo ASR (hijo y nietos como ffmpeg).

    Con `starttime` del hijo capturado al arrancar se descarta el reciclado teórico del
    pgid: si el líder visible no es ese proceso, el grupo original ya no existe y matarlo
    podría alcanzar a un grupo ajeno.
    """
    if starttime is not None:
        current = start_time(pgid)
        if current is not None and current != starttime:
            return
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


def _state(pid: int) -> str | None:
    """Process state from `/proc/<pid>/stat`; None when it cannot be read."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    try:
        return stat[stat.rindex(")") + 2 :].split()[0]
    except (ValueError, IndexError):
        return None


def _inspectable_pids() -> list[int]:
    """Visible PIDs whose descriptors can be inspected; zombies are excluded."""
    return [pid for pid in _pids() if _state(pid) not in {"Z", "X"}]


def open_descriptors_under(root: Path) -> tuple[int, int]:
    """(descriptores bajo `root`, procesos ilegibles) en todos los procesos visibles.

    A zombie has already released every descriptor, so it is skipped instead of counted
    as an unreadable process: reading `/proc/<pid>/fd` of a zombie is denied even for its
    own UID, and counting it used to fail cleanup verification closed on a harmless race.
    """
    prefix = str(root).rstrip("/") + "/"
    found = unreadable = 0
    for pid in _inspectable_pids():
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
    workdir: Path, pgid: int | None, *, starttime: int | None = None, settle_s: float = 5.0
) -> CleanupEvidence:
    """Mata el grupo, espera a que muera, borra el directorio y verifica en `/proc`.

    Bloqueante (lee `/proc` y duerme): el supervisor lo ejecuta en un hilo.
    """
    if pgid is not None:
        kill_group(pgid, starttime)
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


class _Stoppable(Protocol):
    async def stop(self) -> None: ...


DestroyedHook = Callable[[CleanupEvidence | None, BaseException | None], Awaitable[None]]


class AttemptDir:
    """The only owner of an attempt directory.

    `__aenter__` is the only `mkdtemp`. `__aexit__` is the only `destroy_and_verify`,
    and it finishes that check even when cancellation arrives again. A `handoff`
    lets reception keep the directory alive across requests; the later `async with`
    on the same object is the one that destroys it.
    """

    def __init__(
        self, attempt_id: str, tmp_dir: str | None = None, *, path: Path | None = None,
    ) -> None:
        self.attempt_id = attempt_id
        self._tmp_dir = tmp_dir
        self.path = path
        self.pgid: int | None = None
        self.starttime: int | None = None
        self.evidence: CleanupEvidence | None = None
        self.destroy_error: BaseException | None = None
        self.on_destroyed: DestroyedHook | None = None
        self.keeper: _Stoppable | None = None
        self._handoff = False
        self._closed = False

    @classmethod
    def adopt(cls, path: Path) -> AttemptDir:
        """Own a directory this class already created. Entering it does not `mkdtemp`."""
        return cls(path.name, path=path)

    @classmethod
    async def destroy_one(cls, path: Path) -> CleanupEvidence:
        """Verified destruction of one leftover directory. No process group is known."""
        audio = cls.adopt(path)
        async with audio:
            pass
        if audio.destroy_error is not None:
            raise audio.destroy_error
        if audio.evidence is None:
            raise RuntimeError("cleanup_missing")
        return audio.evidence

    async def __aenter__(self) -> AttemptDir:
        if self._closed:
            raise RuntimeError("attempt_dir_closed")
        if self.path is None:
            # mkdtemp is local and fast. Keeping it on the event loop means a test
            # double of `asyncio.to_thread` (the probe) cannot skip directory creation.
            self.path = create_workdir(self.attempt_id, self._tmp_dir)
        return self

    def handoff(self) -> None:
        """The `async with` that is exiting must leave the directory in place."""
        self._handoff = True

    async def aclose(self) -> None:
        """Destroy the directory if a later `async with` never ran."""
        if self._closed:
            return
        self._handoff = False
        await self.__aexit__(None, None, None)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        if self._handoff:
            self._handoff = False
            return False
        saw_cancel = exc_type is asyncio.CancelledError
        saw_cancel = await self._finish(saw_cancel)
        if saw_cancel:
            raise asyncio.CancelledError
        return False

    async def _finish(self, saw_cancel: bool) -> bool:
        if self._closed:
            return saw_cancel
        self._closed = True
        if self.path is None:
            return saw_cancel
        task = asyncio.create_task(asyncio.to_thread(
            destroy_and_verify, self.path, self.pgid, starttime=self.starttime,
        ))
        while not task.done():
            try:
                self.evidence = await asyncio.shield(task)
            except asyncio.CancelledError:
                saw_cancel = True
            except Exception as exc:
                self.destroy_error = exc
                break
        if (
            self.evidence is None
            and self.destroy_error is None
            and task.done()
            and not task.cancelled()
        ):
            error = task.exception()
            if error is not None:
                self.destroy_error = error
            else:
                self.evidence = task.result()
        callback_error: BaseException | None = None
        try:
            if self.on_destroyed is not None:
                hook = self.on_destroyed

                async def _record() -> None:
                    await hook(self.evidence, self.destroy_error)

                record = asyncio.create_task(_record())
                while not record.done():
                    try:
                        await asyncio.shield(record)
                    except asyncio.CancelledError:
                        saw_cancel = True
                    except Exception as exc:
                        callback_error = exc
                        break
                if callback_error is None and record.done() and not record.cancelled():
                    failed = record.exception()
                    if failed is not None:
                        callback_error = failed
        finally:
            keeper = self.keeper
            if keeper is not None:
                await keeper.stop()
        if saw_cancel:
            return True
        if callback_error is not None:
            raise callback_error
        return False
