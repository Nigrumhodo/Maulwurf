"""Configuración por entorno (A1.2): nada sensible en código, fail-fast al arrancar.

Los límites de ingesta se añaden cuando el spike F0 publique los valores medidos (A2.2).
"""
import os
from typing import Literal, cast

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = ("secret_key", "encryption_key", "oauth_state_secret", "google_client_secret")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAULWURF_", env_file=".env", extra="forbid")
    # extra="forbid": una MAULWURF_* mal escrita falla en vez de ignorarse en silencio.

    env: Literal["local", "ci", "staging", "prod"] = "local"
    secret_key: SecretStr  # sin default: obliga a definirlo por entorno
    encryption_key: SecretStr  # AES-GCM (M1); nunca loguear .get_secret_value()
    encryption_key_version: int = Field(default=1, ge=1)  # se guarda junto a cada cifrado
    oauth_state_secret: SecretStr
    # Opcionales hasta A1.3 (OAuth); declararlos evita que extra="forbid" rechace el .env.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Vida máxima de la sesión opaca (A1.4); el logout la revoca antes.
    session_ttl_hours: int = Field(default=168, ge=1, le=720)
    # Único Origin aceptado en mutaciones (A1.4): el que sirve Caddy, same-origin.
    public_origin: AnyHttpUrl = cast(AnyHttpUrl, "https://localhost")
    # cast: pydantic valida el DSN en runtime; mypy no relaja el tipo del default.
    database_url: PostgresDsn = cast(
        PostgresDsn, "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    )
    redis_url: str = "redis://localhost:6379/0"

    @property
    def allowed_origin(self) -> str:
        # AnyHttpUrl normaliza con "/" final; el header Origin nunca lo lleva.
        return str(self.public_origin).rstrip("/")

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "Settings":
        # extra="forbid" solo cubre el .env; en Compose las variables llegan por el entorno
        # del proceso y pydantic-settings ignora ahí las desconocidas sin avisar.
        prefix = self.model_config.get("env_prefix", "")
        known = {f"{prefix}{name}".upper() for name in type(self).model_fields}
        unknown = sorted(
            k for k in os.environ if k.upper().startswith(prefix) and k.upper() not in known
        )
        if unknown:
            raise ValueError(f"variables de entorno desconocidas: {', '.join(unknown)}")
        return self

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> "Settings":
        # Fail-fast: fuera de local, un "change-me" o un Origin sin TLS tumban el arranque.
        if self.env == "local":
            return self
        for name in _SECRET_FIELDS:
            value: SecretStr | None = getattr(self, name)
            if value is not None and "change-me" in value.get_secret_value():
                raise ValueError(f"{name} inseguro en env={self.env}")
        if self.public_origin.scheme != "https":
            raise ValueError(f"public_origin debe usar https en env={self.env}")
        return self


settings = Settings()
