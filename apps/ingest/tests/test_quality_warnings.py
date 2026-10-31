"""U-S3-SG-05 (ingest side): ASR quality warnings marked without storing words.

The warning payload carries a code and counts only. A missing confidence is «unknown»,
never «low»; empty word entries are counted but not persisted as segments.
"""

import json

import pytest

from maulwurf_ingest.persistence import (
    TranscriptDraft,
    TranscriptWord,
    draft_from_job,
    prepare_transcript,
    quality_warnings,
)


def _draft(*words: TranscriptWord, text: str = "") -> TranscriptDraft:
    return TranscriptDraft(
        text=text,
        precision="word" if words else "none",
        words=words,
        model="whisper-large-v3",
    )


def test_low_confidence_is_flagged_without_the_words() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("La", 0.0, 0.2, confidence=0.21),
        TranscriptWord("derivada", 0.3, 0.8, confidence=0.35),
    ))

    assert prepared is not None
    assert prepared.warnings == (
        {"code": "low_confidence", "count": 2, "total": 2},
    )
    assert "La" not in json.dumps(list(prepared.warnings))
    assert "derivada" not in json.dumps(list(prepared.warnings))


def test_confident_words_do_not_warn() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("La", 0.0, 0.2, confidence=0.91),
        TranscriptWord("derivada", 0.3, 0.8, confidence=0.84),
    ))

    assert prepared is not None
    assert prepared.warnings == ()


def test_a_majority_of_low_confidence_words_is_required() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("La", 0.0, 0.2, confidence=0.2),
        TranscriptWord("derivada", 0.3, 0.8, confidence=0.95),
        TranscriptWord("parcial", 0.9, 1.4, confidence=0.93),
    ))

    assert prepared is not None
    assert prepared.warnings == ()


def test_missing_confidence_is_unknown_not_low() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("La", 0.0, 0.2),
        TranscriptWord("derivada", 0.3, 0.8),
    ))

    assert prepared is not None
    assert prepared.warnings == ()


def test_empty_word_entries_are_counted_but_not_stored() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("", 0.0, 0.1),
        TranscriptWord("   ", 0.1, 0.2),
        TranscriptWord("derivada", 0.3, 0.8, confidence=0.9),
    ))

    assert prepared is not None
    assert prepared.text == "derivada"
    assert prepared.segments[0].text == "derivada"
    assert prepared.warnings == ({"code": "empty_segments", "count": 2},)


def test_repetition_is_marked_together_with_quality_warnings() -> None:
    prepared = prepare_transcript(_draft(
        TranscriptWord("la", 0.0, 0.2, confidence=0.2),
        TranscriptWord("la", 0.3, 0.5, confidence=0.2),
        TranscriptWord("la", 0.6, 0.8, confidence=0.2),
    ))

    assert prepared is not None
    assert [warning["code"] for warning in prepared.warnings] == [
        "repetition",
        "low_confidence",
    ]


def test_quality_warnings_are_optional_for_text_only_transcripts() -> None:
    assert quality_warnings("la la la derivada", ()) == (
        {"code": "repetition", "count": 3},
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (0.42, 0.42),
        ("0.42", None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
        (-0.1, None),
        (1.1, None),
    ],
)
def test_draft_from_job_reads_confidence_fail_closed(
    raw: object, expected: float | None
) -> None:
    draft = draft_from_job(
        {
            "text": "La derivada",
            "precision": "word",
            "model": "whisper-large-v3",
            "words": [{"text": "La", "start_s": 0.0, "end_s": 0.2, "confidence": raw}],
        }
    )

    assert draft is not None
    assert draft.words[0].confidence == expected
