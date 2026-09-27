"""A1.2: configuración fail-fast y sin secretos expuestos."""
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings

SECRETS: dict[str, Any] = {
    "secret_key": "s3cret-value-for-test",
    "encryption_key": "enc-value-for-test",
    "oauth_state_secret": "state-value-for-test",
}


PROD_URLS: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://mw:pw@db.internal:5432/maulwurf",
    "redis_url": "redis://cache.internal:6379/0",
    "public_origin": "https://maulwurf.example",
}


def _settings(**overrides: Any) -> Settings:
    # _env_file=None: el resultado no depende del .env de quien corre los tests.
    return Settings(_env_file=None, **{**SECRETS, **overrides})


def _prod(**overrides: Any) -> Settings:
    return _settings(env="prod", **{**PROD_URLS, **overrides})


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
        _prod(**{field: "change-me-please"})


def test_change_me_placeholder_allowed_in_local() -> None:
    assert _settings(secret_key="change-me").env == "local"  # noqa: S106 - valor ficticio


def test_public_origin_requires_https_outside_local() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings(env="staging", **{**PROD_URLS, "public_origin": "http://maulwurf.example"})


def test_allowed_origin_has_no_trailing_slash() -> None:
    assert _settings(public_origin="https://localhost:8443").allowed_origin == (
        "https://localhost:8443"
    )


def test_repr_and_dump_never_expose_secret_values() -> None:
    settings = _settings(google_client_id="client-id", google_client_secret="google-value-for-test")  # noqa: S106
    rendered = f"{settings!r} {settings} {settings.model_dump()} {settings.model_dump_json()}"

    for value in [*SECRETS.values(), "google-value-for-test"]:
        assert value not in rendered


def test_unknown_env_name_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(env="production")


def test_unknown_maulwurf_key_in_dotenv_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MAULWURF_REDIS_ULR=redis://typo\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="MAULWURF_REDIS_ULR"):
        Settings(_env_file=".env", **SECRETS)


def test_dotenv_keys_of_other_services_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El .env compartido con Compose trae variables de postgres, Riva e ingesta.
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=x\nNVIDIA_API_KEY=x\nINGEST_SLOTS=2\nMAULWURF_ENV=local\n"
    )
    monkeypatch.chdir(tmp_path)

    assert Settings(_env_file=".env", **SECRETS).env == "local"


def test_validation_errors_do_not_echo_the_invalid_value() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(database_url="postgre://maulwurf:Sup3rSecretPw@db/maulwurf")

    assert "Sup3rSecretPw" not in str(excinfo.value)


def test_empty_secret_rejected_outside_local() -> None:
    with pytest.raises(ValidationError, match="secret_key vacío"):
        _prod(secret_key="   ")  # noqa: S106


@pytest.mark.parametrize(
    "origin", ["https://localhost/app", "https://localhost/?x=1", "https://localhost#frag"]
)
def test_public_origin_rejects_path_and_query(origin: str) -> None:
    with pytest.raises(ValidationError, match="path, query ni fragment"):
        _settings(public_origin=origin)


@pytest.mark.parametrize("field", ["database_url", "redis_url", "public_origin"])
def test_dev_defaults_rejected_outside_local(field: str) -> None:
    urls = {k: v for k, v in PROD_URLS.items() if k != field}

    with pytest.raises(ValidationError, match=f"{field} conserva el valor de desarrollo"):
        _settings(env="prod", **urls)


def test_prod_with_explicit_values_is_accepted() -> None:
    assert _prod().env == "prod"


def test_default_https_port_normalizes_to_bare_origin() -> None:
    # Compose puede componer https://localhost:${CADDY_HTTPS_PORT:-443}; el navegador envía
    # el Origin sin el puerto por defecto.
    assert _settings(public_origin="https://localhost:443").allowed_origin == "https://localhost"


def test_empty_env_value_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAULWURF_GOOGLE_CLIENT_ID", "")

    assert _settings().google_client_id is None


def test_oauth_client_id_and_secret_go_together() -> None:
    with pytest.raises(ValidationError, match="van juntos"):
        _settings(google_client_id="client-id-only")


def test_unknown_key_scan_follows_the_effective_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("MAULWURF_EXPERIMENTAL=1\n")
    monkeypatch.chdir(tmp_path)

    assert _settings().env == "local"  # _env_file=None: ese .env no participa
