"""Supporting unit cases for A3.5's revision identity rule.

The real behaviour (row locks, partial unique index, base version) is I-S3-AN-04 in the
integration suite. These cases pin the pure payload comparison the service uses to avoid
churning an equivalent pending revision.
"""

from app.services.revisions import same_proposed_fields


def test_equal_payloads_are_the_same_proposal() -> None:
    payload = {
        "tipo": "examen",
        "titulo": "Midterm",
        "detalle": None,
        "due_date": "2026-09-21",
        "due_at": None,
        "timezone": "America/Bogota",
        "all_day": True,
        "date_status": "resolved",
        "confidence_score": 0.5,
        "date_reason": None,
    }
    assert same_proposed_fields(payload, dict(payload)) is True


def test_an_explicit_null_is_not_a_missing_field() -> None:
    assert same_proposed_fields({"detalle": None}, {}) is False


def test_a_changed_nested_value_is_a_new_proposal() -> None:
    assert (
        same_proposed_fields(
            {"subject_id": "8f0c", "date_text": "Monday, September 21"},
            {"subject_id": "8f0c", "date_text": "Tuesday, September 22"},
        )
        is False
    )
