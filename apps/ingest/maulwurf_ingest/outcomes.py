"""Persisted ingest error codes (S2.13).

Every ``error_code`` the ingest service writes is a key of ``CATALOG``. The status
is the attempt's final status. ``http_status`` and ``http_code`` are the public
mapping the API applies; a code that surfaces after the connection is already
gone still has a mapping so an unexpected ``SinkError`` cannot escape the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

_FORMAT = "La ingesta rechazó el formato del audio."
_CAPACITY = "La recepción de audio no está disponible; inténtalo más tarde."
_INTERNAL = "No se pudo procesar la recepción."
_REUPLOAD = "No se pudo transcribir el audio. Vuelve a subirlo."
_CANCELLED = "La recepción se canceló. Vuelve a subir el audio."


@dataclass(frozen=True)
class ErrorSpec:
    status: str
    http_status: int
    http_code: str
    message: str


def _format() -> ErrorSpec:
    return ErrorSpec("rejected", 415, "unsupported_format", _FORMAT)


def _technical() -> ErrorSpec:
    return ErrorSpec("requires_reupload", 500, "internal_error", _REUPLOAD)


def _capacity() -> ErrorSpec:
    return ErrorSpec("requires_reupload", 503, "capacity_unavailable", _CAPACITY)


def _cancelled(code: str) -> ErrorSpec:
    return ErrorSpec("cancelled", 500, code, _CANCELLED)


# Probe reasons that blame the file, plus the outcomes the supervisor and the
# reception endpoints persist. ``probe_unavailable`` is an infrastructure fault.
CATALOG: dict[str, ErrorSpec] = {
    "url": _format(),
    "playlist": _format(),
    "malformed": _format(),
    "unverified_container": _format(),
    "not_pcm_s16": _format(),
    "multi_channel": _format(),
    "unexpected_container": _format(),
    "payload_too_large": ErrorSpec(
        "rejected", 413, "payload_too_large", "El audio supera el límite de recepción.",
    ),
    "no_usable_text": ErrorSpec(
        "rejected", 409, "transcript_unavailable", "El audio no tiene texto utilizable.",
    ),
    "variant_hash_mismatch": ErrorSpec(
        "rejected",
        422,
        "variant_hash_mismatch",
        "El contenido no coincide con la clase confirmada; vuelve a subir el archivo original.",
    ),
    "probe_unavailable": _capacity(),
    "asr_failed": _technical(),
    "asr_timeout": _technical(),
    "asr_rejected": _technical(),
    "supervisor_error": _technical(),
    "reception_abandoned": _technical(),
    "reception_integrity_failed": ErrorSpec(
        "requires_reupload", 500, "internal_error", _INTERNAL,
    ),
    "api_error": _technical(),
    "reception_interrupted": _cancelled("reception_interrupted"),
    "client_disconnected": _cancelled("client_disconnected"),
    "duplicate_exact": _cancelled("duplicate_exact"),
    "duplicate_variant": _cancelled("duplicate_variant"),
}

# Answers that are not stored in ``error_code``. The API still maps them.
HTTP_ONLY: dict[str, ErrorSpec] = {
    "attempt_not_active": ErrorSpec(
        "awaiting_upload", 409, "attempt_not_active", "El intento de subida ya no admite audio.",
    ),
    "capacity_unavailable": ErrorSpec(
        "awaiting_upload", 503, "capacity_unavailable", _CAPACITY,
    ),
    "internal_credential_invalid": ErrorSpec(
        "awaiting_upload", 500, "internal_error", _INTERNAL,
    ),
    "wrong_instance": ErrorSpec("awaiting_upload", 503, "capacity_unavailable", _CAPACITY),
    "reception_not_held": ErrorSpec(
        "awaiting_upload", 503, "capacity_unavailable", _CAPACITY,
    ),
    "unsupported_format": _format(),
    "validation_failed": ErrorSpec(
        "awaiting_upload", 422, "validation_failed", "Hay campos inválidos.",
    ),
}

DISCARD_REASONS = frozenset({
    "duplicate_exact",
    "duplicate_variant",
    "variant_hash_mismatch",
    "client_disconnected",
    "reception_integrity_failed",
    "api_error",
})

PROBE_REASONS = frozenset({
    "url",
    "playlist",
    "malformed",
    "unverified_container",
    "not_pcm_s16",
    "multi_channel",
    "unexpected_container",
    "probe_unavailable",
})


def spec_for(code: str) -> ErrorSpec:
    """Return the catalog row. An unknown code is a programming error."""
    try:
        return CATALOG[code]
    except KeyError:
        raise KeyError(code) from None
