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

import asyncpg

from maulwurf_ingest import rls

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


def close() -> None:
    """Fail closed when the persisted latch cannot be read. No attempt id is invented."""
    global _halted
    _halted = True
    logger.error("ingest.alert code=cleanup_failed")


async def restore_from_database(pool: asyncpg.Pool) -> None:
    """A persisted ``cleanup_status=failed`` keeps admission closed across restarts.

    A database error also closes it. An empty result leaves the in-memory latch as it is,
    so a process that already halted does not reopen itself.
    """
    try:
        row = await rls.fetchrow(pool, None,
            """
            SELECT id FROM ingestion_attempts
             WHERE cleanup_status = 'failed'
             LIMIT 1
            """
        )
    except Exception:  # noqa: BLE001 — a failed read must not reopen admission
        logger.error("admission.restore_failed")
        close()
        return
    if row is not None and isinstance(row["id"], uuid.UUID):
        halt(row["id"])


def reset() -> None:
    """Solo pruebas: la instancia real no se reabre sola tras un cleanup fallido."""
    global _halted
    _halted = False
    _alerts.clear()
