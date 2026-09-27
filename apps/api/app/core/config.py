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

Los límites de ingesta se añaden cuando el spike F0 publique los valores medidos (A2.2).
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

ENV_PREFIX = "MAULWURF_"
ENV_FILES = ("../../.env", ".env")
_SECRET_FIELDS = ("secret_key", "encryption_key", "oauth_state_secret", "google_client_secret")
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

    env: Literal["local", "ci", "staging", "prod"] = "local"
    secret_key: SecretStr  # sin default: obliga a definirlo por entorno
    encryption_key: SecretStr  # AES-GCM (M1); nunca loguear .get_secret_value()
    encryption_key_version: int = Field(default=1, ge=1)  # se guarda junto a cada cifrado
    oauth_state_secret: SecretStr
    # Opcionales hasta A1.3 (OAuth).
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Vida máxima de la sesión opaca (A1.4); el logout la revoca antes.
    session_ttl_hours: int = Field(default=168, ge=1, le=720)
    # Contrato de ingesta S1 (A1.8). Valores provisionales hasta D2/D4 (S1.A8/A2.2):
    # allowlist propuesta por S1.A3 y límite de bytes de `.env.example` (200 MiB, no aprobado).
    privacy_notice_version: str = "2026-09-v1"
    ingest_languages: tuple[str, ...] = Field(default=("es", "en", "fr"), min_length=1)
    upload_max_bytes: int = Field(default=209_715_200, ge=1)
    upload_ttl_minutes: int = Field(default=15, ge=1, le=120)
    # Único Origin aceptado en mutaciones (A1.4): el que sirve Caddy, same-origin.
    public_origin: AnyHttpUrl = cast(AnyHttpUrl, "https://localhost")
    # cast: pydantic valida los DSN en runtime; mypy no relaja el tipo del default.
    database_url: PostgresDsn = cast(
        PostgresDsn, "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    )
    redis_url: RedisDsn = cast(RedisDsn, "redis://localhost:6379/0")

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
    def _reject_insecure_defaults(self) -> "Settings":
        # Fail-fast fuera de local: secreto vacío o "change-me", u Origin sin TLS.
        if self.env == "local":
            return self
        for name in _SECRET_FIELDS:
            value: SecretStr | None = getattr(self, name)
            if value is None:
                continue
            if not value.get_secret_value().strip():
                raise ValueError(f"{name} vacío en env={self.env}")
            if "change-me" in value.get_secret_value():
                raise ValueError(f"{name} inseguro en env={self.env}")
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
