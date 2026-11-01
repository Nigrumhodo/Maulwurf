"""Unidades puras de la verificación de limpieza (S1.B3): gating y guardia de pgid.

Sin BD ni ffmpeg: validan el fail-closed de `CleanupEvidence.verified` y que
`kill_group` no mate a un grupo ajeno si el pgid fue reciclado.
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import time
from pathlib import Path

from maulwurf_ingest import cleanup


def _evidence(**overrides: object) -> cleanup.CleanupEvidence:
    base = cleanup.CleanupEvidence(
        workdir_absent=True, tmp_is_tmpfs=True, live_processes=0, open_descriptors=0,
        mounts_under_workdir=0, unreadable_processes=0,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_verified_requires_every_check() -> None:
    assert _evidence().verified is True
    for field in ("workdir_absent", "tmp_is_tmpfs"):
        assert _evidence(**{field: False}).verified is False
    for field in ("live_processes", "open_descriptors", "mounts_under_workdir"):
        assert _evidence(**{field: 1}).verified is False


def test_unreadable_processes_fails_closed() -> None:
    # Si hubo /proc/<pid>/fd ilegibles (otro UID), la ausencia de descriptores no está
    # probada: un entorno mal endurecido no puede auto-acreditarse la limpieza.
    assert _evidence(unreadable_processes=1).verified is False


def test_absence_requires_every_proc_check() -> None:
    """S4.2: `audio_deleted_at` needs descriptors, processes and mounts, not a name."""
    evidence = _evidence().as_json()
    assert cleanup.absence_verified(evidence) is True
    for field in ("workdir_absent", "tmp_is_tmpfs"):
        assert cleanup.absence_verified({**evidence, field: False}) is False
    for field in (
        "live_processes", "open_descriptors", "mounts_under_workdir", "unreadable_processes",
    ):
        assert cleanup.absence_verified({**evidence, field: 1}) is False


def test_absence_is_not_proven_by_a_partial_report() -> None:
    # A caller that only says "verified" has not inspected /proc; a missing counter is
    # unknown, not zero.
    assert cleanup.absence_verified({}) is False
    assert cleanup.absence_verified({"verified": True}) is False
    assert cleanup.absence_verified({"workdir_absent": True, "tmp_is_tmpfs": True}) is False


def test_absence_rejects_booleans_and_strings_in_counters() -> None:
    evidence = _evidence().as_json()
    for field in (
        "live_processes", "open_descriptors", "mounts_under_workdir", "unreadable_processes",
    ):
        assert cleanup.absence_verified({**evidence, field: False}) is False
        assert cleanup.absence_verified({**evidence, field: "0"}) is False
        assert cleanup.absence_verified({**evidence, field: 0.0}) is False


def _spawn_sleeper() -> tuple[subprocess.Popen[bytes], int]:
    proc = subprocess.Popen(  # noqa: S603 — argv fija, sin shell
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    deadline = time.monotonic() + 10
    while (starttime := cleanup.start_time(proc.pid)) is None:
        assert time.monotonic() < deadline, "el hijo no apareció en /proc"
    return proc, starttime


def test_start_time_reads_the_process_field() -> None:
    proc, starttime = _spawn_sleeper()
    try:
        assert starttime > 0
        assert cleanup.start_time(proc.pid) == starttime
    finally:
        cleanup.kill_group(proc.pid, starttime)
        proc.wait(timeout=10)


def test_kill_group_kills_the_captured_group() -> None:
    proc, starttime = _spawn_sleeper()
    cleanup.kill_group(proc.pid, starttime)
    proc.wait(timeout=10)
    assert proc.poll() is not None


def test_kill_group_ignores_a_recycled_pgid() -> None:
    proc, starttime = _spawn_sleeper()
    try:
        # Un starttime que no puede coincidir: el líder visible no es el proceso capturado.
        cleanup.kill_group(proc.pid, starttime + 1)
        assert proc.poll() is None  # sigue vivo
    finally:
        cleanup.kill_group(proc.pid, starttime)
        proc.wait(timeout=10)


def test_kill_group_on_a_gone_group_is_a_noop() -> None:
    proc, starttime = _spawn_sleeper()
    cleanup.kill_group(proc.pid, starttime)
    proc.wait(timeout=10)
    cleanup.kill_group(proc.pid, starttime)  # ya no existe: no debe lanzar


def test_a_zombie_is_not_an_unreadable_process(tmp_path: Path) -> None:
    """A zombie released every descriptor: `/proc/<pid>/fd` is denied, but it leaks nothing.

    Counting it as unreadable used to make cleanup verification fail closed on a harmless
    race (the killed ffmpeg is still a zombie when the evidence is collected).
    """
    proc, _starttime = _spawn_sleeper()
    try:
        cleanup.kill_group(proc.pid, None)
        deadline = time.monotonic() + 10
        state = None
        while time.monotonic() < deadline:
            state = cleanup._state(proc.pid)
            if state == "Z":
                break
            time.sleep(0.01)
        assert state == "Z", "the child did not become a zombie"

        assert proc.pid not in cleanup._inspectable_pids()
        found, _unreadable = cleanup.open_descriptors_under(tmp_path)

        assert found == 0
    finally:
        proc.wait(timeout=10)
