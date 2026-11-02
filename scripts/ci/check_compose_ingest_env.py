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

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
RENDER_TIMEOUT_S = 120

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


def render(compose_file: Path, overrides: Mapping[str, str | None]) -> dict:
    """Render the compose model with `overrides` applied; a None value removes the name."""
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
        shutil.copyfile(compose_file, infra / "docker-compose.yml")
        env_text = "".join(f"{name}={value}\n" for name, value in values.items())
        (root / ".env").write_text(env_text)
        env_file = root / "interpolation.env"
        env_file.write_text(env_text)
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", str(infra / "docker-compose.yml"),
                "--env-file", str(env_file),
                "config", "--format", "json",
            ],
            env=_clean_process_env(),
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_S,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed (exit {result.returncode})")
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
