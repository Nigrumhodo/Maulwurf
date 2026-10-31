"""J3.5: deterministic `[Maulwurf]` Google Calendar event content.

The idempotent sync job (J3.4) owns lookup/insert/reconcile; this module only turns one
confirmed task version into the body Google expects, so the same task version always
produces byte-identical content:

- **Title.** Exactly one `[Maulwurf]` prefix, trimmed, within Google's 1024-character
  summary limit.
- **Description.** The task detail plus its type, subject and due date, and an absolute
  link to the textual segment. The link carries only `?segment=` and relies on the reader's
  authenticated session: no token, no audio URL and no transcript copy is written to
  Google. Manual tasks have no source, so they get no link.
- **All-day.** `start.date`/`end.date` from the confirmed block, with an exclusive end.
- **Timed.** RFC 3339 instants (UTC) plus an explicit IANA `timeZone`, so the event keeps
  its instant across DST and renders in the class zone.
- **No invented duration.** The confirmed `event_block` is emitted verbatim. A missing or
  inconsistent block is rejected instead of falling back to a default duration; the
  editable default is chosen in review (D3.2) before confirming.

Contract errors carry a closed `code` so the job can persist a reason without storing
provider payloads.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.reader_links import class_reader_path

EVENT_TITLE_PREFIX = "[Maulwurf]"
# Google Calendar limits: summary 1024 characters, description 8192.
TITLE_MAX_LENGTH = 1024
DESCRIPTION_MAX_LENGTH = 8192

_EVENT_BLOCK_MISSING = "event_block_missing"
_EVENT_BLOCK_INVALID = "event_block_invalid"
_TITLE_MISSING = "title_missing"
_TIMEZONE_INVALID = "timezone_invalid"
_DUE_INVALID = "due_invalid"
_PUBLIC_ORIGIN_INVALID = "public_origin_invalid"


class CalendarEventContentError(ValueError):
    """A task version cannot become a Calendar event without inventing data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TaskSegmentRef:
    """Reader target for the task evidence; `ordinal` is the stable segment number."""

    segment_id: uuid.UUID
    ordinal: int


@dataclass(frozen=True, slots=True)
class TaskEventContent:
    """The confirmed task fields the Calendar body depends on.

    The event mode comes from the `event_*` block, never from the due pair: a task may have
    an all-day deadline and a timed agenda block.
    """

    title: str
    timezone: str
    tipo: str
    detail: str | None = None
    subject_name: str | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    audio_id: uuid.UUID | None = None
    segment: TaskSegmentRef | None = None
    event_start_at: datetime | None = None
    event_end_at: datetime | None = None
    event_start_date: date | None = None
    event_end_date_exclusive: date | None = None


def build_event_content(
    task: TaskEventContent, *, public_origin: str
) -> dict[str, Any]:
    """Return the `summary`/`description`/`start`/`end` body for one confirmed task."""
    summary = _summary(task.title)
    zone = _timezone(task.timezone)
    _validate_due(task)
    start, end = _event_block(task)
    link = _reader_line(task, public_origin=public_origin)
    return {
        "summary": summary,
        "description": _description(task, zone=zone, link=link),
        "start": start,
        "end": end,
    }


def _summary(title: str) -> str:
    cleaned = title.strip()
    while cleaned.startswith(EVENT_TITLE_PREFIX):
        cleaned = cleaned[len(EVENT_TITLE_PREFIX) :].strip()
    if not cleaned:
        raise CalendarEventContentError(
            _TITLE_MISSING, "La tarea no tiene título para el evento."
        )
    summary = f"{EVENT_TITLE_PREFIX} {cleaned}"
    return summary[:TITLE_MAX_LENGTH].rstrip()


def _timezone(key: str) -> ZoneInfo:
    cleaned = key.strip()
    if not cleaned:
        raise CalendarEventContentError(
            _TIMEZONE_INVALID, "La tarea no tiene zona horaria IANA."
        )
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarEventContentError(
            _TIMEZONE_INVALID, "La zona horaria de la tarea no es IANA."
        ) from exc


def _validate_due(task: TaskEventContent) -> None:
    if task.due_date is not None and task.due_at is not None:
        raise CalendarEventContentError(
            _DUE_INVALID, "La fecha límite no puede ser día completo e instante a la vez."
        )
    if task.due_at is not None and not _is_aware(task.due_at):
        raise CalendarEventContentError(
            _DUE_INVALID, "La fecha límite con hora debe ser un instante con zona."
        )


def _event_block(task: TaskEventContent) -> tuple[dict[str, str], dict[str, str]]:
    timed = task.event_start_at is not None or task.event_end_at is not None
    all_day = (
        task.event_start_date is not None or task.event_end_date_exclusive is not None
    )
    if not timed and not all_day:
        raise CalendarEventContentError(
            _EVENT_BLOCK_MISSING,
            "La tarea confirmada no tiene bloque de agenda; no se inventa una duración.",
        )
    if timed and all_day:
        raise CalendarEventContentError(
            _EVENT_BLOCK_INVALID, "El bloque de agenda mezcla día completo e instante."
        )
    if timed:
        start_at, end_at = task.event_start_at, task.event_end_at
        if start_at is None or end_at is None:
            raise CalendarEventContentError(
                _EVENT_BLOCK_INVALID, "El bloque con hora está incompleto."
            )
        if not _is_aware(start_at) or not _is_aware(end_at) or end_at <= start_at:
            raise CalendarEventContentError(
                _EVENT_BLOCK_INVALID,
                "El bloque con hora requiere instantes con zona y fin posterior.",
            )
        return (
            {"dateTime": _rfc3339(start_at), "timeZone": task.timezone.strip()},
            {"dateTime": _rfc3339(end_at), "timeZone": task.timezone.strip()},
        )
    start_date, end_date = task.event_start_date, task.event_end_date_exclusive
    if start_date is None or end_date is None or end_date <= start_date:
        raise CalendarEventContentError(
            _EVENT_BLOCK_INVALID,
            "El bloque de día completo requiere un fin exclusivo posterior al inicio.",
        )
    return {"date": start_date.isoformat()}, {"date": end_date.isoformat()}


def _reader_line(task: TaskEventContent, *, public_origin: str) -> str | None:
    if task.audio_id is None:
        return None
    origin = _origin(public_origin)
    segment_id = task.segment.segment_id if task.segment is not None else None
    url = f"{origin}{class_reader_path(task.audio_id, segment_id)}"
    if task.segment is None:
        return f"Ver la clase en Maulwurf: {url}"
    return f"Ver segmento {task.segment.ordinal} en Maulwurf: {url}"


def _origin(public_origin: str) -> str:
    candidate = public_origin.strip()
    parts = urlsplit(candidate)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        raise CalendarEventContentError(
            _PUBLIC_ORIGIN_INVALID,
            "El origen público debe ser un origen http(s) sin ruta, query ni fragmento.",
        )
    return candidate.rstrip("/")


def _description(
    task: TaskEventContent, *, zone: ZoneInfo, link: str | None
) -> str:
    blocks: list[str] = []
    detail = (task.detail or "").strip()
    if detail:
        blocks.append(detail)
    meta = " · ".join(
        part for part in (task.subject_name, task.tipo) if part and part.strip()
    )
    due = _due_line(task, zone=zone)
    context = "\n".join(part for part in (meta, due) if part)
    if context:
        blocks.append(context)
    body = "\n\n".join(blocks)
    suffix = f"\n\n{link}" if link else ""
    budget = DESCRIPTION_MAX_LENGTH - len(suffix)
    if len(body) > budget:
        # Keep the reader link intact: only the task text is shortened.
        body = body[: max(0, budget - 1)].rstrip() + "…"
    if not body:
        return link or ""
    return f"{body}{suffix}"


def _due_line(task: TaskEventContent, *, zone: ZoneInfo) -> str | None:
    if task.due_at is not None:
        local = task.due_at.astimezone(zone)
        return f"Fecha límite: {local.strftime('%Y-%m-%d %H:%M')} ({task.timezone.strip()})"
    if task.due_date is not None:
        return f"Fecha límite: {task.due_date.isoformat()}"
    return None


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _rfc3339(value: datetime) -> str:
    text = value.astimezone(UTC).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text
