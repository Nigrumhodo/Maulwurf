"""Non-sensitive ASR quality signal for the inbox and class page (S3.6).

Ingestion persists warnings as `{code, count[, total]}` objects; the API normalizes them
before exposing them. Unknown fields are dropped on purpose: a malformed row must never
turn into a path for transcript text, provider payloads or other private data to reach a
response, and a bad row is ignored instead of failing the whole class read.
"""

from typing import Any, Final

# Bounded list: the UI only needs the signals, and a corrupt row cannot flood the response.
MAX_QUALITY_WARNINGS: Final = 20
MAX_CODE_LENGTH: Final = 64
_COUNT_FIELDS = ("count", "total")


def normalize_quality_warnings(raw: object) -> tuple[dict[str, Any], ...]:
    """Keep valid `{code, count[, total]}` warnings and drop everything else."""
    if not isinstance(raw, list):
        return ()
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if len(normalized) >= MAX_QUALITY_WARNINGS:
            break
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code or len(code) > MAX_CODE_LENGTH:
            continue
        warning: dict[str, Any] = {"code": code}
        for field in _COUNT_FIELDS:
            value = item.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                warning[field] = value
        normalized.append(warning)
    return tuple(normalized)
