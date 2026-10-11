"""Snapshot de capabilities observado en el spike (S2.1).

No es el endpoint HTTP: eso lo sirve A2.2. Aquí solo está lo que el informe
midió de verdad. D4 sigue bloqueado, así que los máximos de subida y duración
no se publican. 200 MiB, 3 h y 10 min no aparecen como límites validados.
A2.2, mientras `limits_validated` es false, exige listas vacías: este dict no
se publica tal cual en `GET /ingestion/capabilities`.
"""

from __future__ import annotations

from maulwurf_ingest.audio.validate import ACCEPT_CONTAINERS, NORMALIZE_CONTAINERS

_SOURCE_REPORT = "docs/spike/F0.1-informe-riva.md"


def spike_capabilities() -> dict[str, object]:
    """Shape de DISENO §3.5 con los campos no aprobados en null."""
    return {
        "source_report": _SOURCE_REPORT,
        "source_version": None,
        "limits_validated": False,
        "max_upload_bytes": None,
        "max_duration_seconds": None,
        "accepted_input_formats": [*ACCEPT_CONTAINERS, *NORMALIZE_CONTAINERS],
        "languages": ["es", "en", "fr"],
        "language_policy": "explicit_select_required_no_multi",
        "ttl": {
            "upload_start_minutes": None,
            "receive_minutes": None,
            "asr_minutes": None,
        },
        "asr": {
            "provider": "nvidia-riva",
            "model": "whisper-large-v3",
            "timestamp_precision": None,
        },
        "active_slots": None,
        "required_stages": ["index"],
    }
