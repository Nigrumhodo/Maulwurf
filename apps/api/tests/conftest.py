"""Configuración común de pytest para `apps/api`.

`app.core.config` construye `Settings()` al importarse y exige secretos sin default. Aquí
se fijan valores ficticios solo si el entorno no los trae, para que las unitarias corran
sin `.env`; nunca son credenciales reales.
"""
import os

for _name, _value in {
    "MAULWURF_SECRET_KEY": "test-only-secret-key",
    "MAULWURF_ENCRYPTION_KEY": "test-only-encryption-key",
    "MAULWURF_OAUTH_STATE_SECRET": "test-only-oauth-state",
}.items():
    os.environ.setdefault(_name, _value)
