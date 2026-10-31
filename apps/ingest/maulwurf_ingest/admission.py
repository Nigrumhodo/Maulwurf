"""Pestillo de admisión de la instancia de ingesta (S1.B6).

`cleanup_status=failed` cierra la admisión de uploads nuevos y deja una alerta.
Un lease vencido que deja el intento en `pending` no cierra la admisión: solo
bloquea la outbox de índice y análisis. Si ese cleanup no verificado tampoco pudo
persistirse porque otro propietario movió el token (`cleanup_unrecorded`), la
alerta explícita y su métrica dejan rastro sin latch durable: no hay fila `failed`
que justifique cerrar la instancia. S2 consultará `uploads_admitted()`; el `PUT` de
S1 sigue respondiendo `503 capacity_unavailable` por A1.8.

Since S4.2 there are two separate states:

- ``_halted`` is the durable latch: it only turns on when a ``cleanup_status=failed``
  row exists (or this run persisted one) and it is sticky for the process lifetime.
- ``_transient_closed`` is the fail-closed state used when the latch cannot be read
  (database error). A later successful read clears it, so a transient outage does
  not require a restart; the durable latch is never cleared by that read.

The periodic sweeper calls ``restore_from_database`` on every pass, so a ``failed``
row persisted by another instance also closes admission here. The specification
says F0/F1 deploys a single ingestion instance (``docs/ESPECIFICACION.md``, F1
ingestion bullet: "F0/F1 deploys a single ingestion instance; scaling requires
routing to the reservation owner"), but the durable row is authoritative and the
extra read costs one indexed query per pass.
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
_transient_closed = False
_alerts: list[Alert] = []


def uploads_admitted() -> bool:
    """False after a persisted `failed` cleanup or while the latch is unreadable."""
    return not (_halted or _transient_closed)


def alerts() -> tuple[Alert, ...]:
    return tuple(_alerts)


def halt(attempt_id: uuid.UUID) -> None:
    """Cierra la admisión de forma durable y registra la alerta. Sin rutas ni audio.

    Idempotente por intento y código: el sweeper vuelve a leer la misma fila `failed`
    en cada pase y no debe duplicar la alerta ni el log.
    """
    global _halted
    _halted = True
    if any(
        alert.code == "cleanup_failed" and alert.attempt_id == attempt_id
        for alert in _alerts
    ):
        return
    _alerts.append(Alert("cleanup_failed", attempt_id))
    logger.error("ingest.alert code=cleanup_failed attempt_id=%s", attempt_id)


def flag_unrecorded_cleanup(attempt_id: uuid.UUID) -> None:
    """Raise an explicit alert for an unverified cleanup this run could not persist.

    The row stays ``pending`` because a newer owner moved the fencing token (the
    stranded sweep or a reclaim), so there is no durable ``failed`` latch and
    admission stays open. The occurrence must still be observable: the alert is
    returned by ``admission.alerts()`` and logged, and the supervisor increments a
    failure counter exposed by ``/internal/metrics``; the case no longer leaves only
    a warning. Idempotent per attempt and code.
    """
    if any(
        alert.code == "cleanup_unrecorded" and alert.attempt_id == attempt_id
        for alert in _alerts
    ):
        return
    _alerts.append(Alert("cleanup_unrecorded", attempt_id))
    logger.error("ingest.alert code=cleanup_unrecorded attempt_id=%s", attempt_id)


def close() -> None:
    """Fail closed when the persisted latch cannot be read. No attempt id is invented.

    This is the transient half of the latch: a later successful read reopens
    admission unless a durable ``failed`` row exists.
    """
    global _transient_closed
    _transient_closed = True
    logger.error("ingest.alert code=admission_closed cause=latch_unreadable")


def reopen() -> None:
    """Clear a transient close. A durable halt is never reopened here."""
    global _transient_closed
    _transient_closed = False


async def restore_from_database(pool: asyncpg.Pool) -> None:
    """Sync admission with the persisted latch.

    A ``cleanup_status=failed`` row closes admission (durable and sticky) even when
    this instance did not persist it. A successful read with no failed row clears a
    previous transient close, so one failed database pass does not require a restart.
    A read error closes admission until the next successful read.
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
        return
    reopen()


def reset() -> None:
    """Solo pruebas: la instancia real no se reabre sola tras un cleanup fallido."""
    global _halted, _transient_closed
    _halted = False
    _transient_closed = False
    _alerts.clear()
