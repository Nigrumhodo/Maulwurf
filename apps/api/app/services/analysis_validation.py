"""Deterministic post-LLM validation (L3.3).

The LLM response is untrusted. Enums, ranges and the quote-length rule are already enforced
by the L3.2 models; this module adds the checks those models cannot do alone:

- every cited segment belongs to the class, tenant and transcript version in the view;
- every span lies inside that segment's immutable text;
- every `cita_textual` is exactly `segment_text[inicio_char:fin_char]`.

`check_against` remains the pure comparison. The view is the class-scoped segment map, loaded
by `class_segments_statement` (and the transcript lookup that gives the map its version).
Persisting the proposal and proving the reconstruction against PostgreSQL stay in A3.2/A3.6.

Errors carry a closed `code` and a fixed message. They never include the transcript, the
quote, or the model payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, NoReturn

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audio, Segment, Transcript
from app.schemas.analysis import AnalysisResult

_MESSAGES: Final[dict[str, str]] = {
    "segment_not_in_class": "the cited segment is not in this class transcript version",
    "span_out_of_range": "the evidence span exceeds the segment text",
    "quote_mismatch": "the verbatim quote is not the segment slice",
    "transcript_not_in_class": "the transcript version is not in this class",
}

# `check_against` raises these fixed messages and nothing else. An unknown message still
# becomes `quote_mismatch` with the fixed text above, so a future wording change cannot
# start echoing the transcript through `str(exc)`.
_CHECK_CODES: Final[dict[str, str]] = {
    "segmento desconocido en la evidencia": "segment_not_in_class",
    "fin_char excede el texto del segmento": "span_out_of_range",
    "la cita textual no coincide con el segmento": "quote_mismatch",
}


class AnalysisValidationError(Exception):
    """A proposal failed a deterministic membership or quote check."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def reject(code: str, cause: BaseException | None = None) -> NoReturn:
    """Raise the fixed L3.3 error for a closed code.

    The DB-backed reconstruction (A3.6) uses the same errors as `validate_analysis`, so a
    caller cannot accidentally surface a different message or echo transcript text.
    """
    error = AnalysisValidationError(code, _MESSAGES[code])
    if cause is None:
        raise error
    raise error from cause


@dataclass(frozen=True)
class ClassTranscript:
    """One tenant's class at one transcript version, and nothing else.

    `segments` is the only text the validator may read. A segment that belongs to another
    class, tenant or version is simply absent, which is what makes membership fail closed.
    """

    user_id: uuid.UUID
    audio_id: uuid.UUID
    transcript_id: uuid.UUID
    transcript_version: int
    segments: Mapping[uuid.UUID, str]


def validate_analysis(result: AnalysisResult, view: ClassTranscript) -> None:
    """Reject an invented segment, an out-of-range span or a quote that is not the slice.

    :raises AnalysisValidationError: with a closed code. The message is fixed.
    """
    try:
        result.check_against(view.segments)
    except ValueError as exc:
        reject(_CHECK_CODES.get(str(exc), "quote_mismatch"), exc)


def class_transcript_statement(
    user_id: uuid.UUID, audio_id: uuid.UUID, transcript_version: int
) -> Select[tuple[uuid.UUID]]:
    """The transcript row for this tenant, class and version, or no row.

    `audios.deleted_at IS NULL` keeps a tombstoned class out of the view. `(audio_id,
    version)` is unique, so the loader can take the single id.
    """
    return (
        select(Transcript.id)
        .join(
            Audio,
            and_(Transcript.user_id == Audio.user_id, Transcript.audio_id == Audio.id),
        )
        .where(
            Audio.user_id == user_id,
            Audio.id == audio_id,
            Audio.deleted_at.is_(None),
            Transcript.version == transcript_version,
        )
    )


def class_segments_statement(
    user_id: uuid.UUID, audio_id: uuid.UUID, transcript_version: int
) -> Select[tuple[Segment]]:
    """Segments of one class transcript version, ordered by `ordinal`.

    The joins pin each segment to its transcript and that transcript to the class. A
    segment id reused under another user, audio or version cannot satisfy the predicates.
    """
    return (
        select(Segment)
        .join(
            Transcript,
            and_(
                Segment.user_id == Transcript.user_id,
                Segment.transcript_id == Transcript.id,
            ),
        )
        .join(
            Audio,
            and_(Transcript.user_id == Audio.user_id, Transcript.audio_id == Audio.id),
        )
        .where(
            Audio.user_id == user_id,
            Audio.id == audio_id,
            Audio.deleted_at.is_(None),
            Transcript.version == transcript_version,
        )
        .order_by(Segment.ordinal)
    )


async def load_class_transcript(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    transcript_version: int,
) -> ClassTranscript:
    """Load the segment map L3.3 validates against.

    No row for the version is `transcript_not_in_class`, not an empty class: an empty map
    would reject every quote the same way and hide a wrong version. A version that exists
    with no segments is a real (empty) view.

    :raises AnalysisValidationError: when the version is not in this class.
    """
    transcript_id = (
        await db.execute(class_transcript_statement(user_id, audio_id, transcript_version))
    ).scalar_one_or_none()
    if transcript_id is None:
        reject("transcript_not_in_class")
    rows = (
        await db.execute(class_segments_statement(user_id, audio_id, transcript_version))
    ).scalars().all()
    return ClassTranscript(
        user_id=user_id,
        audio_id=audio_id,
        transcript_id=transcript_id,
        transcript_version=transcript_version,
        segments={row.id: row.text for row in rows},
    )
