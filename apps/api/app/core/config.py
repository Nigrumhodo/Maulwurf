"""Configuración por entorno — nada sensible en código (A1.2).

TODO(Andres): completar campos según M1; los límites de ingesta se reemplazan
por los medidos en el spike F0 (ver docs/sprints/santiago.md S1).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAULWURF_", env_file=".env", extra="ignore")

    env: str = "local"
    secret_key: str = "change-me"
    encryption_key: str = "change-me"
    database_url: str = "postgresql+asyncpg://maulwurf:maulwurf@localhost:5432/maulwurf"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
