"""Política de publicación de la outbox (J1.5, U-S1-JF-04). Función pura, sin BD ni Redis.

Reglas (S1.md §1.3, DISENO §3.1):
- `index_requested` / `analyze_requested` son los únicos tipos con gate de cleanup: se
  publican solo si el evento está habilitado Y el último intento de ingesta del audio tiene
  `cleanup_status = verified`. Un lease vencido o un intento sin evidencia no cuentan; sin
  intento conocido tampoco (fail closed). La doble condición es defensa en profundidad: el
  CHECK de la tabla ya impide un index/analyze habilitado con `blocked_reason`.
- Calendar, notificaciones y borrados NO dependen del cleanup de audio. En S1 no tienen
  consumidor, así que se quedan pendientes sin error hasta que su ticket lo conecte.
- Un tipo desconocido nunca se publica (fail closed) y se reporta.
"""
from dataclasses import dataclass
from enum import StrEnum

CLEANUP_GATED_TYPES = frozenset({"index_requested", "analyze_requested"})
# Catálogo de DISENO §3.1 sin consumidor en S1 (sus jobs llegan en S3/S4).
KNOWN_WITHOUT_CONSUMER = frozenset(
    {"sync_task_event", "task_cancelled", "notify", "send_digest", "delete_event",
     "account_deleted"}
)
# Tipo de evento -> función ARQ del worker.
JOB_FOR_TYPE = {"index_requested": "index", "analyze_requested": "analyze"}


class Outcome(StrEnum):
    PUBLISH = "publish"
    CLEANUP_PENDING = "cleanup_pending"
    DISABLED = "disabled"
    NO_CONSUMER = "no_consumer"
    UNKNOWN_TYPE = "unknown_type"
    # Contador, no decisión: el job ya estaba en Redis y no se volvió a encolar.
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    job: str | None = None

    @property
    def publish(self) -> bool:
        return self.outcome is Outcome.PUBLISH


def decide(
    event_type: str, *, enabled: bool, blocked_reason: str | None, cleanup_status: str | None
) -> Decision:
    """Qué hacer con un evento pendiente. `cleanup_status` es el del último intento del audio."""
    if event_type in CLEANUP_GATED_TYPES:
        if not enabled or blocked_reason is not None or cleanup_status != "verified":
            return Decision(Outcome.CLEANUP_PENDING)
        return Decision(Outcome.PUBLISH, JOB_FOR_TYPE[event_type])
    if event_type in KNOWN_WITHOUT_CONSUMER:
        return Decision(Outcome.NO_CONSUMER if enabled else Outcome.DISABLED)
    return Decision(Outcome.UNKNOWN_TYPE)
