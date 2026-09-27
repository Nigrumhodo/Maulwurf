"""A1.2: configuración fail-fast y sin secretos expuestos."""
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

SECRETS = {
    "secret_key": "s3cret-value-for-test",
    "encryption_key": "enc-value-for-test",
    "oauth_state_secret": "state-value-for-test",
}


def _settings(**overrides: Any) -> Settings:
    # _env_file=None: el resultado no depende del .env de quien corre los tests.
    return Settings(_env_file=None, **{**SECRETS, **overrides})


@pytest.mark.parametrize("missing", sorted(SECRETS))
def test_missing_required_secret_fails_fast(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(f"MAULWURF_{missing.upper()}", raising=False)
    values: dict[str, Any] = {k: v for k, v in SECRETS.items() if k != missing}

    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)


def test_unknown_maulwurf_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAULWURF_DATABSE_URL", "postgresql+asyncpg://typo/db")

    with pytest.raises(ValidationError, match="MAULWURF_DATABSE_URL"):
        _settings()


@pytest.mark.parametrize("field", ["secret_key", "encryption_key", "google_client_secret"])
def test_change_me_placeholder_rejected_outside_local(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _settings(env="prod", **{field: "change-me-please"})


def test_change_me_placeholder_allowed_in_local() -> None:
    assert _settings(secret_key="change-me").env == "local"


def test_public_origin_requires_https_outside_local() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings(env="staging", public_origin="http://maulwurf.example")


def test_allowed_origin_has_no_trailing_slash() -> None:
    assert _settings(public_origin="https://localhost:8443").allowed_origin == (
        "https://localhost:8443"
    )


def test_repr_and_dump_never_expose_secret_values() -> None:
    settings = _settings(google_client_secret="google-value-for-test")
    rendered = f"{settings!r} {settings} {settings.model_dump()} {settings.model_dump_json()}"

    for value in [*SECRETS.values(), "google-value-for-test"]:
        assert value not in rendered


def test_unknown_env_name_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(env="production")
