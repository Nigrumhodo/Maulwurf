"""Presupuesto de RAM por slot (S1.B2).

slot = original + fragmentos activos + buffer de ffmpeg + buffer del SDK + margen.

El PCM de 3 h a 16 kHz mono s16 (345600000 bytes) no entra en el slot: la ingesta
no decodifica las 3 h enteras. 200 MiB y 3 h no se aprueban aquí.
"""

from __future__ import annotations

PCM_3H_16K_S16_BYTES = 3 * 3600 * 16000 * 2
FRAGMENT_1S_16K_S16_BYTES = 16000 * 2

PROVISIONAL_UPLOAD_BYTES = 200 * 1024 * 1024
PROVISIONAL_TMPFS_BYTES = 256 * 1024 * 1024
PROVISIONAL_SLOTS = 2
PROVISIONAL_MEM_LIMIT_BYTES = 1024 * 1024 * 1024

APPROVED_200_MIB = False
APPROVED_3H = False


def slot_bytes(
    *,
    original_bytes: int,
    active_fragments: int,
    fragment_bytes: int,
    ffmpeg_buffer_bytes: int,
    sdk_buffer_bytes: int,
    margin_bytes: int,
) -> int:
    """Suma el presupuesto de un slot. No incluye el PCM de 3 h."""
    parts = {
        "original_bytes": original_bytes,
        "active_fragments": active_fragments,
        "fragment_bytes": fragment_bytes,
        "ffmpeg_buffer_bytes": ffmpeg_buffer_bytes,
        "sdk_buffer_bytes": sdk_buffer_bytes,
        "margin_bytes": margin_bytes,
    }
    for name, value in parts.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    return (
        original_bytes
        + active_fragments * fragment_bytes
        + ffmpeg_buffer_bytes
        + sdk_buffer_bytes
        + margin_bytes
    )


def instance_bytes(slot: int, slots: int) -> int:
    """Presupuesto de la instancia: un slot por admisión concurrente."""
    if slot < 0 or slots < 0:
        raise ValueError("slot and slots must be non-negative")
    return slot * slots


def process_ram_bytes(
    *,
    heap_upload_copies: int,
    upload_bytes: int,
    runtime_overhead_bytes: int,
) -> int:
    """RAM del proceso que el slot de tmpfs no cuenta.

    ``run_attempt`` retiene ``audio: bytes`` en el heap. El pico del hijo
    ffmpeg no entra aquí: hay que medirlo aparte.
    """
    parts = {
        "heap_upload_copies": heap_upload_copies,
        "upload_bytes": upload_bytes,
        "runtime_overhead_bytes": runtime_overhead_bytes,
    }
    for name, value in parts.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    return heap_upload_copies * upload_bytes + runtime_overhead_bytes


def fits_limit(need_bytes: int, limit_bytes: int) -> bool:
    """True solo si el presupuesto cabe en el límite. No aprueba 200 MiB ni 3 h."""
    if need_bytes < 0 or limit_bytes < 0:
        raise ValueError("need_bytes and limit_bytes must be non-negative")
    return need_bytes <= limit_bytes
