"""A3.7: the rules that make a task confirmable.

The task row can exist in several review states; only a version with a resolved date, a
valid Calendar block and the evidence its origin requires may become ``confirmed``. This
module is pure: the caller reads the task, applies any pending revision and passes the
effective values here. It never guesses a date, a duration or a quote.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.core.errors import ApiError
from app.core.validation import is_iana_timezone


@dataclass(frozen=True)
class TaskCandidate:
    """Effective task fields after applying a pending revision, if there is one."""

    source: str
    audio_id: UUID | None
    tipo: str
    titulo: str
    detalle: str | None
    date_status: str
    due_date: date | None
    due_at: datetime | None
    timezone: str
    all_day: bool
    event_start_at: datetime | None
    event_end_at: datetime | None
    event_start_date: date | None
    event_end_date_exclusive: date | None
    source_count: int


def _unresolved() -> ApiError:
    return ApiError(
        422,
        "date_unresolved",
        "Confirma una fecha resuelta antes de escribir la tarea en Calendar.",
        {"field": "date_status"},
    )


def _block_missing() -> ApiError:
    return ApiError(
        422,
        "event_block_missing",
        "La tarea necesita un bloque de agenda; no se inventa una duración.",
        {"field": "event_block"},
    )


def _block_invalid() -> ApiError:
    return ApiError(
        422,
        "event_block_invalid",
        "El bloque de agenda no es válido.",
        {"field": "event_block"},
    )


def _evidence_invalid() -> ApiError:
    return ApiError(
        422,
        "evidence_invalid",
        "La tarea necesita una cita textual verificable antes de confirmarse.",
        {"field": "sources"},
    )


def ensure_confirmable(candidate: TaskCandidate) -> None:
    """Fail closed unless the effective task can become a Calendar event verbatim."""
    if candidate.date_status != "resolved":
        raise _unresolved()
    if (candidate.due_date is None) == (candidate.due_at is None):
        raise _unresolved()
    if candidate.all_day != (candidate.due_date is not None):
        raise _unresolved()
    if candidate.due_at is not None and candidate.due_at.tzinfo is None:
        raise _unresolved()
    if not is_iana_timezone(candidate.timezone):
        raise _unresolved()

    timed = candidate.event_start_at is not None or candidate.event_end_at is not None
    all_day_block = (
        candidate.event_start_date is not None
        or candidate.event_end_date_exclusive is not None
    )
    if not timed and not all_day_block:
        raise _block_missing()
    if timed and all_day_block:
        raise _block_invalid()
    if timed:
        if candidate.event_start_at is None or candidate.event_end_at is None:
            raise _block_invalid()
        if (
            candidate.event_start_at.tzinfo is None
            or candidate.event_end_at.tzinfo is None
            or candidate.event_end_at <= candidate.event_start_at
        ):
            raise _block_invalid()
    else:
        if (
            candidate.event_start_date is None
            or candidate.event_end_date_exclusive is None
            or candidate.event_end_date_exclusive <= candidate.event_start_date
        ):
            raise _block_invalid()

    if candidate.source == "extraction" and candidate.source_count == 0:
        raise _evidence_invalid()
    if candidate.source == "chat" and candidate.audio_id is not None:
        if candidate.source_count == 0:
            raise _evidence_invalid()
