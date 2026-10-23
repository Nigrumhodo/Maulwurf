"""S2.12: a probe that does not exit is killed with its children."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from maulwurf_ingest.audio import validate


def _running(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return False
    text = stat.read_text(encoding="utf-8")
    fields = text[text.rindex(")") + 2 :].split()
    return fields[0] not in {"Z", "X"}


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_hung_ffprobe_is_killed_with_its_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "hang.py"
    script.write_text(
        "import os, sys, time, pathlib\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(30)\n"
        "    raise SystemExit(0)\n"
        "path.write_text(f'{os.getpid()} {child}\\n')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    pidfile = tmp_path / "pids"
    monkeypatch.setattr(validate, "_PROBE_TIMEOUT_S", 0.4)
    with pytest.raises(validate._ProbeCrashed):
        validate._run_ffprobe([sys.executable, str(script), str(pidfile)])
    parent_s, child_s = pidfile.read_text(encoding="utf-8").split()
    assert _running(int(parent_s)) is False
    assert _running(int(child_s)) is False
