"""Unit support for S3.6: ASR quality warnings exposed to the inbox/class page.

The persisted signal must stay non-sensitive (code + counts, never spoken text) and must
fail closed on rows that do not have the expected shape.
"""

import pytest

from app.services.quality import normalize_quality_warnings


def test_known_warnings_keep_code_and_counts() -> None:
    raw = [
        {"code": "repetition", "count": 3},
        {"code": "low_confidence", "count": 4, "total": 10},
        {"code": "empty_segments", "count": 1},
    ]

    assert normalize_quality_warnings(raw) == (
        {"code": "repetition", "count": 3},
        {"code": "low_confidence", "count": 4, "total": 10},
        {"code": "empty_segments", "count": 1},
    )


def test_unknown_fields_never_leak_transcript_text() -> None:
    raw = [{"code": "repetition", "count": 3, "text": "La la la derivada"}]

    normalized = normalize_quality_warnings(raw)

    assert normalized == ({"code": "repetition", "count": 3},)
    assert "derivada" not in str(normalized)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        "not-a-list",
        [None, "text", 3],
        [{"count": 3}],
        [{"code": ""}],
        [{"code": "x" * 65}],
        [{"code": 3, "count": 1}],
    ],
)
def test_malformed_rows_are_dropped(raw: object) -> None:
    assert normalize_quality_warnings(raw) == ()


def test_counts_must_be_non_negative_integers() -> None:
    raw = [
        {"code": "repetition", "count": -1},
        {"code": "empty_segments", "count": True},
        {"code": "low_confidence", "count": 1.5, "total": 2},
        {"code": "low_confidence", "count": 2, "total": -1},
    ]

    assert normalize_quality_warnings(raw) == (
        {"code": "repetition"},
        {"code": "empty_segments"},
        {"code": "low_confidence", "total": 2},
        {"code": "low_confidence", "count": 2},
    )


def test_warning_list_is_bounded() -> None:
    raw = [{"code": "repetition", "count": index} for index in range(100)]

    normalized = normalize_quality_warnings(raw)

    assert len(normalized) == 20
    assert normalized[0] == {"code": "repetition", "count": 0}


@pytest.mark.parametrize(
    "code",
    [
        "unknown",
        "repetition_typo",
        "llm_provider_error",
        "x" * 64,
        "REPETITION",
    ],
)
def test_codes_outside_the_ingestion_allowlist_are_dropped(code: str) -> None:
    assert normalize_quality_warnings([{"code": code, "count": 1}]) == ()


def test_known_codes_survive_next_to_unknown_codes() -> None:
    raw = [
        {"code": "repetition", "count": 3},
        {"code": "made_up", "count": 9},
        {"code": "empty_segments", "count": 1},
    ]

    assert normalize_quality_warnings(raw) == (
        {"code": "repetition", "count": 3},
        {"code": "empty_segments", "count": 1},
    )
