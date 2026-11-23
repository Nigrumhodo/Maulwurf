"""I-S2-JF-11: the environment that `docker compose config` gives `ingest` and `api`.

Renders the compose file from a scratch copy whose sibling `.env` holds dummy values, so the
developer's real `../.env` is never read. Prints variable names and pass/fail only, never values.
Requires the `docker` CLI with the Compose plugin; it does not start containers.

The default invocation checks the dev file (including the e2e merge) and
``infra/docker-compose.prod.yml``. ``--compose-file`` checks that one file.
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
EDGE_SUBNET = "172.28.10.0/24"
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
        "INGEST_SLOTS",
        "INGEST_RECEPTION_TIMEOUT_S",
        "INGEST_FRAGMENT_OVERLAP_S",
        "INGEST_FRAGMENT_SECONDS",
        "NVIDIA_API_KEY",
        "RIVA_SERVER",
        "RIVA_FUNCTION_ID",
        "RIVA_USE_SSL",
        "TMPDIR",
        "INGEST_METRICS_TOKEN",
    }
)
INGEST_RIVA_NAMES = ("NVIDIA_API_KEY", "RIVA_SERVER", "RIVA_FUNCTION_ID", "RIVA_USE_SSL")
APP_SERVICES = ("api", "worker", "scheduler")
HARDENED_PROD_SERVICES = ("caddy", "postgres", "redis", "prometheus")
READ_ONLY_PROD_SERVICES = ("caddy", "redis", "prometheus")
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
PROD_DUMMY: dict[str, str] = {
    "REDIS_PASSWORD": "dummy-redis",
    "MAULWURF_DOMAIN": "maulwurf.example",
    "MAULWURF_IMAGE_TAG": "ci",
    "MAULWURF_METRICS_TOKEN": "dummy-metrics-token",
    "INGEST_METRICS_TOKEN": "dummy-ingest-metrics-token",
    "DATABASE_URL_APP": (
        "postgresql+asyncpg://maulwurf_app_login:dummy-app@postgres:5432/maulwurf"
    ),
    "DATABASE_URL_INGEST": (
        "postgresql+asyncpg://maulwurf_ingest_login:dummy-ingest@postgres:5432/maulwurf"
    ),
    "DATABASE_URL_JOBS": (
        "postgresql+asyncpg://maulwurf_jobs_login:dummy-jobs@postgres:5432/maulwurf"
    ),
}


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)

    def check(self, passed: bool, label: str) -> None:
        print(f"{'PASS' if passed else 'FAIL'} {label}")
        if not passed:
            self.failures.append(label)


def render(
    compose_files: Path | tuple[Path, ...],
    overrides: Mapping[str, str | None],
    *,
    profiles: tuple[str, ...] = (),
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
        for profile in profiles:
            command.extend(["--profile", profile])
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


def check_riva_not_on_app_services(report: Report, model: Mapping[str, Any], label: str) -> None:
    """F6-13: NVIDIA/Riva material stays on ingest. Empty overrides env_file; a value does not."""
    services = model["services"]
    for name in APP_SERVICES:
        env = service_env(model, name) if name in services else {}
        leaked = sorted(key for key in INGEST_RIVA_NAMES if env.get(key))
        report.check(
            not leaked,
            f"{label}: {name} has no NVIDIA/Riva secrets (found: {leaked or 'none'})",
        )


def _command_text(service: Mapping[str, Any]) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, str):
        return command
    return ""


def _positive_limit(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and value.isdigit() and int(value) > 0


def check_log_rotation(report: Report, model: Mapping[str, Any], label: str) -> None:
    for name, service in model["services"].items():
        if not isinstance(service, dict):
            report.check(False, f"{label}: {name} logging rotates json-file at 10m x 5")
            continue
        logging = service.get("logging") or {}
        options = logging.get("options") if isinstance(logging, dict) else {}
        options = options or {}
        report.check(
            isinstance(logging, dict)
            and logging.get("driver") == "json-file"
            and options.get("max-size") == "10m"
            and str(options.get("max-file")) == "5",
            f"{label}: {name} logging rotates json-file at 10m x 5",
        )


def check_prod_hardening(report: Report, model: Mapping[str, Any]) -> None:
    services = model["services"]
    for name in HARDENED_PROD_SERVICES:
        service = services[name]
        cap_drop = [str(item).upper() for item in (service.get("cap_drop") or [])]
        cap_add = [str(item).upper() for item in (service.get("cap_add") or [])]
        security = service.get("security_opt") or []
        report.check("ALL" in cap_drop, f"prod: {name} drops all capabilities")
        report.check(
            "no-new-privileges:true" in security,
            f"prod: {name} sets no-new-privileges",
        )
        report.check(_positive_limit(service.get("pids_limit")), f"prod: {name} sets pids_limit")
        report.check("NET_RAW" not in cap_add, f"prod: {name} does not add NET_RAW")
    for name in READ_ONLY_PROD_SERVICES:
        report.check(
            services[name].get("read_only") is True,
            f"prod: {name} root is read-only",
        )
    redis_command = _command_text(services["redis"])
    report.check(
        "--requirepass" not in redis_command,
        "prod: redis command has no --requirepass argv",
    )
    api_command = _command_text(services["api"])
    report.check(
        "10.0.0.0/8" not in api_command and "192.168.0.0/16" not in api_command,
        "prod: forwarded-allow-ips is not every private range",
    )
    report.check(EDGE_SUBNET in api_command or "UVICORN_FORWARDED_ALLOW_IPS" in api_command, (
        "prod: forwarded-allow-ips stays on the edge subnet"
    ))
    ingest_env = service_env(model, "ingest")
    metrics = ingest_env.get("INGEST_METRICS_TOKEN")
    credential = ingest_env.get("INGEST_CREDENTIAL_SECRET")
    report.check(
        bool(metrics) and metrics != credential,
        "prod: INGEST_METRICS_TOKEN is set and distinct from the ingest credential",
    )
    migrate_url = str(service_env(model, "migrate").get("MAULWURF_DATABASE_URL") or "")
    report.check(
        migrate_url.startswith("postgresql") and "maulwurf_app_login" not in migrate_url,
        "prod: migrate keeps the owner DSN",
    )
    report.check(
        "maulwurf_app_login" in str(service_env(model, "api").get("MAULWURF_DATABASE_URL") or ""),
        "prod: api uses DATABASE_URL_APP",
    )
    report.check(
        "maulwurf_ingest_login"
        in str(service_env(model, "ingest").get("MAULWURF_DATABASE_URL") or ""),
        "prod: ingest uses DATABASE_URL_INGEST",
    )
    report.check(
        "maulwurf_jobs_login" in str(service_env(model, "worker").get("MAULWURF_DATABASE_URL") or ""),
        "prod: worker uses DATABASE_URL_JOBS",
    )
    report.check(
        "maulwurf_jobs_login"
        in str(service_env(model, "scheduler").get("MAULWURF_DATABASE_URL") or ""),
        "prod: scheduler uses DATABASE_URL_JOBS",
    )


def _is_prod(compose_file: Path) -> bool:
    return compose_file.name == "docker-compose.prod.yml"


def run_dev_checks(compose_file: Path) -> Report:
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
    check_riva_not_on_app_services(report, full, "base")
    check_log_rotation(report, full, "base")
    merged = render(
        (compose_file, DEFAULT_E2E),
        {"E2E_ALLOW_EPHEMERAL_CREDENTIALS": "1"},
    )
    check_mount_contract(report, merged, "e2e")
    check_riva_not_on_app_services(report, merged, "e2e")
    check_log_rotation(report, merged, "e2e")
    return report


def run_prod_checks(compose_file: Path) -> Report:
    report = Report()
    full = render(compose_file, PROD_DUMMY, profiles=("observability",))
    check_ingest(report, full, all_set=True)
    check_api_ingest_pair(report, full, expect_set=True)
    check_mount_contract(report, full, "prod")
    check_riva_not_on_app_services(report, full, "prod")
    check_log_rotation(report, full, "prod")
    check_prod_hardening(report, full)
    return report


def run_checks(compose_file: Path) -> Report:
    """Backward-compatible entry: one file, dev or prod by name."""
    if _is_prod(compose_file):
        return run_prod_checks(compose_file)
    return run_dev_checks(compose_file)


def _emit(report: Report) -> int:
    if report.failures:
        print(f"{len(report.failures)} check(s) failed", file=sys.stderr)
        return 1
    print("I-S2-JF-11 passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compose-file", type=Path, default=None)
    parser.add_argument("--prod-compose-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if shutil.which("docker") is None:
        print("FAIL docker CLI not found", file=sys.stderr)
        return 1
    targets: list[Path] = []
    if args.compose_file is not None:
        targets.append(args.compose_file.resolve())
    if args.prod_compose_file is not None:
        targets.append(args.prod_compose_file.resolve())
    if not targets:
        targets = [DEFAULT_COMPOSE, DEFAULT_PROD]
    failed = 0
    for compose_file in targets:
        print(f"compose file: {compose_file}")
        try:
            report = run_checks(compose_file)
        except (RuntimeError, subprocess.SubprocessError, json.JSONDecodeError, KeyError) as error:
            print(f"FAIL {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        failed += _emit(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
