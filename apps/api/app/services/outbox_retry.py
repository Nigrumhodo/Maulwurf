"""Backoff con jitter acotado para reintentos de publicación de la outbox (J2.1).

Función pura: el RNG y el reloj son inyectables, así que los tests fijan la semilla y no
dependen del tiempo real. El máximo de intentos no vive aquí sino en `outbox_events.max_attempts`.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta

# 2**16 * base ya supera cualquier cap razonable; acota el exponente sin tocar la política.
_MAX_EXPONENT = 16


@dataclass(frozen=True)
class RetryPolicy:
    """Espera `min(cap, base * 2**(n-1))` más un jitter uniforme de hasta `jitter_fraction`.

    La espera nunca supera `cap * (1 + jitter_fraction)`, así que el jitter también queda acotado.
    """

    base_seconds: float = 30.0
    cap_seconds: float = 300.0
    jitter_fraction: float = 0.2
    rng: random.Random = field(default_factory=random.Random)

    def delay(self, failed_attempts: int) -> timedelta:
        """Espera tras el `failed_attempts`-ésimo intento fallido (empieza en 1)."""
        if failed_attempts < 1:
            raise ValueError("failed_attempts must be >= 1")
        exponent = min(failed_attempts - 1, _MAX_EXPONENT)
        backoff = min(self.cap_seconds, self.base_seconds * 2**exponent)
        jitter = self.rng.uniform(0.0, backoff * self.jitter_fraction)
        return timedelta(seconds=backoff + jitter)


DEFAULT_RETRY = RetryPolicy()
