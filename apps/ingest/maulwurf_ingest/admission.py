"""Pestillo de admisión de la instancia de ingesta (S1.B6).

`cleanup_status=failed` cierra la admisión de uploads nuevos y deja una alerta.
Un lease vencido que deja el intento en `pending` no cierra la admisión: solo
bloquea la outbox de índice y análisis. S2 consultará `uploads_admitted()`; el
`PUT` de S1 sigue respondiendo `503 capacity_unavailable` por A1.8.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    code: str
    attempt_id: uuid.UUID


_halted = False
_alerts: list[Alert] = []


def uploads_admitted() -> bool:
    """False solo después de un cleanup persistido como `failed`."""
    return not _halted


def alerts() -> tuple[Alert, ...]:
    return tuple(_alerts)


def halt(attempt_id: uuid.UUID) -> None:
    """Cierra la admisión y registra la alerta. Sin rutas ni audio."""
    global _halted
    _halted = True
    _alerts.append(Alert("cleanup_failed", attempt_id))
    logger.error("ingest.alert code=cleanup_failed attempt_id=%s", attempt_id)


def reset() -> None:
    """Solo pruebas: la instancia real no se reabre sola tras un cleanup fallido."""
    global _halted
    _halted = False
    _alerts.clear()
