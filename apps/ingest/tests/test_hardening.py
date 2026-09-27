"""U-/I-S1-SG-06 (parte S1.B1): autochequeo de endurecimiento sobre hechos simulados.

El mountinfo base es el que observa el contenedor `ingest` del compose (recortado).
"""
from dataclasses import replace
from typing import Any

import pytest

from maulwurf_ingest.hardening import ProcessFacts, check, mount_for, parse_mountinfo

CONTAINER_MOUNTINFO = """\
1842 1098 0:90 / / ro,relatime - overlay overlay rw,lowerdir=/l,upperdir=/u,workdir=/w
1849 1842 0:312 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw
1854 1842 0:317 / /dev rw,nosuid - tmpfs tmpfs rw,size=65536k,mode=755
1857 1854 0:319 / /dev/pts rw,nosuid,noexec,relatime - devpts devpts rw,gid=5,mode=620
1861 1842 0:124 / /sys ro,nosuid,nodev,noexec,relatime - sysfs sysfs ro
1866 1861 0:29 / /sys/fs/cgroup ro,nosuid,nodev,noexec,relatime - cgroup2 cgroup rw,nsdelegate
1876 1854 0:324 / /dev/shm rw,nosuid,nodev,noexec,relatime - tmpfs shm rw,size=65536k
1880 1842 259:2 /c/hostname /etc/hostname ro,relatime - ext4 /dev/nvme0n1p2 rw
1919 1842 0:327 / /work/tmp rw,nosuid,nodev,noexec,relatime - tmpfs tmpfs rw,size=262144k
"""


def _facts(mountinfo: str = CONTAINER_MOUNTINFO, **overrides: Any) -> ProcessFacts:
    base = ProcessFacts(
        mounts=parse_mountinfo(mountinfo),
        tmp_dir="/work/tmp",
        euid=10001,
        core_limits=(0, 0),
        swap_max="0",
    )
    return replace(base, **overrides)


def test_compose_container_passes() -> None:
    assert check(_facts()) == []


def test_parser_reads_fields_and_unescapes_octal() -> None:
    mounts = parse_mountinfo(
        "1 0 0:1 / /mnt/con\\040espacio rw,noexec - tmpfs tmpfs rw,size=1k\nbasura\n"
    )
    assert len(mounts) == 1
    assert mounts[0].mount_point == "/mnt/con espacio"
    assert mounts[0].fstype == "tmpfs"
    assert mounts[0].size_bounded


def test_mount_for_picks_the_deepest_mount() -> None:
    mounts = parse_mountinfo(CONTAINER_MOUNTINFO)
    assert mount_for(mounts, "/work/tmp/intento/a.wav").mount_point == "/work/tmp"  # type: ignore[union-attr]
    assert mount_for(mounts, "/work/tmpx").mount_point == "/"  # type: ignore[union-attr]


def test_writable_root_fails() -> None:
    mountinfo = CONTAINER_MOUNTINFO.replace("/ / ro,relatime", "/ / rw,relatime")
    failures = check(_facts(mountinfo))
    assert "root_writable" in failures
    # Un overlay escribible es disco: también se reporta como mount persistente.
    assert "disk_backed_writable_mount:/" in failures


def test_tmp_on_root_filesystem_fails() -> None:
    assert "tmp_not_tmpfs" in check(_facts(tmp_dir="/app/scratch"))


def test_unbounded_tmpfs_fails() -> None:
    mountinfo = CONTAINER_MOUNTINFO.replace("tmpfs tmpfs rw,size=262144k", "tmpfs tmpfs rw")
    assert check(_facts(mountinfo)) == ["tmp_unbounded"]


def test_tmpfs_without_noexec_fails() -> None:
    mountinfo = CONTAINER_MOUNTINFO.replace(
        "/work/tmp rw,nosuid,nodev,noexec,relatime", "/work/tmp rw,nosuid,nodev,relatime"
    )
    assert check(_facts(mountinfo)) == ["tmp_missing_noexec_nosuid_nodev"]


def test_writable_disk_bind_mount_fails() -> None:
    mountinfo = CONTAINER_MOUNTINFO + (
        "1990 1842 259:2 /home/u/audios /data rw,relatime - ext4 /dev/nvme0n1p2 rw\n"
    )
    assert check(_facts(mountinfo)) == ["disk_backed_writable_mount:/data"]


def test_writable_cgroup_v1_mount_is_ram_backed() -> None:
    mountinfo = CONTAINER_MOUNTINFO + (
        "1999 1842 0:36 /memory /sys/fs/cgroup/memory rw,nosuid,nodev,noexec,relatime"
        " - cgroup cgroup rw,memory\n"
    )
    assert check(_facts(mountinfo)) == []


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"euid": 0}, "running_as_root"),
        ({"core_limits": (0, -1)}, "core_dumps_enabled"),
        ({"core_limits": (1024, 1024)}, "core_dumps_enabled"),
        ({"swap_max": "max"}, "swap_allowed"),
        ({"swap_max": None}, "swap_unknown"),
    ],
)
def test_process_limits_fail_closed(override: dict[str, Any], code: str) -> None:
    assert check(_facts(**override)) == [code]
