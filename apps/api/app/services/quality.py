"""Non-sensitive ASR quality signal for the inbox and class page (S3.6).

Ingestion persists warnings as `{code, count[, total]}` objects; the API normalizes them
before exposing them. Unknown fields are dropped on purpose: a malformed row must never
turn into a path for transcript text, provider payloads or other private data to reach a
response, and a bad row is ignored instead of failing the whole class read.
"""

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, model_serializer

# Bounded list: the UI only needs the signals, and a corrupt row cannot flood the response.
MAX_QUALITY_WARNINGS: Final = 20
QualityCode = Literal["repetition", "empty_segments", "low_confidence"]
# Ingestion only emits these codes; unknown ones are dropped instead of being echoed back.
_KNOWN_CODES: Final = frozenset({"repetition", "empty_segments", "low_confidence"})
_COUNT_FIELDS = ("count", "total")


class QualityWarning(BaseModel):
    """One non-sensitive ASR signal. Absent counts stay absent in JSON."""

    model_config = ConfigDict(extra="ignore")

    code: QualityCode
    count: int | None = None
    total: int | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_counts(self, handler: Any) -> dict[str, Any]:
        dumped = handler(self)
        return {key: value for key, value in dumped.items() if value is not None}


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def normalize_quality_warnings(raw: object) -> tuple[QualityWarning, ...]:
    """Keep valid `{code, count[, total]}` warnings and drop everything else."""
    if not isinstance(raw, list):
        return ()
    normalized: list[QualityWarning] = []
    for item in raw:
        if len(normalized) >= MAX_QUALITY_WARNINGS:
            break
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in _KNOWN_CODES:
            continue
        normalized.append(
            QualityWarning(
                code=code,
                count=_count(item.get("count")),
                total=_count(item.get("total")),
            )
        )
    return tuple(normalized)
