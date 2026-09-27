"""Configuración por entorno (A1.2): nada sensible en código, fail-fast al arrancar.

Fuentes, de menor a mayor prioridad: `../../.env` (el `.env` de la raíz del repo, compartido
con Compose, cuando se arranca desde `apps/api`), `.env` del directorio de trabajo y el
entorno del proceso. Esos archivos también traen variables de otros servicios
(`POSTGRES_PASSWORD`, `RIVA_*`, `INGEST_*`), por eso las claves sin prefijo se ignoran;
una `MAULWURF_*` desconocida, venga de donde venga, tumba el arranque.

Los límites de ingesta se añaden cuando el spike F0 publique los valores medidos (A2.2).
"""
import os
from pathlib import Path
from typing import Literal, cast

from dotenv import dotenv_values
from pydantic import AnyHttpUrl, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "MAULWURF_"
ENV_FILES = ("../../.env", ".env")
_SECRET_FIELDS = ("secret_key", "encryption_key", "oauth_state_secret", "google_client_secret")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILES,
        extra="ignore",  # ver docstring: las MAULWURF_* desconocidas se validan aparte
        # Un ValidationError incrusta el valor recibido: sin esto, un DSN mal formado con
        # su contraseña real acabaría impreso en logs al arrancar.
        hide_input_in_errors=True,
    )

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
        for path in ENV_FILES:
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
        # Un header Origin es solo esquema://host[:puerto]; con path nunca coincidiría.
        if self.public_origin.path not in (None, "", "/") or self.public_origin.query:
            raise ValueError("public_origin no admite path ni query")
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
        return self


settings = Settings()
