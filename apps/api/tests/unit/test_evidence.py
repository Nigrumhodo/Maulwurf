"""U-S3-SG-03: no ASR offsets — textual evidence and zero invented minutes (S3.3/M4).

The evidence layer must resolve the same non-temporal identity the analysis produces —
segment, Unicode span and verbatim quote — when `timestamp_precision=none` or when a stored
pair is not truthful. Times are never interpolated, shared with a neighbour or rendered as
`00:00`; the visible reference falls back to "segment N".
"""

import json
import math
import uuid
from datetime import date

import pytest

from app.services.evidence import (
    EvidenceInvalid,
    EvidenceSpan,
    SegmentText,
    evidence_label,
    evidence_reference,
    format_timestamp,
    reliable_times,
    resolve_textual_evidence,
)

SEGMENT_A = uuid.UUID("3f1c9a52-7d4e-4b8a-9c1e-2a6b5d8e0f11")
SEGMENT_B = uuid.UUID("9b2e4c71-1a3f-4d6e-8b5c-7e0a2f9d4c33")
SEGMENT_C = uuid.UUID("c4d5e6f7-8a9b-4c0d-9e1f-2a3b4c5d6e7f")

TEXT_A = "La integral definida acumula área."
TEXT_B = "El examen 🎓 será el lunes en el aula 204."
TEXT_C = "Traer calculadora y repasar la tabla."


def _segments(
    *,
    a_times: tuple[float | None, float | None] = (0.0, 4.0),
    b_times: tuple[float | None, float | None] = (None, None),
    c_times: tuple[float | None, float | None] = (10.0, 14.0),
) -> list[SegmentText]:
    return [
        SegmentText(
            segment_id=SEGMENT_A,
            ordinal=1,
            text=TEXT_A,
            char_start=0,
            char_end=len(TEXT_A),
            t_start=a_times[0],
            t_end=a_times[1],
        ),
        SegmentText(
            segment_id=SEGMENT_B,
            ordinal=2,
            text=TEXT_B,
            char_start=len(TEXT_A) + 1,
            char_end=len(TEXT_A) + 1 + len(TEXT_B),
            t_start=b_times[0],
            t_end=b_times[1],
        ),
        SegmentText(
            segment_id=SEGMENT_C,
            ordinal=3,
            text=TEXT_C,
            char_start=len(TEXT_A) + len(TEXT_B) + 2,
            char_end=len(TEXT_A) + len(TEXT_B) + 2 + len(TEXT_C),
            t_start=c_times[0],
            t_end=c_times[1],
        ),
    ]


def _span(segment_id: uuid.UUID, inicio: int, fin: int) -> EvidenceSpan:
    return EvidenceSpan(segment_id=segment_id, inicio_char=inicio, fin_char=fin)


def test_no_offsets_still_yield_textual_evidence() -> None:
    segments = _segments(a_times=(None, None), b_times=(None, None), c_times=(None, None))
    inicio = TEXT_B.index("examen")
    fin = TEXT_B.index("lunes") + len("lunes")

    evidence = resolve_textual_evidence(
        spans=[_span(SEGMENT_B, inicio, fin)],
        segments=segments,
        timestamp_precision="none",
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.segment_id == SEGMENT_B
    assert item.ordinal == 2
    assert item.quote == "examen 🎓 será el lunes"
    assert item.t_start is None
    assert item.t_end is None
    assert item.reference == "segment 2"
    assert item.as_source()["t_start"] is None
    assert item.as_source()["t_end"] is None
    assert item.as_source()["label"] == "segment 2"
    assert "00:00" not in json.dumps(item.as_source(), ensure_ascii=False)


def test_unicode_span_uses_code_points_and_absolute_transcript_offsets() -> None:
    segment_offset = len(TEXT_A) + 1
    inicio = TEXT_B.index("🎓")
    fin = inicio + len("🎓")

    evidence = resolve_textual_evidence(
        spans=[_span(SEGMENT_B, inicio, fin)],
        segments=_segments(),
        timestamp_precision="segment",
    )

    item = evidence[0]
    assert item.quote == "🎓"
    assert item.inicio_char == inicio
    assert item.fin_char == fin
    assert item.char_start == segment_offset + inicio
    assert item.char_end == segment_offset + fin


def test_stored_times_are_withheld_when_precision_says_none() -> None:
    # Defence in depth: even if a row carried times under `none`, exposing them would
    # fabricate an offset the ASR never measured.
    evidence = resolve_textual_evidence(
        spans=[_span(SEGMENT_A, 0, 5)],
        segments=_segments(a_times=(5.0, 9.0)),
        timestamp_precision="none",
    )

    assert evidence[0].t_start is None
    assert evidence[0].t_end is None
    assert evidence[0].reference == "segment 1"


def test_untimed_segment_is_never_interpolated_between_timed_neighbours() -> None:
    evidence = resolve_textual_evidence(
        spans=[
            _span(SEGMENT_A, 0, 5),
            _span(SEGMENT_B, 0, 5),
            _span(SEGMENT_C, 0, 5),
        ],
        segments=_segments(),
        timestamp_precision="segment",
    )

    by_ordinal = {item.ordinal: item for item in evidence}
    assert (by_ordinal[1].t_start, by_ordinal[1].t_end) == (0.0, 4.0)
    assert by_ordinal[1].reference == format_timestamp(0.0)
    assert (by_ordinal[2].t_start, by_ordinal[2].t_end) == (None, None)
    assert by_ordinal[2].reference == "segment 2"
    assert (by_ordinal[3].t_start, by_ordinal[3].t_end) == (10.0, 14.0)
    assert by_ordinal[3].reference == format_timestamp(10.0)


@pytest.mark.parametrize(
    ("t_start", "t_end"),
    [
        pytest.param(None, None, id="both-null"),
        pytest.param(5.0, None, id="only-start"),
        pytest.param(None, 5.0, id="only-end"),
        pytest.param(-1.0, 5.0, id="negative-start"),
        pytest.param(5.0, 5.0, id="zero-duration"),
        pytest.param(6.0, 5.0, id="inverted"),
        pytest.param(math.nan, 5.0, id="nan-start"),
        pytest.param(5.0, math.inf, id="infinite-end"),
    ],
)
def test_untruthful_offsets_are_withheld(
    t_start: float | None, t_end: float | None
) -> None:
    assert reliable_times(
        timestamp_precision="segment", t_start=t_start, t_end=t_end
    ) == (None, None)


def test_times_past_the_class_duration_are_withheld() -> None:
    assert reliable_times(
        timestamp_precision="segment", t_start=10.0, t_end=70.0, duration_seconds=60.0
    ) == (None, None)


@pytest.mark.parametrize("precision", ["none", "unknown", ""])
def test_non_temporal_precision_never_exposes_a_pair(precision: str) -> None:
    assert reliable_times(
        timestamp_precision=precision, t_start=0.0, t_end=5.0
    ) == (None, None)


def test_reference_never_builds_a_time_from_an_incomplete_pair() -> None:
    assert evidence_reference(segment_ordinal=7, t_start=65.0, t_end=70.0) == "01:05"
    assert evidence_reference(segment_ordinal=7, t_start=None, t_end=70.0) == "segment 7"
    assert evidence_reference(segment_ordinal=7, t_start=65.0, t_end=None) == "segment 7"
    assert evidence_reference(segment_ordinal=7, t_start=70.0, t_end=65.0) == "segment 7"
    assert evidence_reference(segment_ordinal=None, t_start=None, t_end=None) == ""


def test_evidence_label_adds_the_server_side_class_context() -> None:
    label = evidence_label(
        subject_name="Cálculo II",
        class_date=date(2026, 9, 21),
        segment_ordinal=4,
        t_start=None,
        t_end=None,
    )
    assert label == "[Cálculo II · 2026-09-21 · segment 4]"


@pytest.mark.parametrize(
    ("inicio", "fin"),
    [
        pytest.param(-1, 2, id="negative-start"),
        pytest.param(2, 2, id="empty"),
        pytest.param(3, 2, id="inverted"),
        pytest.param(0, len(TEXT_B) + 1, id="past-the-segment"),
    ],
)
def test_span_outside_the_persisted_segment_is_rejected(inicio: int, fin: int) -> None:
    with pytest.raises(EvidenceInvalid):
        resolve_textual_evidence(
            spans=[_span(SEGMENT_B, inicio, fin)],
            segments=_segments(),
            timestamp_precision="none",
        )


def test_span_for_an_unknown_segment_is_rejected_without_echoing_text() -> None:
    with pytest.raises(EvidenceInvalid) as exc:
        resolve_textual_evidence(
            spans=[_span(uuid.uuid4(), 0, 2)],
            segments=_segments(),
            timestamp_precision="none",
        )

    assert "examen" not in str(exc.value)


def test_duplicate_segment_rows_are_rejected() -> None:
    segments = [*_segments(), _segments()[0]]

    with pytest.raises(EvidenceInvalid):
        resolve_textual_evidence(
            spans=[_span(SEGMENT_A, 0, 2)],
            segments=segments,
            timestamp_precision="none",
        )
