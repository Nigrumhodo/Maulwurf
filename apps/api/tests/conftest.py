"""Configuración común de pytest para `apps/api`.

`app.core.config` construye `Settings()` al importarse, así que el entorno se fija aquí, a
nivel de módulo: pytest carga este archivo antes de importar cualquier test, y un fixture
llegaría tarde. Solo se conservan las URLs de servicios (la suite de integración las
necesita); cualquier otra `MAULWURF_*` del shell se descarta para que un `MAULWURF_ENV=prod`
o una variable residual no cambie el resultado. Los secretos son ficticios.
"""
import os

_KEPT_FROM_SHELL = {"MAULWURF_DATABASE_URL", "MAULWURF_REDIS_URL"}

for _name in [k for k in os.environ if k.upper().startswith("MAULWURF_")]:
    if _name.upper() not in _KEPT_FROM_SHELL:
        del os.environ[_name]

os.environ.update(
    {
        "MAULWURF_SECRET_KEY": "test-only-secret-key",
        "MAULWURF_ENCRYPTION_KEY": "test-only-encryption-key",
        "MAULWURF_OAUTH_STATE_SECRET": "test-only-oauth-state",
    }
)
