"""A3.3 unit decisions: optimistic locking, partial edits and revision payloads.

U-S3-AN-02 is the pure half of `PATCH /tasks/{id}`: a stale `version` is a typed
`409 version_conflict` with the current status, before any field is merged or written.
The endpoint half runs against PostgreSQL in `tests/integration/test_task_endpoints.py`.
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.models import Task
from app.schemas.tasks import (
    TaskCreate,
    TaskPatch,
    TaskSourceIn,
    TaskVersionBody,
)
from app.services.tasks import (
    TaskCursor,
    _coerce_revision,
    _current_fields,
    _decode_cursor,
    _encode_cursor,
    _merge_patch,
    _revision_payload,
    ensure_version,
)

DUE_DAY = date(2026, 11, 9)
DUE_AT = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


def _task(**overrides: object) -> Task:
    values: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "source": "manual",
        "tipo": "recordatorio",
        "titulo": "Repasar la unidad 3",
        "timezone": "America/Bogota",
        "all_day": False,
        "date_status": "missing",
    }
    values.update(overrides)
    return Task(**values)


def test_u_s3_an_02_stale_patch_version_is_a_409_with_the_current_status() -> None:
    with pytest.raises(ApiError) as failure:
        ensure_version(current_version=3, current_status="confirmed", expected_version=2)

    assert failure.value.status_code == 409
    assert failure.value.code == "version_conflict"
    assert failure.value.details == {"current_version": 3, "current_status": "confirmed"}


def test_the_current_patch_version_passes() -> None:
    ensure_version(current_version=3, current_status="confirmed", expected_version=3)


def test_a_partial_due_date_becomes_a_resolved_all_day_task() -> None:
    task = _task()
    merged = _merge_patch(_current_fields(task), TaskPatch(version=1, due_date=DUE_DAY))

    assert merged["date_status"] == "resolved"
    assert merged["all_day"] is True
    assert merged["due_date"] == DUE_DAY
    assert merged["due_at"] is None
    assert merged["date_text"] == "Monday, November 9"


def test_a_partial_instant_becomes_a_resolved_timed_task() -> None:
    task = _task()
    merged = _merge_patch(_current_fields(task), TaskPatch(version=1, due_at=DUE_AT))

    assert merged["date_status"] == "resolved"
    assert merged["all_day"] is False
    assert merged["due_at"] == DUE_AT
    assert merged["date_text"] == "Monday, November 9 at 10:00"


def test_clearing_both_due_fields_returns_to_missing() -> None:
    task = _task(date_status="resolved", all_day=True, due_date=DUE_DAY, date_text="old")
    merged = _merge_patch(
        _current_fields(task), TaskPatch(version=1, due_date=None, due_at=None)
    )

    assert merged["date_status"] == "missing"
    assert merged["all_day"] is False
    assert merged["due_date"] is None
    assert merged["date_text"] is None


def test_an_explicit_null_non_nullable_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskPatch(version=1, titulo=None)


def test_a_patch_requires_a_version() -> None:
    with pytest.raises(ValidationError):
        TaskPatch.model_validate({"titulo": "Sin versión"})


def test_the_revision_payload_is_json_serializable_and_round_trips() -> None:
    fields = {
        **_current_fields(_task()),
        "due_date": DUE_DAY,
        "due_at": None,
        "timezone": "America/Bogota",
        "all_day": True,
        "date_status": "resolved",
        "subject_id": uuid.uuid4(),
        "event_start_date": DUE_DAY,
        "event_end_date_exclusive": date(2026, 11, 10),
    }
    payload = _revision_payload(fields)

    assert payload["due_date"] == "2026-11-09"
    assert payload["due_at"] is None
    assert payload["event_start_date"] == "2026-11-09"
    assert isinstance(payload["subject_id"], str)
    assert _coerce_revision(payload)["due_date"] == DUE_DAY
    assert _coerce_revision(payload)["event_end_date_exclusive"] == date(2026, 11, 10)
    assert _coerce_revision(payload)["subject_id"] == uuid.UUID(payload["subject_id"])


def test_the_task_cursor_round_trips_and_rejects_garbage() -> None:
    task_id = uuid.uuid4()
    encoded = _encode_cursor(TaskCursor(DUE_AT, task_id))

    assert _decode_cursor(encoded) == TaskCursor(DUE_AT, task_id)
    with pytest.raises(ApiError) as failure:
        _decode_cursor("not-a-cursor")
    assert failure.value.code == "validation_failed"


def test_manual_tasks_cannot_cite_sources() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            source="manual",
            tipo="tarea",
            titulo="Manual con fuente",
            timezone="America/Bogota",
            sources=[
                TaskSourceIn(
                    segment_id=uuid.uuid4(),
                    char_start=0,
                    char_end=4,
                    quote="text",
                )
            ],
        )


def test_an_extraction_requires_a_source() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            source="extraction",
            tipo="tarea",
            titulo="Sin fuente",
            timezone="America/Bogota",
        )


def test_a_general_chat_suggestion_needs_no_source() -> None:
    task = TaskCreate(
        source="chat",
        tipo="recomendacion",
        titulo="Lee el capítulo 4",
        timezone="America/Bogota",
    )

    assert task.sources == []
    assert task.audio_id is None
    assert task.date_status.value == "missing"


def test_a_quote_must_measure_its_span() -> None:
    with pytest.raises(ValidationError):
        TaskSourceIn(
            segment_id=uuid.uuid4(),
            char_start=0,
            char_end=4,
            quote="texto",
        )


def test_a_version_body_requires_a_positive_version() -> None:
    with pytest.raises(ValidationError):
        TaskVersionBody(expected_version=0)
