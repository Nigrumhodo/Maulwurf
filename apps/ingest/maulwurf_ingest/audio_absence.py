"""Barrido de ausencia de audio (S1.B5, I-S1-SG-06).

Busca original, convertidos y fragmentos en disco, Redis, logs, trazas y cachés.
El informe solo tiene booleanos y conteos: nunca rutas, bytes ni dumps.

En mounts y cachés se buscan los nombres que escribe el ASR de S1 (`original`,
`converted.wav`, `frag-NNNN.wav`). En el tmpfs del intento también cuenta cualquier
sufijo de audio: una biblioteca ajena en la caché del usuario no es una fuga de
ingesta. De cada candidato se leen como máximo 12 bytes para ver magia (`RIFF`,
`OggS`, `fLaC`, `ID3`); esos bytes no se guardan.

S1 no exporta trazas: `traces_configured` queda en falso y el conteo de marcadores
en cero salvo que el llamador pase texto de traza.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from maulwurf_ingest import hardening

FILE_BUDGET = 500_000
_HEAD = 12
_MAGIC = (b"RIFF", b"OggS", b"fLaC", b"ID3", b"data:audio")
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm"})
# Pseudo-fs y discos ajenos (el disco de Windows montado en WSL no es el host de ingesta).
_SKIP_FSTYPES = frozenset({
    "proc", "sysfs", "cgroup", "cgroup2", "devtmpfs", "devpts", "bpf", "tracefs",
    "securityfs", "pstore", "debugfs", "mqueue", "hugetlbfs", "configfs", "fusectl",
    "autofs", "binfmt_misc", "nsfs", "rpc_pipefs", "ramfs",
    "drvfs", "9p", "cifs", "nfs", "nfs4", "smb3", "fuse.drvfs",
})


@dataclass
class _Counts:
    audio_files: int = 0
    magic_matches: int = 0
    unreadable_directories: int = 0
    seen: int = 0
    complete: bool = True


@dataclass(frozen=True)
class AbsenceReport:
    audio_files: int
    magic_matches: int
    unreadable_directories: int
    mounts_scanned: int
    cache_audio_files: int
    redis_audio_markers: int
    redis_checked: bool
    log_audio_markers: int
    trace_audio_markers: int
    traces_configured: bool
    scan_complete: bool
    redis_ok: bool = field(default=True)

    @property
    def clear(self) -> bool:
        return (
            self.scan_complete
            and self.redis_ok
            and self.audio_files == 0
            and self.magic_matches == 0
            and self.cache_audio_files == 0
            and self.redis_audio_markers == 0
            and self.log_audio_markers == 0
            and self.trace_audio_markers == 0
        )

    def as_json(self) -> dict[str, object]:
        return {**asdict(self), "clear": self.clear}


def _artifact(name: str) -> bool:
    return name == "original" or name == "converted.wav" or (
        name.startswith("frag-") and name.endswith(".wav") and name[5:-4].isdigit()
    )


def _interesting(name: str, *, suffixes: bool) -> bool:
    if _artifact(name):
        return True
    return suffixes and Path(name).suffix.lower() in _AUDIO_SUFFIXES


def _magic(path: Path) -> bool:
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags | nofollow)
    except OSError:
        return False
    try:
        head = os.read(fd, _HEAD)
    except OSError:
        return False
    finally:
        os.close(fd)
    return any(head.startswith(marker) for marker in _MAGIC)


def _note(counts: _Counts, budget: int) -> bool:
    counts.seen += 1
    if counts.seen > budget:
        counts.complete = False
        return False
    return True


def _walk(root: Path, counts: _Counts, *, suffixes: bool, budget: int, prune: set[str]) -> None:
    root_n = os.path.normpath(root)

    def _onerror(_err: OSError) -> None:
        counts.unreadable_directories += 1

    try:
        walker = os.walk(root, followlinks=False, onerror=_onerror)
        for dirpath, dirnames, filenames in walker:
            if not _note(counts, budget):
                return
            kept: list[str] = []
            for name in dirnames:
                child = os.path.normpath(os.path.join(dirpath, name))
                if child in prune and child != root_n:
                    continue
                kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                if not _note(counts, budget):
                    return
                if not _interesting(name, suffixes=suffixes):
                    continue
                counts.audio_files += 1
                candidate = Path(dirpath) / name
                try:
                    is_file = candidate.is_file()
                except OSError:
                    counts.unreadable_directories += 1
                    continue
                if is_file and _magic(candidate):
                    counts.magic_matches += 1
    except OSError:
        counts.unreadable_directories += 1


def scan_tree(root: Path, *, suffixes: bool = True, budget: int = FILE_BUDGET) -> _Counts:
    """Recorre un árbol. `suffixes` cuenta también extensiones de audio."""
    counts = _Counts()
    try:
        present = root.exists()
    except OSError:
        counts.unreadable_directories += 1
        return counts
    if present:
        _walk(root, counts, suffixes=suffixes, budget=budget, prune=set())
    return counts


def report_from_tree(root: Path) -> AbsenceReport:
    """Informe de un solo árbol (pruebas). No incluye mounts, Redis ni logs."""
    counts = scan_tree(root)
    return AbsenceReport(
        audio_files=counts.audio_files,
        magic_matches=counts.magic_matches,
        unreadable_directories=counts.unreadable_directories,
        mounts_scanned=0,
        cache_audio_files=0,
        redis_audio_markers=0,
        redis_checked=False,
        log_audio_markers=0,
        trace_audio_markers=0,
        traces_configured=False,
        scan_complete=counts.complete,
        redis_ok=True,
    )


def scannable_mount_points(mountinfo: str) -> list[str]:
    """Puntos de montaje donde la ingesta podría haber dejado audio."""
    points: list[str] = []
    for mount in hardening.parse_mountinfo(mountinfo):
        if mount.fstype in _SKIP_FSTYPES:
            continue
        points.append(os.path.normpath(mount.mount_point))
    return points


def default_cache_roots() -> list[Path]:
    roots: list[Path] = []
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        roots.append(Path(xdg))
    home_cache = Path.home() / ".cache"
    if home_cache not in roots:
        roots.append(home_cache)
    return [root for root in roots if root.is_dir()]


def count_markers(blobs: Iterable[bytes]) -> int:
    """Cuántos buffers empiezan por magia de audio. No conserva los bytes."""
    found = 0
    for blob in blobs:
        head = blob[:64]
        if any(head.startswith(marker) or marker in head[:64] for marker in _MAGIC):
            found += 1
    return found


def count_text_markers(texts: Iterable[str]) -> int:
    encoded = (text.encode("utf-8", errors="replace") for text in texts)
    return count_markers(encoded)


class _Reader:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""

    def _fill(self) -> None:
        chunk = self._sock.recv(4096)
        if not chunk:
            raise OSError("redis closed")
        self._buf += chunk

    def _line(self) -> bytes:
        while b"\r\n" not in self._buf:
            self._fill()
        line, _, rest = self._buf.partition(b"\r\n")
        self._buf = rest
        return line

    def _exact(self, size: int) -> bytes:
        while len(self._buf) < size:
            self._fill()
        data, self._buf = self._buf[:size], self._buf[size:]
        return data

    def value(self) -> object:
        if not self._buf:
            self._fill()
        kind, self._buf = self._buf[:1], self._buf[1:]
        if kind == b"+":
            return self._line().decode("utf-8", errors="replace")
        if kind == b"-":
            self._line()
            raise OSError("redis error")
        if kind == b":":
            return int(self._line())
        if kind == b"$":
            length = int(self._line())
            if length < 0:
                return None
            data = self._exact(length)
            if self._exact(2) != b"\r\n":
                raise OSError("redis framing")
            return data
        if kind == b"*":
            count = int(self._line())
            if count < 0:
                return None
            return [self.value() for _ in range(count)]
        raise OSError("redis type")


def _command(sock: socket.socket, reader: _Reader, *parts: str) -> object:
    payload = f"*{len(parts)}\r\n".encode()
    for part in parts:
        raw = part.encode()
        payload += f"${len(raw)}\r\n".encode() + raw + b"\r\n"
    sock.sendall(payload)
    return reader.value()


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode()
    raise OSError("redis type")


def read_redis_heads(url: str, *, limit: int = 5000) -> list[bytes]:
    """Cabeceras de los valores string. No devuelve claves ni el resto del valor."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    db = (parsed.path or "/0").lstrip("/") or "0"
    password = unquote(parsed.password) if parsed.password else None
    sock = socket.create_connection((host, port), timeout=2.0)
    try:
        sock.settimeout(2.0)
        reader = _Reader(sock)
        if password is not None:
            _command(sock, reader, "AUTH", password)
        _command(sock, reader, "SELECT", db)
        heads: list[bytes] = []
        cursor = "0"
        while True:
            reply = _command(sock, reader, "SCAN", cursor, "COUNT", "200")
            if not isinstance(reply, list) or len(reply) != 2 or not isinstance(reply[1], list):
                raise OSError("redis scan")
            cursor = _as_bytes(reply[0]).decode()
            keys = reply[1]
            for key in keys:
                if len(heads) >= limit:
                    return heads
                key_s = _as_bytes(key).decode("utf-8", errors="replace")
                kind = _command(sock, reader, "TYPE", key_s)
                if kind != "string":
                    continue
                head = _command(sock, reader, "GETRANGE", key_s, "0", str(_HEAD - 1))
                if isinstance(head, bytes):
                    heads.append(head)
            if cursor == "0":
                return heads
    finally:
        sock.close()


def sweep(
    *,
    extra_roots: Sequence[Path] = (),
    cache_roots: Sequence[Path] | None = None,
    log_texts: Sequence[str] = (),
    trace_texts: Sequence[str] = (),
    redis_url: str | None = None,
    mountinfo: str | None = None,
    budget: int = FILE_BUDGET,
) -> AbsenceReport:
    """Barrido del host. `extra_roots` (tmpfs del intento) cuenta sufijos; el resto, nombres."""
    counts = _Counts()
    info = mountinfo if mountinfo is not None else _read_mountinfo()
    points = scannable_mount_points(info) if info is not None else []
    prune = {os.path.normpath(mount.mount_point) for mount in hardening.parse_mountinfo(info or "")}
    for point in points:
        if not counts.complete:
            break
        root = Path(point)
        try:
            is_dir = root.is_dir()
        except OSError:
            counts.unreadable_directories += 1
            continue
        if is_dir:
            _walk(root, counts, suffixes=False, budget=budget, prune=prune)

    cache_counts = _Counts()
    for root in default_cache_roots() if cache_roots is None else cache_roots:
        if not cache_counts.complete:
            break
        _walk(root, cache_counts, suffixes=False, budget=budget, prune=set())

    extra = _Counts()
    for root in extra_roots:
        if not extra.complete:
            break
        _walk(root, extra, suffixes=True, budget=budget, prune=set())

    redis_checked = False
    redis_ok = True
    redis_markers = 0
    if redis_url:
        try:
            redis_markers = count_markers(read_redis_heads(redis_url))
            redis_checked = True
        except OSError:
            redis_ok = False

    audio_files = counts.audio_files + extra.audio_files
    magic = counts.magic_matches + extra.magic_matches + cache_counts.magic_matches
    unreadable = (
        counts.unreadable_directories
        + extra.unreadable_directories
        + cache_counts.unreadable_directories
    )
    complete = counts.complete and extra.complete and cache_counts.complete and info is not None
    return AbsenceReport(
        audio_files=audio_files,
        magic_matches=magic,
        unreadable_directories=unreadable,
        mounts_scanned=len(points),
        cache_audio_files=cache_counts.audio_files,
        redis_audio_markers=redis_markers,
        redis_checked=redis_checked,
        log_audio_markers=count_text_markers(log_texts),
        trace_audio_markers=count_text_markers(trace_texts),
        traces_configured=False,
        scan_complete=complete,
        redis_ok=redis_ok,
    )


def _read_mountinfo() -> str | None:
    try:
        return hardening.MOUNTINFO.read_text()
    except OSError:
        return None
