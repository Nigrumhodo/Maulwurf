"""I-S1-SG-06 (parte S1.B1): endurecimiento REAL del contenedor `ingest` de compose.

Se comprueba desde fuera (`docker inspect`, host) y desde dentro (`docker exec`), sobre el
contenedor en marcha, no sobre el YAML. Sin audio: solo se escriben ceros y marcas vacías,
y todo se borra al terminar.

Requiere el stack de `infra/` levantado (`docker compose up -d --wait`). Si no hay Docker o
contenedor `ingest`, los tests se saltan (por ejemplo en el job de integración de CI, que
no levanta compose). `MAULWURF_INGEST_CONTAINER` fuerza un contenedor concreto.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO / "infra" / "docker-compose.yml"
HOST_COREDUMPS = Path("/var/lib/systemd/coredump")
TMP_DIR = "/work/tmp"
INGEST_UID = "10001"


def _run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — argv fija, sin shell
        list(args), capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(scope="module")
def container() -> str:
    if shutil.which("docker") is None:
        pytest.skip("docker no disponible")
    name = os.environ.get("MAULWURF_INGEST_CONTAINER")
    if not name:
        result = _run("docker", "compose", "-f", str(COMPOSE_FILE), "ps", "-q", "ingest")
        name = result.stdout.strip()
    if not name or _run("docker", "inspect", name).returncode != 0:
        pytest.skip("contenedor ingest de compose no está en marcha")
    return name


@pytest.fixture(scope="module")
def inspect(container: str) -> dict[str, Any]:
    data: list[dict[str, Any]] = json.loads(_run("docker", "inspect", container).stdout)
    return data[0]


def _py(container: str, code: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return _run("docker", "exec", container, "python", "-c", code, timeout=timeout)


def _wait_healthy(container: str, deadline_s: float = 90) -> None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        state = _run(
            "docker", "inspect", "--format", "{{.State.Health.Status}}", container
        ).stdout.strip()
        if state == "healthy":
            return
        time.sleep(1)
    raise AssertionError(f"ingest no volvió a healthy en {deadline_s:.0f} s")


def test_runtime_configuration_is_hardened(inspect: dict[str, Any]) -> None:
    host = inspect["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert inspect["Config"]["User"] == f"{INGEST_UID}:{INGEST_UID}"
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in host["SecurityOpt"]
    assert host["Memory"] > 0
    assert host["MemorySwap"] == host["Memory"]  # sin swap adicional
    assert host["PidsLimit"] and host["PidsLimit"] > 0
    assert {"Name": "core", "Soft": 0, "Hard": 0} in host["Ulimits"]
    tmpfs_opts = host["Tmpfs"][TMP_DIR].split(",")
    assert any(opt.startswith("size=") for opt in tmpfs_opts)
    assert {"noexec", "nosuid", "nodev"} <= set(tmpfs_opts)
    # Ningún bind mount ni volumen: nada del contenedor sobrevive en disco.
    assert [m for m in inspect["Mounts"] if m["Type"] in {"bind", "volume"}] == []


def test_self_check_passes_inside_the_container(container: str) -> None:
    result = _py(
        container,
        "import json;from maulwurf_ingest import hardening as h;"
        "print(json.dumps(h.check(h.collect())))",
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert json.loads(result.stdout) == []


def test_readyz_is_ready(container: str) -> None:
    result = _py(
        container,
        "import urllib.request;"
        "print(urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=5).status)",
    )
    assert result.stdout.strip() == "200", result.stderr[-500:]


@pytest.mark.parametrize(
    "path",
    # Rutas dentro del contenedor que deben rechazar escritura; no se usan como temporales.
    ["/probe", "/app/probe", "/home/probe", "/var/tmp/probe"],  # noqa: S108
)
def test_writes_outside_tmpfs_are_refused(container: str, path: str) -> None:
    result = _py(
        container,
        "import errno,sys\n"
        f"try:\n open({path!r},'wb').close()\n"
        "except OSError as e:\n print(errno.errorcode[e.errno]); sys.exit(0)\n"
        "print('WRITABLE'); sys.exit(1)",
    )
    assert result.returncode == 0, f"{path} es escribible"
    assert result.stdout.strip() in {"EROFS", "EACCES", "ENOENT"}


def test_tmpfs_is_bounded_and_does_not_spill(container: str) -> None:
    # Llena el tmpfs con ceros hasta ENOSPC: el límite lo aplica el kernel, sin desbordar
    # a disco. El fichero se borra siempre, también si el test falla.
    result = _py(
        container,
        "import errno,os,sys\n"
        f"p=os.path.join({TMP_DIR!r},'fill-probe'); n=0; chunk=b'\\0'*(8<<20)\n"
        "try:\n"
        " with open(p,'wb',buffering=0) as f:\n"
        "  while n < (2<<30):\n"
        "   f.write(chunk); n+=len(chunk)\n"
        " print('NO_LIMIT'); sys.exit(1)\n"
        "except OSError as e:\n"
        " print(errno.errorcode[e.errno], n)\n"
        "finally:\n"
        " os.path.exists(p) and os.unlink(p)\n",
        timeout=120,
    )
    code, written = result.stdout.split()
    assert code == "ENOSPC", result.stderr[-500:]
    assert int(written) <= 256 << 20  # INGEST_TMPFS_BYTES provisional (256 MiB)
    leftover = _run("docker", "exec", container, "ls", "-A", TMP_DIR).stdout.split()
    assert "fill-probe" not in leftover


def test_crash_leaves_no_core_dump(container: str) -> None:
    # En el host `core_pattern` entrega los volcados a systemd-coredump, que los descarta
    # cuando el límite de core del proceso es 0. Se cuenta lo que llega para UID 10001.
    if not HOST_COREDUMPS.is_dir():
        pytest.skip("el host no usa systemd-coredump")

    def dumps() -> set[str]:
        return {p.name for p in HOST_COREDUMPS.iterdir() if f".{INGEST_UID}." in p.name}

    before = dumps()
    result = _py(container, "import os,signal;os.kill(os.getpid(),signal.SIGSEGV)")
    assert result.returncode in {-11, 139}
    time.sleep(3)
    assert dumps() == before
    leftover = _run("docker", "exec", container, "ls", "-A", TMP_DIR, "/app").stdout
    assert "core" not in leftover.split()


@pytest.fixture
def restarted(container: str) -> Iterator[str]:
    yield container
    _wait_healthy(container)


def test_temporaries_vanish_on_container_restart(restarted: str) -> None:
    marker = f"{TMP_DIR}/restart-marker"
    assert _run("docker", "exec", restarted, "touch", marker).returncode == 0
    assert _run("docker", "restart", "-t", "10", restarted, timeout=60).returncode == 0
    _wait_healthy(restarted)
    assert _run("docker", "exec", restarted, "test", "-e", marker).returncode == 1
