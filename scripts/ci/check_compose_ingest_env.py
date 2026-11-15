"""I-S2-JF-11: the environment that `docker compose config` gives `ingest` and `api`.

Renders the compose file from a scratch copy whose sibling `.env` holds dummy values, so the
developer's real `../.env` is never read. Prints variable names and pass/fail only, never values.
Requires the `docker` CLI with the Compose plugin; it does not start containers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
INGEST_LIMIT_NAMES = ("INGEST_MAX_UPLOAD_BYTES", "INGEST_FRAGMENT_OVERLAP_S", "INGEST_FRAGMENT_SECONDS")
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
INGEST_SECRET_NAME = "MAULWURF_INGEST_CREDENTIAL_SECRET"
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
        result = subprocess.run(
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


_BYTE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1000,
    "ki": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1000**2,
    "mi": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1000**3,
    "gi": 1024**3,
    "gib": 1024**3,
}


def _as_bytes(value: object) -> int | None:
    """Compose renders sizes as ints or as `1g` / `268435456` strings."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", value.strip().lower())
    if match is None:
        return None
    number, unit = match.groups()
    multiplier = _BYTE_UNITS.get(unit)
    if multiplier is None:
        return None
    return int(float(number) * multiplier)


def _memory_limit(service: Mapping[str, Any]) -> object:
    if service.get("mem_limit") is not None:
        return service.get("mem_limit")
    deploy = service.get("deploy")
    if not isinstance(deploy, dict):
        return None
    resources = deploy.get("resources")
    if not isinstance(resources, dict):
        return None
    limits = resources.get("limits")
    if not isinstance(limits, dict):
        return None
    return limits.get("memory")


def _core_limits(service: Mapping[str, Any]) -> tuple[int, int] | None:
    ulimits = service.get("ulimits")
    if not isinstance(ulimits, dict):
        return None
    core = ulimits.get("core")
    if isinstance(core, int):
        return (core, core)
    if isinstance(core, dict):
        soft = core.get("soft", core.get("Soft"))
        hard = core.get("hard", core.get("Hard"))
        if isinstance(soft, int) and isinstance(hard, int):
            return (soft, hard)
    return None


def _tmpfs_mount(service: Mapping[str, Any]) -> tuple[set[str], int | None] | None:
    """Options and size of the `/work/tmp` tmpfs, or None when that mount is absent."""
    raw = service.get("tmpfs")
    items: list[object] = raw if isinstance(raw, list) else [raw]
    for item in items:
        if isinstance(item, str):
            target, _, option_text = item.partition(":")
            if target != TMP_MOUNT:
                continue
            options = {part for part in option_text.split(",") if part}
            size = next(
                (_as_bytes(part.split("=", 1)[1]) for part in options if part.startswith("size=")),
                None,
            )
            return options, size
        if isinstance(item, dict) and item.get("target") == TMP_MOUNT:
            nested = item.get("tmpfs") if isinstance(item.get("tmpfs"), dict) else {}
            option_text = item.get("options")
            if not isinstance(option_text, str):
                option_text = nested.get("options") if isinstance(nested, dict) else ""
            options = {part for part in str(option_text or "").split(",") if part}
            size_value: object = nested.get("size") if isinstance(nested, dict) else None
            if size_value is None:
                size_value = next(
                    (part.split("=", 1)[1] for part in options if part.startswith("size=")),
                    None,
                )
            return options, _as_bytes(size_value)
    return None


def _writable_volumes(service: Mapping[str, Any]) -> list[str]:
    raw = service.get("volumes") or []
    if not isinstance(raw, list):
        return [str(raw)]
    writable: list[str] = []
    for volume in raw:
        if isinstance(volume, str):
            if not volume.endswith(":ro"):
                writable.append(volume)
            continue
        if isinstance(volume, dict):
            if volume.get("read_only") is True:
                continue
            target = volume.get("target") or volume.get("destination") or volume
            writable.append(str(target))
            continue
        writable.append(str(volume))
    return writable


def check_mount_contract(report: Report, model: Mapping[str, Any], label: str) -> None:
    """Tmpfs, read-only root, no writable volume, no extra swap, no core dumps.

    The tmpfs size is compared with the rendered `INGEST_MAX_UPLOAD_BYTES`. Neither
    number is copied into this file, so the two compose defaults cannot drift apart
    without this check failing.
    """
    service = model["services"]["ingest"]
    env = service_env(model, "ingest")
    report.check(service.get("read_only") is True, f"{label}: ingest root is read-only")
    report.check(
        env.get("TMPDIR") == TMP_MOUNT,
        f"{label}: TMPDIR is {TMP_MOUNT}",
    )
    mount = _tmpfs_mount(service)
    if mount is None:
        report.check(False, f"{label}: tmpfs mounted at {TMP_MOUNT}")
        return
    options, size = mount
    report.check(
        TMP_FLAGS <= options,
        f"{label}: tmpfs has noexec,nosuid,nodev (options: {sorted(options)})",
    )
    upload_text = env.get("INGEST_MAX_UPLOAD_BYTES")
    upload = int(upload_text) if isinstance(upload_text, str) and upload_text.isdigit() else None
    report.check(
        size is not None and upload is not None and size > upload,
        f"{label}: tmpfs size ({size}) exceeds the upload limit ({upload})",
    )
    writable = _writable_volumes(service)
    report.check(not writable, f"{label}: ingest has no writable volume ({writable or 'none'})")
    memory = _as_bytes(_memory_limit(service))
    swap = _as_bytes(service.get("memswap_limit"))
    report.check(
        memory is not None and swap is not None and swap == memory,
        f"{label}: memswap_limit equals mem_limit ({swap} vs {memory})",
    )
    report.check(
        _core_limits(service) == (0, 0),
        f"{label}: core ulimit is 0",
    )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE)
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("FAIL docker CLI not found", file=sys.stderr)
        return 1
    try:
        report = run_checks(args.compose_file.resolve())
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
