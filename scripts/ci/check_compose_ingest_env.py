"""I-S2-JF-11: the environment that `docker compose config` gives `ingest` and `api`.

Renders the compose file from a scratch copy whose sibling `.env` holds dummy values, so the
developer's real `../.env` is never read. Prints variable names and pass/fail only, never values.
Requires the `docker` CLI with the Compose plugin; it does not start containers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
DEFAULT_E2E = REPO_ROOT / "infra" / "docker-compose.e2e.yml"
DEFAULT_PROD = REPO_ROOT / "infra" / "docker-compose.prod.yml"
RENDER_TIMEOUT_S = 120
TMP_MOUNT = "/work/tmp"
TMP_FLAGS = frozenset({"noexec", "nosuid", "nodev"})

INGEST_ENV_ALLOWLIST = frozenset(
    {
        "MAULWURF_ENV",
        "MAULWURF_DATABASE_URL",
        "INGEST_CREDENTIAL_SECRET",
        "INGEST_CREDENTIAL_KID",
        "INGEST_CREDENTIAL_PREVIOUS_SECRET",
        "INGEST_CREDENTIAL_PREVIOUS_KID",
        "INGEST_MAX_UPLOAD_BYTES",
        "INGEST_FRAGMENT_OVERLAP_S",
        "INGEST_FRAGMENT_SECONDS",
        "NVIDIA_API_KEY",
        "RIVA_SERVER",
        "RIVA_FUNCTION_ID",
        "RIVA_USE_SSL",
        "TMPDIR",
    }
)
INGEST_RIVA_NAMES = ("NVIDIA_API_KEY", "RIVA_SERVER", "RIVA_FUNCTION_ID", "RIVA_USE_SSL")
INGEST_LIMIT_NAMES = (
    "INGEST_MAX_UPLOAD_BYTES",
    "INGEST_FRAGMENT_OVERLAP_S",
    "INGEST_FRAGMENT_SECONDS",
)
API_SECRET_NAMES = (
    "MAULWURF_SECRET_KEY",
    "MAULWURF_ENCRYPTION_KEY",
    "MAULWURF_OAUTH_STATE_SECRET",
    "MAULWURF_GOOGLE_CLIENT_ID",
    "MAULWURF_GOOGLE_CLIENT_SECRET",
    "MAULWURF_EMBEDDING_API_KEY",
    "MAULWURF_LLM_API_KEY",
    "POSTGRES_PASSWORD",
)
INGEST_SECRET_NAME = "MAULWURF_INGEST_CREDENTIAL_SECRET"  # noqa: S105
INGEST_URL_NAME = "MAULWURF_INGEST_URL"
INGEST_ORIGIN_DEFAULT = "https://localhost:443"
INGEST_USER = "10001:10001"

DUMMY_INGEST_SECRET = "ab" * 32
DUMMY_ENV: dict[str, str] = {
    "MAULWURF_SECRET_KEY": "dummy-session-key",
    "MAULWURF_ENCRYPTION_KEY": "dummy-encryption-key",
    "MAULWURF_OAUTH_STATE_SECRET": "dummy-oauth-state",
    "MAULWURF_GOOGLE_CLIENT_ID": "dummy-google-id",
    "MAULWURF_GOOGLE_CLIENT_SECRET": "dummy-google-secret",
    "MAULWURF_EMBEDDING_API_KEY": "dummy-embedding-key",
    "MAULWURF_LLM_API_KEY": "dummy-llm-key",
    "POSTGRES_PASSWORD": "dummy-postgres",
    # Required (`:?`) by the production compose file only.
    "MAULWURF_DOMAIN": "dummy.example.com",
    "MAULWURF_IMAGE_TAG": "dummy-tag",
    "MAULWURF_METRICS_TOKEN": "dummy-metrics-token",
    "REDIS_PASSWORD": "dummy-redis",
    "NVIDIA_API_KEY": "dummy-nvidia-key",
    "RIVA_SERVER": "dummy-riva.example:443",
    "RIVA_FUNCTION_ID": "dummy-function-id",
    "RIVA_USE_SSL": "true",
    INGEST_SECRET_NAME: DUMMY_INGEST_SECRET,
    INGEST_URL_NAME: "http://ingest:8000",
    "INGEST_CREDENTIAL_PREVIOUS_SECRET": "cd" * 32,
    "INGEST_CREDENTIAL_PREVIOUS_KID": "k1",
}


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)

    def check(self, passed: bool, label: str) -> None:
        print(f"{'PASS' if passed else 'FAIL'} {label}")
        if not passed:
            self.failures.append(label)


def render(
    compose_files: Path | tuple[Path, ...], overrides: Mapping[str, str | None]
) -> dict:
    """Render the compose model with `overrides` applied; a None value removes the name.

    `compose_files` is one file or a merge (`-f` in order). The copies live in a scratch
    directory so the developer's real `../.env` is never read.
    """
    files = (compose_files,) if isinstance(compose_files, Path) else compose_files
    values = {**DUMMY_ENV}
    for name, value in overrides.items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    with tempfile.TemporaryDirectory(dir=os.environ.get("KIROCREW_SCRATCH")) as scratch:
        root = Path(scratch)
        infra = root / "infra"
        infra.mkdir()
        copied: list[Path] = []
        for compose_file in files:
            destination = infra / compose_file.name
            shutil.copyfile(compose_file, destination)
            copied.append(destination)
        env_text = "".join(f"{name}={value}\n" for name, value in values.items())
        (root / ".env").write_text(env_text)
        env_file = root / "interpolation.env"
        env_file.write_text(env_text)
        command = ["docker", "compose"]
        for destination in copied:
            command.extend(["-f", str(destination)])
        command.extend(["--env-file", str(env_file), "config", "--format", "json"])
        result = subprocess.run(  # noqa: S603
            command,
            env=_clean_process_env(),
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_S,
            check=False,
        )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        raise RuntimeError(
            f"docker compose config failed (exit {result.returncode}): {detail}"
        )
    return json.loads(result.stdout)


def _clean_process_env() -> dict[str, str]:
    """Only what docker needs; the caller's shell must not leak secrets into the render."""
    keep = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    return {name: os.environ[name] for name in keep if name in os.environ}


def service_env(model: Mapping, service: str) -> dict[str, str | None]:
    raw = model["services"][service].get("environment") or {}
    return dict(raw)


def check_ingest(report: Report, model: Mapping, *, all_set: bool) -> None:
    env = service_env(model, "ingest")
    names = set(env)
    extra = sorted(names - INGEST_ENV_ALLOWLIST)
    report.check(not extra, f"ingest env has only allowlisted names (extra: {extra or 'none'})")
    for name in INGEST_RIVA_NAMES + INGEST_LIMIT_NAMES + ("INGEST_CREDENTIAL_SECRET",):
        report.check(name in names, f"ingest declares {name}")
    leaked = sorted(name for name in API_SECRET_NAMES if name in names)
    report.check(not leaked, f"ingest has no API secret names (found: {leaked or 'none'})")
    report.check(
        "env_file" not in model["services"]["ingest"],
        "ingest has no env_file (whole .env is never passed)",
    )
    report.check(
        str(model["services"]["ingest"].get("user")) == INGEST_USER,
        f"ingest runs as {INGEST_USER}",
    )
    replicas = model["services"]["ingest"].get("deploy", {}).get("replicas")
    report.check(replicas == 1, "ingest deploy.replicas is 1")
    ingest_networks = set(model["services"]["ingest"].get("networks") or {})
    report.check("edge" not in ingest_networks, "ingest is not on the edge network")
    caddy_depends = set(model["services"]["caddy"].get("depends_on") or {})
    report.check("ingest" not in caddy_depends, "caddy does not depend on ingest")
    if all_set:
        ingest_secret = env.get("INGEST_CREDENTIAL_SECRET")
        report.check(
            ingest_secret == DUMMY_INGEST_SECRET,
            "ingest INGEST_CREDENTIAL_SECRET comes from MAULWURF_INGEST_CREDENTIAL_SECRET",
        )
        report.check(
            env.get("NVIDIA_API_KEY") is not None,
            "NVIDIA_API_KEY reaches ingest when set",
        )
        report.check(
            env.get("INGEST_CREDENTIAL_PREVIOUS_KID") == "k1",
            "the optional previous credential kid reaches ingest during rotation",
        )


def check_nvidia_has_no_default(report: Report, compose_file: Path) -> None:
    model = render(compose_file, {"NVIDIA_API_KEY": None})
    value = service_env(model, "ingest").get("NVIDIA_API_KEY")
    report.check(value is None, "NVIDIA_API_KEY has no default (absent when unset)")


def check_api_ingest_pair(report: Report, model: Mapping, *, expect_set: bool) -> None:
    env = service_env(model, "api")
    url = env.get(INGEST_URL_NAME)
    secret = env.get(INGEST_SECRET_NAME)
    if expect_set:
        report.check(url == "http://ingest:8000", f"api gets {INGEST_URL_NAME} from env")
        report.check(
            secret == DUMMY_INGEST_SECRET,
            f"api gets {INGEST_SECRET_NAME} from env",
        )
    else:
        report.check(
            url is None and secret is None,
            "api omits the ingest pair when unset (never an empty secret next to a URL)",
        )


def _byte_count(value: object) -> int | None:
    """`UnitBytes` JSON is a decimal string (`"1073741824"`), never `1g` or a number."""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _tmpfs_mount(service: Mapping[str, Any]) -> tuple[set[str], int] | None:
    """The one short-syntax string compose-go keeps in `tmpfs`.

    Anything else — a second mount, an object, a unit suffix — is a different
    shape and fails closed.
    """
    raw = service.get("tmpfs")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], str):
        return None
    target, separator, option_text = raw[0].partition(":")
    if separator != ":" or target != TMP_MOUNT:
        return None
    options = {part for part in option_text.split(",") if part}
    sizes = [part.removeprefix("size=") for part in options if part.startswith("size=")]
    if len(sizes) != 1 or not sizes[0].isdigit():
        return None
    return options, int(sizes[0])


def _writable_targets(service: Mapping[str, Any]) -> list[str] | None:
    """Targets of volumes that are not read-only objects.

    compose-go omits `read_only: false`, so a missing flag is writable. `None`
    means the value is not the object list `config` emits (no volumes is `[]`
    after a missing key).
    """
    raw = service.get("volumes")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    writable: list[str] = []
    for volume in raw:
        if not isinstance(volume, dict) or not isinstance(volume.get("target"), str):
            return None
        if volume.get("read_only") is not True:
            writable.append(volume["target"])
    return writable


def _core_is_zero(service: Mapping[str, Any]) -> bool:
    """YAML `core: 0` marshals as `{}`: compose-go omits zero ulimit fields."""
    ulimits = service.get("ulimits")
    if not isinstance(ulimits, dict):
        return False
    return ulimits.get("core") == {}


def check_mount_contract(report: Report, model: Mapping[str, Any], label: str) -> None:
    """Tmpfs, read-only root, no writable volume, no extra swap, no core dumps.

    The tmpfs size is compared with the rendered `INGEST_MAX_UPLOAD_BYTES`. Neither
    number is copied into this file, so the two compose defaults cannot drift apart
    without this check failing.
    """
    service = model["services"]["ingest"]
    if not isinstance(service, dict):
        report.check(False, f"{label}: ingest service is an object")
        return
    env = service_env(model, "ingest")
    report.check(service.get("read_only") is True, f"{label}: ingest root is read-only")
    report.check(env.get("TMPDIR") == TMP_MOUNT, f"{label}: TMPDIR is {TMP_MOUNT}")
    mount = _tmpfs_mount(service)
    if mount is None:
        report.check(False, f"{label}: tmpfs is one {TMP_MOUNT} string")
        report.check(False, f"{label}: tmpfs size exceeds the upload limit")
    else:
        options, size = mount
        report.check(
            TMP_FLAGS <= options,
            f"{label}: tmpfs has noexec,nosuid,nodev (options: {sorted(options)})",
        )
        upload_text = env.get("INGEST_MAX_UPLOAD_BYTES")
        upload = (
            int(upload_text)
            if isinstance(upload_text, str) and upload_text.isdigit()
            else None
        )
        report.check(
            upload is not None and size > upload,
            f"{label}: tmpfs size ({size}) exceeds the upload limit ({upload})",
        )
    writable = _writable_targets(service)
    report.check(
        writable == [],
        f"{label}: ingest has no writable volume ({writable})",
    )
    memory = _byte_count(service.get("mem_limit"))
    swap = _byte_count(service.get("memswap_limit"))
    report.check(
        memory is not None and memory == swap,
        f"{label}: memswap_limit equals mem_limit ({swap} vs {memory})",
    )
    report.check(_core_is_zero(service), f"{label}: core ulimit is 0")


def check_origin(report: Report, compose_file: Path) -> None:
    default_env = service_env(render(compose_file, {"MAULWURF_PUBLIC_ORIGIN": None}), "api")
    report.check(
        default_env.get("MAULWURF_PUBLIC_ORIGIN") == INGEST_ORIGIN_DEFAULT,
        "MAULWURF_PUBLIC_ORIGIN defaults to https://localhost:443",
    )
    port_env = service_env(
        render(compose_file, {"MAULWURF_PUBLIC_ORIGIN": None, "CADDY_HTTPS_PORT": "8443"}),
        "api",
    )
    report.check(
        port_env.get("MAULWURF_PUBLIC_ORIGIN") == "https://localhost:8443",
        "MAULWURF_PUBLIC_ORIGIN follows CADDY_HTTPS_PORT",
    )
    override = "https://override.example:9443"
    overridden = service_env(
        render(compose_file, {"MAULWURF_PUBLIC_ORIGIN": override}), "api"
    )
    report.check(
        overridden.get("MAULWURF_PUBLIC_ORIGIN") == override,
        "MAULWURF_PUBLIC_ORIGIN from .env overrides the default",
    )


def run_checks(compose_file: Path) -> Report:
    report = Report()
    full = render(compose_file, {})
    check_ingest(report, full, all_set=True)
    check_api_ingest_pair(report, full, expect_set=True)

    check_nvidia_has_no_default(report, compose_file)

    without_ingest_pair = render(
        compose_file,
        {INGEST_SECRET_NAME: None, INGEST_URL_NAME: None},
    )
    check_api_ingest_pair(report, without_ingest_pair, expect_set=False)

    check_origin(report, compose_file)
    check_mount_contract(report, full, "base")
    merged = render(
        (compose_file, DEFAULT_E2E),
        {"E2E_ALLOW_EPHEMERAL_CREDENTIALS": "1"},
    )
    check_mount_contract(report, merged, "e2e")
    return report


def run_prod_checks(prod_file: Path) -> Report:
    report = Report()
    model = render(prod_file, {})
    check_ingest(report, model, all_set=True)
    check_api_ingest_pair(report, model, expect_set=True)
    check_mount_contract(report, model, "prod")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--prod-compose-file", type=Path, default=DEFAULT_PROD)
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("FAIL docker CLI not found", file=sys.stderr)
        return 1
    try:
        report = run_checks(args.compose_file.resolve())
        prod_report = run_prod_checks(args.prod_compose_file.resolve())
        report.failures.extend(prod_report.failures)
    except (RuntimeError, subprocess.SubprocessError, json.JSONDecodeError, KeyError) as error:
        print(f"FAIL {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if report.failures:
        print(f"{len(report.failures)} check(s) failed", file=sys.stderr)
        return 1
    print("I-S2-JF-11 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
