"""A1.2: configuración fail-fast y sin secretos expuestos."""
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import Field, RedisDsn, ValidationError

from app.core.config import Settings

SECRETS: dict[str, Any] = {
    "secret_key": "s2-test-secret-key-0123456789abcdef",
    "encryption_key": "s2-test-encryption-key-0123456789ab",
    "oauth_state_secret": "s2-test-oauth-state-secret-0123456789",
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
    assert _settings(secret_key="change-me").env == "local"


def test_the_notification_scheduler_is_disabled_by_default() -> None:
    # J4.2 claims into `sending`; J4.1 is the consumer. Without it, claims would expire into
    # a false `delivery_unknown`, so the flag is opt-in only.
    assert _settings().notification_scheduler_enabled is False
    assert (
        _settings(notification_scheduler_enabled=True).notification_scheduler_enabled is True
    )


# --- A2.16 / U-S2-AN-15: fail-closed settings -----------------------------------------


def test_env_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without MAULWURF_ENV the local guard must not be inherited by accident.
    monkeypatch.delenv("MAULWURF_ENV", raising=False)

    with pytest.raises(ValidationError, match="env"):
        Settings(_env_file=None, **SECRETS)


def test_empty_env_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAULWURF_ENV", "")

    with pytest.raises(ValidationError, match="env"):
        Settings(_env_file=None, **SECRETS)


@pytest.mark.parametrize("field", ["secret_key", "encryption_key", "oauth_state_secret"])
def test_weak_secret_rejected_outside_local(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field}.*32"):
        _prod(**{field: "x" * 31})


def test_secret_of_minimum_length_is_accepted_outside_local() -> None:
    assert _prod(secret_key="k" * 32).env == "prod"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("secret_key", "encryption_key"),
        ("secret_key", "oauth_state_secret"),
        ("encryption_key", "oauth_state_secret"),
    ],
)
def test_reused_secrets_rejected_outside_local(first: str, second: str) -> None:
    shared = "shared-secret-value-0123456789abcdef"

    with pytest.raises(ValidationError, match="distintos"):
        _prod(**{first: shared, second: shared})


def test_short_or_reused_secrets_remain_usable_in_local() -> None:
    settings = _settings(
        env="local",
        secret_key="short",
        encryption_key="short",
        oauth_state_secret="short",
    )

    assert settings.env == "local"


@pytest.mark.parametrize(
    ("field", "context"),
    [
        ("google_client_secret", {"google_client_id": "client-id"}),
        (
            "embedding_api_key",
            {"embedding_base_url": "https://api.openai.com/v1"},
        ),
        (
            "llm_api_key",
            {
                "llm_base_url": "https://api.openai.com/v1",
                "llm_model_window": 128_000,
            },
        ),
    ],
)
def test_short_provider_secret_is_accepted_outside_local(
    field: str, context: dict[str, Any]
) -> None:
    # Provider-issued keys keep the provider format; our 32-byte policy is only for the
    # boundary secrets we generate.
    assert _prod(**{**context, field: "short-provider-key"}).env == "prod"


def test_provider_key_aliasing_a_boundary_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="reutiliza"):
        _prod(
            llm_base_url="https://api.openai.com/v1",
            llm_api_key=SECRETS["secret_key"],
            llm_model_window=128_000,
        )


def test_provider_keys_may_alias_each_other() -> None:
    shared = "one-gateway-key-0123456789abcdef"
    settings = _prod(
        embedding_base_url="https://api.openai.com/v1",
        embedding_api_key=shared,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key=shared,
        llm_model_window=128_000,
    )

    assert settings.env == "prod"


@pytest.mark.parametrize(
    "context",
    [
        {"embedding_base_url": "https://api.openai.com/v1"},
        {
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model_window": 128_000,
        },
    ],
)
def test_google_client_secret_may_not_alias_a_gateway_key(
    context: dict[str, Any],
) -> None:
    shared = "shared-oauth-and-gateway-0123456789"
    field = "embedding_api_key" if "embedding_base_url" in context else "llm_api_key"

    with pytest.raises(ValidationError, match="google_client_secret"):
        _prod(
            **context,
            google_client_id="client-id",
            google_client_secret=shared,
            **{field: shared},
        )


def test_surrounding_whitespace_in_a_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="espacios"):
        _prod(secret_key=" " + "k" * 32)


def test_surrounding_whitespace_in_a_provider_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="espacios"):
        _prod(google_client_id="client-id", google_client_secret=" " + "g" * 32)


def test_public_origin_requires_https_outside_local() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings(env="staging", **{**PROD_URLS, "public_origin": "http://maulwurf.example"})


def test_allowed_origin_has_no_trailing_slash() -> None:
    assert _settings(public_origin="https://localhost:8443").allowed_origin == (
        "https://localhost:8443"
    )


def test_repr_and_dump_never_expose_secret_values() -> None:
    settings = _settings(
        google_client_id="client-id",
        google_client_secret="google-value-for-test",
        embedding_base_url="https://api.openai.com/v1",
        embedding_api_key="embedding-value-for-test",
    )
    rendered = f"{settings!r} {settings} {settings.model_dump()} {settings.model_dump_json()}"

    for value in [
        *SECRETS.values(),
        "google-value-for-test",
        "embedding-value-for-test",
    ]:
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
def test_dev_defaults_rejected_outside_local(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # La suite de integración exporta las URLs de servicios; aquí deben faltar.
    monkeypatch.delenv(f"MAULWURF_{field.upper()}", raising=False)
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


def test_dev_default_guard_also_covers_default_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Si un campo pasa a default_factory, el guard debe seguir detectando el valor.
    monkeypatch.delenv("MAULWURF_REDIS_URL", raising=False)

    class FactorySettings(Settings):
        redis_url: RedisDsn = Field(
            default_factory=lambda: cast(RedisDsn, "redis://localhost:6379/0")
        )

    urls = {k: v for k, v in PROD_URLS.items() if k != "redis_url"}
    with pytest.raises(ValidationError, match="redis_url conserva el valor de desarrollo"):
        FactorySettings(_env_file=None, env="prod", **{**SECRETS, **urls})


def test_empty_language_allowlist_is_rejected() -> None:
    with pytest.raises(ValidationError, match="ingest_languages"):
        _settings(ingest_languages=())


# --- Versioned embeddings and D3-Texto (L2.2) -----------------------------------------


def test_embedding_dimension_is_pinned_to_the_schema() -> None:
    # `embeddings.vector` is `vector(1536)` in migration 0002: another value fails at boot.
    with pytest.raises(ValidationError, match="embedding_dimension"):
        _settings(embedding_dimension=3072)


def test_embedding_url_and_key_go_together() -> None:
    with pytest.raises(ValidationError, match="embedding_base_url"):
        _settings(embedding_base_url="https://api.openai.com/v1")
    with pytest.raises(ValidationError, match="embedding_api_key"):
        _settings(embedding_api_key="only-the-key")


def test_change_me_embedding_key_rejected_outside_local() -> None:
    with pytest.raises(ValidationError, match="embedding_api_key"):
        _prod(
            embedding_base_url="https://api.openai.com/v1",
            embedding_api_key="change-me-please",
        )


def test_d3_text_models_default_to_fail_closed() -> None:
    # Empty by default: while D3-Texto is open only synthetic or redacted text is indexed.
    assert _settings().d3_text_closed_models == ()


def test_embedding_budget_is_bounded() -> None:
    with pytest.raises(ValidationError, match="embedding_batch_size"):
        _settings(embedding_batch_size=10_000)
    with pytest.raises(ValidationError, match="embedding_max_retries"):
        _settings(embedding_max_retries=99)


def test_embedding_concurrency_is_bounded_and_sequential_by_default() -> None:
    # PR #71: the knob exists, but parallelism stays off until D5 fixes the provider quota.
    assert _settings().embedding_max_concurrency == 1
    with pytest.raises(ValidationError, match="embedding_max_concurrency"):
        _settings(embedding_max_concurrency=0)
    with pytest.raises(ValidationError, match="embedding_max_concurrency"):
        _settings(embedding_max_concurrency=8)


def test_search_quota_is_bounded_and_provisional() -> None:
    # A2.11: 30/min is provisional until D5/F0 publishes the real provider quota.
    assert _settings().search_rate_limit_per_minute == 30
    with pytest.raises(ValidationError, match="search_rate_limit_per_minute"):
        _settings(search_rate_limit_per_minute=0)
    with pytest.raises(ValidationError, match="search_rate_limit_per_minute"):
        _settings(search_rate_limit_per_minute=601)


# --- Streaming LLM budget (L2.5) ------------------------------------------------------


def test_llm_url_key_and_window_are_required_together() -> None:
    with pytest.raises(ValidationError, match="llm_base_url"):
        _settings(llm_base_url="https://api.openai.com/v1")
    with pytest.raises(ValidationError, match="llm_api_key"):
        _settings(llm_api_key="only-the-key")
    with pytest.raises(ValidationError, match="llm_model_window"):
        _settings(
            llm_base_url="https://api.openai.com/v1",
            llm_api_key="llm-value-for-test",
        )


def test_llm_budget_must_fit_inside_the_window() -> None:
    with pytest.raises(ValidationError, match="llm_model_window"):
        _settings(
            llm_base_url="https://api.openai.com/v1",
            llm_api_key="llm-value-for-test",
            llm_model_window=1536,
            llm_output_reserve_tokens=1024,
            llm_context_margin_tokens=512,
        )


def test_llm_secret_is_not_exposed_in_repr() -> None:
    settings = _settings(
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="llm-value-for-test",
        llm_model_window=8192,
    )
    rendered = f"{settings!r} {settings} {settings.model_dump()} {settings.model_dump_json()}"
    assert "llm-value-for-test" not in rendered


def test_llm_defaults_are_fail_closed() -> None:
    settings = _settings()
    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
    assert settings.llm_model_window is None


# A2.13 / ADR-0008 §1-§2: the reception hop is configured as a pair, and its secret is a floor.

INGEST_SECRET_HEX = "ab" * 32


def test_ingest_url_and_secret_go_together() -> None:
    with pytest.raises(ValidationError, match="ingest_url e ingest_credential_secret"):
        _settings(ingest_url="http://ingest:8000")
    with pytest.raises(ValidationError, match="ingest_url e ingest_credential_secret"):
        _settings(ingest_credential_secret=INGEST_SECRET_HEX)


def test_ingest_pair_unset_is_valid() -> None:
    settings = _settings()

    assert settings.ingest_url is None and settings.ingest_credential_secret is None
    assert settings.ingest_credential_kid == "k1"


@pytest.mark.parametrize("env", ["local", "staging"])
def test_change_me_ingest_secret_is_rejected_in_every_environment(env: str) -> None:
    with pytest.raises(ValidationError, match="ingest_credential_secret"):
        _settings(
            env=env,
            ingest_url="http://ingest:8000",
            ingest_credential_secret="change-me-" + INGEST_SECRET_HEX,
        )


def test_ingest_secret_shorter_than_32_bytes_is_rejected() -> None:
    thirty_one = "ab" * 31
    with pytest.raises(ValidationError, match="32 bytes"):
        _settings(ingest_url="http://ingest:8000", ingest_credential_secret=thirty_one)


def test_ingest_secret_must_be_hex() -> None:
    with pytest.raises(ValidationError, match="hexadecimal"):
        _settings(ingest_url="http://ingest:8000", ingest_credential_secret="z" * 64)


def test_ingest_secret_of_32_bytes_is_accepted() -> None:
    settings = _settings(
        ingest_url="http://ingest:8000", ingest_credential_secret=INGEST_SECRET_HEX
    )

    assert settings.ingest_credential_secret is not None
    assert len(settings.ingest_credential_secret.get_secret_value()) == 64


def test_ingest_kid_is_a_short_label() -> None:
    with pytest.raises(ValidationError, match="ingest_credential_kid"):
        _settings(ingest_credential_kid="Key-One")
