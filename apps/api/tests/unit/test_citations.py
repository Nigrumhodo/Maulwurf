"""U-S2-JL-08: citation-marker buffering, labels and URLs (L2.5)."""

import uuid
from dataclasses import replace
from datetime import date

from app.services.citations import (
    CitationBuffer,
    CitationRef,
    citation_event,
    citation_label,
    citation_record,
    citation_url,
    format_timestamp,
)
from app.services.search import RetrievedChunk


def _chunk(
    *,
    t_start: float | None = 65.0,
    t_end: float | None = 70.0,
    segment_ordinal: int | None = 2,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        audio_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        subject_name="Cálculo",
        subject_color="#112233",
        class_date=date(2026, 9, 21),
        content="La integral definida acumula área.",
        snippet="La integral definida acumula área.",
        segment_id=uuid.uuid4(),
        t_start=t_start,
        t_end=t_end,
        score_rrf=0.5,
        transcript_id=uuid.uuid4(),
        transcript_version=2,
        segment_ordinal=segment_ordinal,
    )


def test_timestamp_is_deterministic_and_clamped() -> None:
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(65.9) == "01:05"
    assert format_timestamp(3725) == "1:02:05"
    assert format_timestamp(-3) == "00:00"


def test_label_uses_measured_time_and_falls_back_to_segment() -> None:
    timed = _chunk()
    assert citation_label(timed) == "[Cálculo · 2026-09-21 · 01:05]"
    untimed = _chunk(t_start=None, t_end=None, segment_ordinal=2)
    assert citation_label(untimed) == "[Cálculo · 2026-09-21 · segment 2]"
    no_segment = _chunk(t_start=None, t_end=None, segment_ordinal=None)
    assert citation_label(no_segment) == "[Cálculo · 2026-09-21]"


def test_url_targets_the_segment_or_the_class() -> None:
    chunk = _chunk()
    assert citation_url(chunk) == f"/biblioteca/{chunk.audio_id}?segment={chunk.segment_id}"
    chunk_without_segment = replace(chunk, segment_id=None)
    assert citation_url(chunk_without_segment) == f"/biblioteca/{chunk.audio_id}"


def test_citation_event_and_record_keep_only_server_identifiers() -> None:
    chunk = _chunk()
    ref = CitationRef(index=1, chunk=chunk, offset=12)
    event = citation_event(ref)
    record = citation_record(ref)

    assert event["index"] == 1
    assert event["audio_id"] == str(chunk.audio_id)
    assert event["transcript_version"] == 2
    assert event["segment_id"] == str(chunk.segment_id)
    assert event["t_start"] == 65.0
    assert event["label"] == "[Cálculo · 2026-09-21 · 01:05]"
    assert record["index"] == 1
    assert record["offset"] == 12
    assert record["quote"] is None


def test_markers_are_removed_and_indices_are_returned_in_order() -> None:
    buffer = CitationBuffer(source_count=2)
    text, indices = buffer.feed("Antes [S1] medio [S2] después")
    assert text == "Antes  medio  después"
    assert indices == (1, 2)
    assert buffer.flush() == ""


def test_marker_split_across_deltas_is_kept_pending() -> None:
    buffer = CitationBuffer(source_count=3)
    assert buffer.feed("Dato [S") == ("Dato ", ())
    text, indices = buffer.feed("2] y [S3")
    assert text == " y "
    assert indices == (2,)
    assert buffer.feed("] fin") == (" fin", (3,))


def test_invalid_or_out_of_range_markers_are_literal_text() -> None:
    buffer = CitationBuffer(source_count=1)
    text, indices = buffer.feed("[S0] [S2] [S99] [Sx] [S1]")
    assert text == "[S0] [S2] [S99] [Sx] "
    assert indices == (1,)


def test_flush_returns_an_incomplete_marker_as_literal_text() -> None:
    buffer = CitationBuffer(source_count=1)
    assert buffer.feed("texto [S") == ("texto ", ())
    assert buffer.flush() == "[S"
    assert buffer.flush() == ""


def test_nonmarker_bracket_is_emitted_without_delay() -> None:
    buffer = CitationBuffer(source_count=1)
    assert buffer.feed("a [nota] b [") == ("a [nota] b ", ())
    assert buffer.flush() == "["
