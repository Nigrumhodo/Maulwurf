"""Clasificación de errores al encolar un job de la outbox (J2.1).

Tres clases, con tratamiento distinto:
- `INFRA`: Redis caído, lento o sin memoria. Ningún evento tiene la culpa, así que el ciclo se
  aborta sin gastar intentos (si no, un reinicio de Redis mataría toda la outbox, contra
  ADR-0005: se pierden jobs, no datos).
- `TRANSIENT`: fallo de un comando concreto que puede acabar bien en otro intento del mismo
  evento. Consume intento, con backoff.
- `PERMANENT`: cualquier otra cosa (p. ej. argumentos no serializables). Nunca se reintenta.
"""

from enum import StrEnum

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

# Redis rechaza toda escritura (noeviction, cargando o ocupado): afecta a todos los eventos.
_INFRA_RESPONSE_PREFIXES = ("OOM", "LOADING", "BUSY")
_TRANSIENT_RESPONSE_PREFIXES = ("TRYAGAIN",)


class ErrorKind(StrEnum):
    INFRA = "infra"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


def classify_enqueue_error(exc: BaseException) -> ErrorKind:
    """Clasifica un error de `enqueue_job`. Ante la duda, `PERMANENT` (no reintentar)."""
    if isinstance(exc, RedisConnectionError | RedisTimeoutError | OSError):
        return ErrorKind.INFRA
    if isinstance(exc, ResponseError):
        # Solo el prefijo: el mensaje completo puede traer datos del servidor.
        message = str(exc)
        if message.startswith(_INFRA_RESPONSE_PREFIXES):
            return ErrorKind.INFRA
        if message.startswith(_TRANSIENT_RESPONSE_PREFIXES):
            return ErrorKind.TRANSIENT
    return ErrorKind.PERMANENT
