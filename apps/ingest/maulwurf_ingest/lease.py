"""Lease, heartbeat y fencing de `ingestion_attempts` (S1.B3, contrato S1.md §1.4).

Cada escritura del supervisor es una sola sentencia condicionada, así que la BD decide
quién es el propietario, no el reloj del proceso:

- `acquire` solo toma un intento activo sin lease o con lease vencido, e **incrementa**
  `fencing_token`. Si un propietario viejo vuelve, su token ya no coincide.
- `heartbeat` y `transition` (publicar un estado) exigen token igual **y** lease vigente:
  un propietario vencido no puede publicar, aunque nadie haya tomado el intento.
- `record_cleanup` exige solo el token: la evidencia de limpieza es real aunque el lease
  haya vencido, siempre que nadie más haya tomado el intento. Que el lease venza nunca
  marca `verified` por sí mismo; solo lo hace esta evidencia.

SQL parametrizado con asyncpg; el esquema lo migra la API (`apps/api/alembic`).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

ACTIVE_STATES = (
    "awaiting_upload",
    "receiving",
    "transcribing",
    "transcript_committed_cleanup_pending",
)


@dataclass(frozen=True)
class Lease:
    attempt_id: uuid.UUID
    user_id: uuid.UUID
    instance: str
    fencing_token: int
    ttl: timedelta


async def acquire(
    pool: asyncpg.Pool, *, user_id: uuid.UUID, attempt_id: uuid.UUID, instance: str,
    ttl: timedelta,
) -> Lease | None:
    row = await pool.fetchrow(
        """
        UPDATE ingestion_attempts
           SET owner_instance = $3, fencing_token = fencing_token + 1,
               lease_expires_at = now() + $4, heartbeat_at = now(), updated_at = now()
         WHERE id = $1 AND user_id = $2 AND status = ANY($5::text[])
           AND (lease_expires_at IS NULL OR lease_expires_at <= now())
        RETURNING fencing_token
        """,
        attempt_id, user_id, instance, ttl, list(ACTIVE_STATES),
    )
    if row is None:
        return None
    return Lease(attempt_id, user_id, instance, row["fencing_token"], ttl)


async def heartbeat(pool: asyncpg.Pool, lease: Lease) -> bool:
    status = await pool.execute(
        """
        UPDATE ingestion_attempts
           SET lease_expires_at = now() + $4, heartbeat_at = now()
         WHERE id = $1 AND user_id = $2 AND fencing_token = $3 AND lease_expires_at > now()
        """,
        lease.attempt_id, lease.user_id, lease.fencing_token, lease.ttl,
    )
    return bool(status == "UPDATE 1")


async def transition(
    pool: asyncpg.Pool, lease: Lease, *, from_status: str, to_status: str,
    error_code: str | None = None, fragments_done: int | None = None,
    fragments_total: int | None = None,
) -> bool:
    """Publica un cambio de estado; False si el lease no es de este propietario o venció."""
    status = await pool.execute(
        """
        UPDATE ingestion_attempts
           SET status = $5, error_code = COALESCE($6, error_code),
               fragments_done = COALESCE($7, fragments_done),
               fragments_total = COALESCE($8, fragments_total), updated_at = now()
         WHERE id = $1 AND user_id = $2 AND fencing_token = $3 AND status = $4
           AND lease_expires_at > now()
        """,
        lease.attempt_id, lease.user_id, lease.fencing_token, from_status, to_status,
        error_code, fragments_done, fragments_total,
    )
    return bool(status == "UPDATE 1")


async def record_cleanup(
    pool: asyncpg.Pool, lease: Lease, *, verified: bool, evidence: dict[str, object],
    checked_at: datetime,
) -> bool:
    status = await pool.execute(
        """
        UPDATE ingestion_attempts
           SET cleanup_status = $4::text, cleanup_evidence = $5::jsonb,
               cleanup_verified_at = CASE WHEN $4 = 'verified' THEN $6::timestamptz END,
               audio_deleted_at = CASE WHEN $4 = 'verified' THEN $6::timestamptz END,
               updated_at = now()
         WHERE id = $1 AND user_id = $2 AND fencing_token = $3
        """,
        lease.attempt_id, lease.user_id, lease.fencing_token,
        "verified" if verified else "failed", json.dumps(evidence), checked_at,
    )
    return bool(status == "UPDATE 1")
