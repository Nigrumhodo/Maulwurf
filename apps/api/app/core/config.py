"""Configuración por entorno (A1.2): nada sensible en código, fail-fast al arrancar.

Fuentes, de menor a mayor prioridad: `../../.env` (el `.env` de la raíz del repo, compartido
con Compose, cuando se arranca desde `apps/api`), `.env` del directorio de trabajo y el
entorno del proceso. Esos archivos también traen variables de otros servicios
(`POSTGRES_PASSWORD`, `RIVA_*`, `INGEST_*`), por eso las claves sin prefijo se ignoran;
una `MAULWURF_*` desconocida en el entorno o en los `.env` efectivos tumba el arranque.
`VAR=` vacío cuenta como no configurado. Con `extra="ignore"`, un argumento con typo en
`Settings(**kwargs)` se ignora: solo el código de tests construye Settings así.

Fuera de `local`, los valores de desarrollo (DSN de localhost, Origin de localhost) no se
aceptan: una variable olvidada en staging/prod debe fallar al arrancar, no en la primera
consulta.

Las capabilities de ingesta se reciben como una configuración JSON publicada a partir del
spike F0 (A2.2). Los valores provisionales del contrato S1 no se exponen como límites
efectivos.
"""
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import dotenv_values
from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    TypeAdapter,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.ingestion import IngestionCapabilities

ENV_PREFIX = "MAULWURF_"
ENV_FILES = ("../../.env", ".env")
_SECRET_FIELDS = (
    "secret_key",
    "encryption_key",
    "oauth_state_secret",
    "google_client_secret",
    "embedding_api_key",
    "llm_api_key",
)
# Policy floor for secrets we generate. HKDF in `crypto.py` derives the AES-256 key, so this
# is not an AES-GCM requirement. Provider-issued keys are excluded below: their length and
# format belong to the provider (a 24-character Google client secret is valid).
BOUNDARY_SECRET_MIN_BYTES = 32
# These three guard different boundaries (sessions, encrypted data, OAuth state); reusing
# one value would let a leak in one boundary compromise the others.
_BOUNDARY_SECRET_FIELDS = ("secret_key", "encryption_key", "oauth_state_secret")
# One gateway key may serve embeddings and chat; the Google OAuth client secret is a
# different trust domain and must not alias either gateway key.
_GATEWAY_SECRET_FIELDS = ("embedding_api_key", "llm_api_key")
_PROVIDER_SECRET_FIELDS = ("google_client_secret", *_GATEWAY_SECRET_FIELDS)
# Pinned by `embeddings.vector vector(1536)` in migration 0002 (M6). Changing it requires a
# migration and a whole new index generation, so startup validates it instead of silently
# accepting a model with another dimension.
EMBEDDING_DIMENSION = 1536
INGEST_SECRET_MIN_BYTES = 32
_DEV_DEFAULT_FIELDS = ("database_url", "redis_url", "public_origin")
# `.env` que usa la construcción en curso (respeta `_env_file`, p. ej. None en tests).
_active_env_files: ContextVar[tuple[str, ...]] = ContextVar("_active_env_files", default=ENV_FILES)


def _as_tuple(env_file: Any) -> tuple[str, ...]:
    if env_file is None:
        return ()
    if isinstance(env_file, str | Path):
        return (str(env_file),)
    return tuple(str(f) for f in env_file)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILES,
        extra="ignore",  # ver docstring: las MAULWURF_* desconocidas se validan aparte
        # Un ValidationError incrusta el valor recibido: sin esto, un DSN mal formado con
        # su contraseña real acabaría impreso en logs al arrancar.
        hide_input_in_errors=True,
        env_ignore_empty=True,  # `VAR=` es «no configurado», no cadena vacía
    )

    def __init__(self, **values: Any) -> None:
        token = _active_env_files.set(_as_tuple(values.get("_env_file", ENV_FILES)))
        try:
            super().__init__(**values)
        finally:
            _active_env_files.reset(token)

    # No default: a deployment that forgets MAULWURF_ENV must not inherit the local guards
    # and their lenient secret rules (A2.16).
    env: Literal["local", "ci", "staging", "prod"]
    secret_key: SecretStr  # sin default: obliga a definirlo por entorno
    encryption_key: SecretStr  # AES-GCM (M1); nunca loguear .get_secret_value()
    encryption_key_version: int = Field(default=1, ge=1)  # se guarda junto a cada cifrado
    oauth_state_secret: SecretStr
    # Opcionales hasta A1.3 (OAuth).
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Vida máxima de la sesión opaca (A1.4); el logout la revoca antes.
    session_ttl_hours: int = Field(default=168, ge=1, le=720)
    # A2.11: provisional per-user search quota (one embeddings call per search). D5/F0
    # must replace it with the provider quota measured by the contract spike.
    search_rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    # Contrato de ingesta S1 (A1.8). Valores provisionales hasta D2/D4 (S1.A8/A2.2):
    # allowlist propuesta por S1.A3 y límite de bytes de `.env.example` (200 MiB, no aprobado).
    privacy_notice_version: str = "2026-09-v1"
    ingest_languages: tuple[str, ...] = Field(default=("es", "en", "fr"), min_length=1)
    upload_max_bytes: int = Field(default=209_715_200, ge=1)
    # A2.19: `ingest` owns the reception cut (`INGEST_MAX_UPLOAD_BYTES`) and the shared `.env`
    # reaches this process too. Reading the same name lets startup reject two disagreeing
    # size limits instead of silently cutting bytes with a different value than the API
    # advertises. Unset outside Compose/the shared `.env`: no second value to compare.
    ingest_max_upload_bytes: int | None = Field(
        default=None, ge=1, validation_alias="INGEST_MAX_UPLOAD_BYTES"
    )
    # Fallback only for an explicit all-null TTL publication; S4.4 publishes 10 min.
    upload_ttl_minutes: int = Field(default=15, ge=1, le=120)
    # Token de confirmación de variante del `409 duplicate_variant` (A2.4, punto C4): M2
    # pide «expiración corta» sin publicar un número, así que es provisional como el resto
    # del contrato S1. Corto a propósito: es de un solo uso y solo se persiste su hash.
    variant_token_ttl_minutes: int = Field(default=5, ge=1, le=60)
    # Configuración no sensible, publicada y versionada por el spike. Pydantic Settings
    # la lee de MAULWURF_INGESTION_CAPABILITIES como un objeto JSON; sin ella el endpoint
    # publica los TTLs efectivos de S4.4 (10/30/60, con su benchmark) y deja null los
    # límites de tamaño/duración que D4 todavía no aprobó.
    ingestion_capabilities: IngestionCapabilities = Field(default_factory=IngestionCapabilities)
    # Reception hop to `ingest` (ADR-0008 §1-§2). Unset: `get_reception_sink()` keeps the
    # unavailable sink (503). Both the URL and the secret are needed to stream.
    ingest_url: AnyHttpUrl | None = None
    ingest_credential_secret: SecretStr | None = None
    ingest_credential_kid: str = Field(default="k1", pattern=r"^[a-z0-9]{1,16}$")
    # Versioned embeddings (L2.2, M6/D5): provider compatible with the embeddings API.
    # `base_url` is the base to which `/embeddings` is appended. Without base_url and api_key
    # indexing fails with `embedding_not_configured`; there is never a default provider nor
    # a silent degradation to fake vectors.
    embedding_base_url: AnyHttpUrl | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    embedding_dimension: int = Field(default=EMBEDDING_DIMENSION, ge=1)
    # Bounded per-batch budget (guardrail 10: no loop or burst without a limit).
    embedding_batch_size: int = Field(default=100, ge=1, le=512)
    embedding_max_retries: int = Field(default=3, ge=0, le=10)
    embedding_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    # Lotes en paralelo (PR #71). Por defecto 1 = secuencial: la cuota del proveedor sigue
    # pendiente (D5, guardia 10), así que el paralelismo no se activa por defecto; el techo
    # (4) acota cualquier ráfaga cuando D5 fije el límite real.
    embedding_max_concurrency: int = Field(default=1, ge=1, le=4)
    # Versioned chat/LLM provider (L2.5, M7/D5). Same fail-closed rule as embeddings: without
    # base_url + api_key there is no provider and the SSE pipeline publishes
    # `provider_unavailable` instead of inventing a completion. The window is required with a
    # provider because the token budget cannot be enforced without it.
    llm_base_url: AnyHttpUrl | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = Field(default="gpt-4o-mini", min_length=1)
    llm_model_window: int | None = Field(default=None, ge=1)
    # Provisional until D5/F0 publishes the provider's real limits. They are configuration,
    # not a hidden default: the composition of window - reserve - margin is the contract.
    llm_output_reserve_tokens: int = Field(default=1024, ge=1)
    llm_context_margin_tokens: int = Field(default=512, ge=0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    # D3-Texto: models/providers whose decision is closed (ESPECIFICACION §12). Empty by
    # default — fail closed: with the decision open only synthetic or redacted text is
    # indexed (S2.md §0.6, rule 6). Only the lead's recorded closure fills it in.
    d3_text_closed_models: tuple[str, ...] = ()
    # Único Origin aceptado en mutaciones (A1.4): el que sirve Caddy, same-origin.
    public_origin: AnyHttpUrl = cast(AnyHttpUrl, "https://localhost")
    # cast: pydantic valida los DSN en runtime; mypy no relaja el tipo del default.
    database_url: PostgresDsn = cast(
        PostgresDsn, "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    )
    # A2.17: constrained runtime role (NOLOGIN, no BYPASSRLS). Every transaction starts
    # with SET LOCAL ROLE before touching tenant rows; migrations keep the DSN owner.
    database_role: str = Field(default="maulwurf_app", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    redis_url: RedisDsn = cast(RedisDsn, "redis://localhost:6379/0")
    # J4.2/J4.1: without a deployed Gmail consumer, claiming due rows would move them to
    # `sending` only to expire them as a false `delivery_unknown` (terminal). The scheduler
    # therefore starts disabled and must only be enabled once J4.1 consumes `sending`.
    notification_scheduler_enabled: bool = False

    @property
    def allowed_origin(self) -> str:
        # AnyHttpUrl normaliza con "/" final; el header Origin nunca lo lleva.
        return str(self.public_origin).rstrip("/")

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "Settings":
        known = {f"{ENV_PREFIX}{name}".upper() for name in type(self).model_fields}
        names = set(os.environ)
        for path in _active_env_files.get():
            if Path(path).is_file():
                names.update(dotenv_values(path))
        unknown = sorted(
            n for n in names if n.upper().startswith(ENV_PREFIX) and n.upper() not in known
        )
        if unknown:
            raise ValueError(f"variables de entorno desconocidas: {', '.join(unknown)}")
        return self

    @model_validator(mode="after")
    def _reject_invalid_origin(self) -> "Settings":
        # Un header Origin es solo esquema://host[:puerto]; con path, query o fragment
        # nunca coincidiría y el CSRF fallaría siempre sin diagnóstico.
        origin = self.public_origin
        if origin.path not in (None, "", "/") or origin.query or origin.fragment:
            raise ValueError("public_origin no admite path, query ni fragment")
        return self

    @model_validator(mode="after")
    def _reject_incomplete_oauth(self) -> "Settings":
        if (self.google_client_id is None) != (self.google_client_secret is None):
            raise ValueError("google_client_id y google_client_secret van juntos")
        return self

    @model_validator(mode="after")
    def _reject_incomplete_ingest_config(self) -> "Settings":
        if (self.ingest_url is None) != (self.ingest_credential_secret is None):
            raise ValueError("ingest_url e ingest_credential_secret van juntos")
        return self

    @model_validator(mode="after")
    def _reject_weak_ingest_secret(self) -> "Settings":
        # ADR-0008 §2: a 32-byte floor and no placeholder in every environment, local included.
        # This is a security floor, not a capacity limit.
        secret = self.ingest_credential_secret
        if secret is None:
            return self
        raw = secret.get_secret_value()
        if "change-me" in raw:
            raise ValueError("ingest_credential_secret inseguro")
        try:
            decoded = bytes.fromhex(raw.strip())
        except ValueError:
            raise ValueError("ingest_credential_secret debe ser hexadecimal") from None
        if len(decoded) < INGEST_SECRET_MIN_BYTES:
            raise ValueError("ingest_credential_secret debe tener al menos 32 bytes")
        return self

    @model_validator(mode="after")
    def _reject_upload_limit_drift(self) -> "Settings":
        # A2.19: one effective byte limit. `MAULWURF_UPLOAD_MAX_BYTES` is what the API
        # advertises; `INGEST_MAX_UPLOAD_BYTES` is where `ingest` cuts. If both are
        # configured they must agree, or the deployment would reject a size the API
        # accepted (or accept one it already rejected).
        if (
            self.ingest_max_upload_bytes is not None
            and self.ingest_max_upload_bytes != self.upload_max_bytes
        ):
            raise ValueError("upload_max_bytes e INGEST_MAX_UPLOAD_BYTES deben coincidir")
        return self

    @model_validator(mode="after")
    def _reject_incomplete_embedding_config(self) -> "Settings":
        if (self.embedding_base_url is None) != (self.embedding_api_key is None):
            raise ValueError("embedding_base_url y embedding_api_key van juntos")
        return self

    @model_validator(mode="after")
    def _reject_embedding_dimension_change(self) -> "Settings":
        # The vector dimension lives in the schema (`embeddings.vector vector(1536)`, M6);
        # another value here could only come from an incompatible model and would fail on
        # INSERT, so it is rejected at startup with the field name.
        if self.embedding_dimension != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding_dimension debe ser {EMBEDDING_DIMENSION} "
                "(dimensión fijada en la migración 0002)"
            )
        return self

    @model_validator(mode="after")
    def _reject_incomplete_llm_config(self) -> "Settings":
        if (self.llm_base_url is None) != (self.llm_api_key is None):
            raise ValueError("llm_base_url y llm_api_key van juntos")
        if self.llm_base_url is not None and self.llm_model_window is None:
            raise ValueError(
                "llm_model_window es obligatorio cuando el proveedor LLM está configurado"
            )
        return self

    @model_validator(mode="after")
    def _reject_llm_budget(self) -> "Settings":
        if self.llm_model_window is None:
            return self
        minimum = self.llm_output_reserve_tokens + self.llm_context_margin_tokens + 1
        if self.llm_model_window < minimum:
            raise ValueError(
                "llm_model_window debe superar llm_output_reserve_tokens + "
                "llm_context_margin_tokens"
            )
        return self

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> "Settings":
        # Fail-fast outside local: empty, placeholder, too short or reused secrets, or a
        # public origin without TLS. Local keeps the development placeholders usable.
        if self.env == "local":
            return self
        secret_values: dict[str, str] = {}
        for name in _SECRET_FIELDS:
            value: SecretStr | None = getattr(self, name)
            if value is None:
                continue
            # Validate exactly what the runtime uses: a newline injected by a secret file is
            # an explicit error, not something `.strip()` should hide.
            raw = value.get_secret_value()
            if not raw.strip():
                raise ValueError(f"{name} vacío en env={self.env}")
            if raw != raw.strip():
                raise ValueError(
                    f"{name} tiene espacios o saltos de línea alrededor en env={self.env}"
                )
            if "change-me" in raw:
                raise ValueError(f"{name} inseguro en env={self.env}")
            # Length is our policy only for the secrets we generate, not for provider keys.
            if (
                name in _BOUNDARY_SECRET_FIELDS
                and len(raw.encode("utf-8")) < BOUNDARY_SECRET_MIN_BYTES
            ):
                raise ValueError(
                    f"{name} debe tener al menos {BOUNDARY_SECRET_MIN_BYTES} bytes "
                    f"en env={self.env}"
                )
            secret_values[name] = raw
        missing = [name for name in _BOUNDARY_SECRET_FIELDS if name not in secret_values]
        if missing:
            raise ValueError(
                f"faltan secretos de frontera: {', '.join(missing)} en env={self.env}"
            )
        boundary = {secret_values[name] for name in _BOUNDARY_SECRET_FIELDS}
        if len(boundary) != len(_BOUNDARY_SECRET_FIELDS):
            raise ValueError(
                "secret_key, encryption_key y oauth_state_secret deben ser distintos "
                f"en env={self.env}"
            )
        for name in _PROVIDER_SECRET_FIELDS:
            provider_value = secret_values.get(name)
            if provider_value is not None and provider_value in boundary:
                raise ValueError(
                    f"{name} reutiliza secret_key, encryption_key u "
                    f"oauth_state_secret en env={self.env}"
                )
        google_secret = secret_values.get("google_client_secret")
        gateway = {
            secret_values[name] for name in _GATEWAY_SECRET_FIELDS if name in secret_values
        }
        if google_secret is not None and google_secret in gateway:
            raise ValueError(
                "google_client_secret no puede reutilizar embedding_api_key ni "
                f"llm_api_key en env={self.env}"
            )
        ingest_secret = self.ingest_credential_secret
        if (
            ingest_secret is not None
            and ingest_secret.get_secret_value() in secret_values.values()
        ):
            raise ValueError(
                f"ingest_credential_secret reutiliza otro secreto en env={self.env}"
            )
        if self.public_origin.scheme != "https":
            raise ValueError(f"public_origin debe usar https en env={self.env}")
        for name in _DEV_DEFAULT_FIELDS:
            field = type(self).model_fields[name]
            # call_default_factory: con default_factory, get_default() devolvería la factoría
            # y el guard dejaría de detectar el valor de desarrollo (fail-open).
            default: object = TypeAdapter(field.annotation).validate_python(
                field.get_default(call_default_factory=True)
            )
            if str(getattr(self, name)) == str(default):
                raise ValueError(f"{name} conserva el valor de desarrollo en env={self.env}")
        return self


settings = Settings()
