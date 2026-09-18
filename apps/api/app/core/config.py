"""Configuración por entorno — nada sensible en código (A1.2).

TODO(Andres): completar campos según M1; los límites de ingesta se reemplazan
por los medidos en el spike F0 (ver docs/sprints/santiago.md S1).
"""
from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAULWURF_", env_file=".env", extra="forbid")
    # extra="forbid": una MAULWURF_* mal escrita falla en vez de ignorarse en silencio.

    env: str = "local"
    secret_key: SecretStr  # sin default: obliga a definirlo por entorno
    encryption_key: SecretStr  # AES-GCM versionada (M1); nunca loguear .get_secret_value()
    oauth_state_secret: SecretStr  # .env.example sí lo define; antes se descartaba en silencio
    database_url: PostgresDsn = "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    redis_url: str = "redis://localhost:6379/0"

    @model_validator(mode="after")
    def _reject_insecure_defaults(self) -> "Settings":
        # Fail-fast: si un entorno no-local arranca con "change-me", que falle la app.
        if self.env != "local":
            for name in ("secret_key", "encryption_key", "oauth_state_secret"):
                if "change-me" in getattr(self, name).get_secret_value():
                    raise ValueError(f"{name} inseguro en env={self.env}")
        return self


settings = Settings()
