"""U-S2-JL-01: chunking with Unicode offsets, sentence/segment cuts and `chunk_segments`.

`app/services/chunking.py` is the single authority that turns a stored transcript plus its
ordered `segments` rows into the chunk drafts of ESPECIFICACION.md M6 (target ~800 tokens,
~100 overlap, preferred cuts at sentence and ASR segment boundaries, Unicode offsets
`[start, end)` in code points). Pure functions and dataclasses, no DB, no HTTP. No
`integration` marker: runs in `python-unit`.

The §M6↔table reconciliation (L2.9) is out of scope; these tests pin the chunker's own
contract, not persistence.
"""

import time
from uuid import uuid4

import pytest

from app.services.chunking import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    ChunkDraft,
    SegmentInput,
    chunk_segments,
    count_tokens_default,
    split_sentences,
)


def _segment(
    ordinal: int,
    text: str,
    char_start: int,
    *,
    t_start: float | None = None,
    t_end: float | None = None,
) -> SegmentInput:
    return SegmentInput(
        id=uuid4(),
        ordinal=ordinal,
        text=text,
        char_start=char_start,
        char_end=char_start + len(text),
        t_start=t_start,
        t_end=t_end,
    )


def _build(texts: list[str]) -> tuple[str, list[SegmentInput]]:
    """Build a transcript text and its contiguous segments from the given pieces."""
    text = ""
    segments: list[SegmentInput] = []
    for ordinal, piece in enumerate(texts):
        segments.append(_segment(ordinal, piece, len(text)))
        text += piece
    return text, segments


def _run(
    text: str,
    segments: list[SegmentInput],
    *,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[ChunkDraft]:
    return chunk_segments(
        audio_id=uuid4(),
        transcript_version=1,
        index_version="idx-v1",
        transcript_text=text,
        segments=segments,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
    )


def _sentences(count: int, words: int) -> str:
    return " ".join(" ".join(f"w{i}_{k}" for k in range(words)) + "." for i in range(count))


def test_count_tokens_treats_cjk_each_as_one_and_words_as_runs() -> None:
    assert count_tokens_default("微積分。") == 4  # three ideographs + full stop
    assert count_tokens_default("café día") == 2
    assert count_tokens_default("") == 0


def test_split_sentences_covers_text_contiguously() -> None:
    text = "Uno. Dos\nTres. mas"
    spans = split_sentences(text)

    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    assert "".join(text[start:end] for start, end in spans) == text
    for (_, end), (start, _) in zip(spans, spans[1:], strict=False):
        assert end == start


def test_target_and_overlap_are_respected() -> None:
    text, segments = _build([_sentences(60, 28)])  # ~29 tokens per sentence
    chunks = _run(text, segments)

    assert len(chunks) >= 3
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    for chunk in chunks:
        assert chunk.token_count <= TARGET_TOKENS
        assert chunk.content == text[chunk.char_start : chunk.char_end]
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.char_start < previous.char_end  # positive overlap
        overlap = count_tokens_default(text[current.char_start : previous.char_end])
        assert 0 < overlap <= OVERLAP_TOKENS


def test_emoji_offsets_count_one_code_point() -> None:
    text = "día 🎓 微積分 café."
    # 🎓 is astral: two UTF-16 units but a single code point, so offsets are code points.
    assert len(text.encode("utf-16-le")) // 2 == len(text) + 1

    chunks = _run(text, [_segment(0, text, 0)])

    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].char_end == len(text)


def test_accents_offsets_do_not_shift() -> None:
    text = "café\ndía."
    chunks = _run(text, [_segment(0, text, 0)], target_tokens=2, overlap_tokens=0)

    assert [chunk.content for chunk in chunks] == ["café\n", "día."]
    assert chunks[0].char_end == len("café\n")


def test_cjk_offsets_are_code_points() -> None:
    text = "微積分。中文。"
    chunks = _run(text, [_segment(0, text, 0)], target_tokens=4, overlap_tokens=0)

    assert [chunk.content for chunk in chunks] == ["微積分。", "中文。"]
    assert chunks[1].char_start == len("微積分。")


def test_cut_lands_exactly_on_sentence_boundary() -> None:
    text = "a a. b b."
    chunks = _run(text, [_segment(0, text, 0)], target_tokens=3, overlap_tokens=0)

    assert chunks[0].content == "a a."
    assert chunks[0].char_end == 4
    assert chunks[1].content == " b b."
    assert chunks[1].char_start == 4


def test_short_final_chunk_is_emitted() -> None:
    text = "a a. b b. c."
    chunks = _run(text, [_segment(0, text, 0)], target_tokens=3, overlap_tokens=0)

    assert [chunk.content for chunk in chunks] == ["a a.", " b b.", " c."]
    assert chunks[-1].token_count < 3
    assert chunks[-1].char_end == len(text)


def test_no_chunk_is_overlap_only() -> None:
    text, segments = _build([_sentences(10, 8)])
    chunks = _run(text, segments, target_tokens=18, overlap_tokens=9)

    assert len(chunks) >= 2
    for previous, current in zip(chunks, chunks[1:], strict=False):
        # Every chunk adds new characters; a trailing window of pure overlap is never emitted.
        assert current.char_end > previous.char_end


def test_zero_overlap_produces_contiguous_chunks() -> None:
    text, segments = _build(["a a. b b. c c. d d."])
    chunks = _run(text, segments, target_tokens=6, overlap_tokens=0)

    assert len(chunks) == 2
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.char_start == previous.char_end


def test_never_emits_an_overlap_only_chunk() -> None:
    # Regression: the trailing short sentence was re-emitted alone, fully contained in the
    # previous chunk (no new content).
    text, segments = _build(["a. a. a a."])
    chunks = _run(text, segments, target_tokens=4, overlap_tokens=0)

    assert len(chunks) >= 2
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.char_start <= previous.char_end
        assert current.char_end > previous.char_end
    assert chunks[-1].char_end == len(text)


def test_chunk_segments_links_and_ranges() -> None:
    text, segments = _build(["Uno. Dos. ", "Tres. Cuatro."])
    chunks = _run(text, segments, target_tokens=4, overlap_tokens=0)

    for chunk in chunks:
        assert chunk.segments
        for link in chunk.segments:
            segment = segments[link.segment_ordinal]
            assert 0 <= link.inicio_char < link.fin_char <= len(segment.text)
            assert segment.char_start + link.inicio_char == max(
                chunk.char_start, segment.char_start
            )
            assert segment.char_start + link.fin_char == min(chunk.char_end, segment.char_end)


def test_prefers_asr_segment_boundary() -> None:
    text, segments = _build([f"Seg{i}." for i in range(6)])
    starts = {segment.char_start for segment in segments}
    chunks = _run(text, segments, target_tokens=2, overlap_tokens=0)

    for chunk in chunks:
        assert chunk.char_start in starts
        assert chunk.content == text[chunk.char_start : chunk.char_end]


def test_oversized_sentence_is_split_with_offsets() -> None:
    text = " ".join(["w"] * 2500) + "."
    chunks = _run(text, [_segment(0, text, 0)])

    assert len(chunks) >= 3
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    for chunk in chunks:
        assert chunk.token_count <= TARGET_TOKENS
        assert chunk.content
        assert chunk.content == text[chunk.char_start : chunk.char_end]


def test_oversized_sentence_splits_on_unicode_whitespace() -> None:
    # One oversized sentence whose words are separated by tabs: the sub-split must land on
    # the tab, never inside a word (a plain " " search would miss it).
    text = "\t".join(f"w{i}" for i in range(2500)) + "."
    chunks = _run(text, [_segment(0, text, 0)], overlap_tokens=0)

    assert len(chunks) >= 3
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    rebuilt = ""
    for chunk in chunks:
        assert chunk.content == text[chunk.char_start : chunk.char_end]
        rebuilt += chunk.content
    assert rebuilt == text
    for chunk in chunks[:-1]:
        assert chunk.content.endswith("\t")


def test_newlines_split_chunks_at_line_boundaries() -> None:
    text = "line one\nline two\nline three\n"
    chunks = _run(text, [_segment(0, text, 0)], target_tokens=2, overlap_tokens=0)

    assert "".join(chunk.content for chunk in chunks) == text
    for chunk in chunks[:-1]:
        assert chunk.content.endswith("\n")


def test_oversized_split_stays_within_a_time_budget() -> None:
    # Guards against the regression where sub-splitting scanned every whitespace match in
    # the span on each iteration (~4 s for 60k words). The backwards scan runs in ~1 s;
    # the bound is generous to stay stable on slower CI hosts (best-of-three runs).
    text = " ".join(["w"] * 60_000) + "."
    segment = _segment(0, text, 0)

    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        chunks = _run(text, [segment])
        best = min(best, time.perf_counter() - started)

    assert chunks[-1].char_end == len(text)
    assert best < 3.0


def test_cjk_without_punctuation_splits_in_bounded_time() -> None:
    # Whitespace-free CJK (common in ASR output) used to hit a quadratic search path
    # (~5 s for 100k chars). The bounded search keeps it well under a second; the bound is
    # generous to stay stable on slower CI hosts (best-of-three runs).
    text = "中" * 100_000
    segment = _segment(0, text, 0)

    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        chunks = _run(text, [segment])
        best = min(best, time.perf_counter() - started)

    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    assert all(chunk.token_count <= TARGET_TOKENS for chunk in chunks)
    assert all(chunk.content == text[chunk.char_start : chunk.char_end] for chunk in chunks)
    assert best < 2.0


def test_same_input_same_output() -> None:
    text, segments = _build(["Uno. Dos. Tres. Cuatro."])
    audio_id = uuid4()

    def run() -> list[ChunkDraft]:
        return chunk_segments(
            audio_id=audio_id,
            transcript_version=1,
            index_version="idx-v1",
            transcript_text=text,
            segments=segments,
            target_tokens=4,
            overlap_tokens=2,
        )

    assert run() == run()


def test_times_derived_or_null() -> None:
    first, second = "Uno. ", "Dos."
    text = first + second
    segment_a = SegmentInput(uuid4(), 0, first, 0, len(first), 0.0, 10.0)
    segment_b = SegmentInput(uuid4(), 1, second, len(first), len(text), 10.0, 20.0)

    chunks = _run(text, [segment_a, segment_b], target_tokens=4, overlap_tokens=0)
    assert len(chunks) == 1
    assert chunks[0].t_start == 0.0
    assert chunks[0].t_end == 20.0

    segment_b = SegmentInput(uuid4(), 1, second, len(first), len(text), None, None)
    chunks = _run(text, [segment_a, segment_b], target_tokens=4, overlap_tokens=0)
    assert chunks[0].t_start is None
    assert chunks[0].t_end is None


def test_zero_duration_segments_are_accepted() -> None:
    first, second = "Uno. ", "Dos."
    text = first + second
    segments = [
        SegmentInput(uuid4(), 0, first, 0, len(first), 5.0, 5.0),  # zero duration
        SegmentInput(uuid4(), 1, second, len(first), len(text), 5.0, 9.0),
    ]

    chunks = _run(text, segments, target_tokens=4, overlap_tokens=0)

    assert len(chunks) == 1
    assert chunks[0].t_start == 5.0
    assert chunks[0].t_end == 9.0


def test_empty_transcript_returns_no_chunks() -> None:
    assert _run("", []) == []


def test_whitespace_only_transcript_returns_no_chunks() -> None:
    for text in ("   ", "\n\t \n", " \u00a0 "):
        assert _run(text, [_segment(0, text, 0)]) == []


def test_invalid_segments_raise() -> None:
    with pytest.raises(ValueError):
        _run("Uno. Dos.", [])  # non-empty text without segments

    text = "Uno. Dos."
    out_of_order = [
        SegmentInput(uuid4(), 1, "Uno. ", 0, 5, None, None),
        SegmentInput(uuid4(), 0, "Dos.", 5, 9, None, None),
    ]
    with pytest.raises(ValueError):
        _run(text, out_of_order)

    non_contiguous = [
        SegmentInput(uuid4(), 0, "Uno.", 0, 4, None, None),
        SegmentInput(uuid4(), 1, "Dos.", 5, 9, None, None),
    ]
    with pytest.raises(ValueError):
        _run(text, non_contiguous)

    mismatched_text = [SegmentInput(uuid4(), 0, "XXX", 0, 3, None, None)]
    with pytest.raises(ValueError):
        _run(text, mismatched_text)

    out_of_bounds = [SegmentInput(uuid4(), 0, "Uno. Dos. X", 0, 11, None, None)]
    with pytest.raises(ValueError):
        _run(text, out_of_bounds)

    inverted_times = [SegmentInput(uuid4(), 0, "Uno. Dos.", 0, 9, 5.0, 4.0)]
    with pytest.raises(ValueError):
        _run(text, inverted_times)

    negative_times = [SegmentInput(uuid4(), 0, "Uno. Dos.", 0, 9, -1.0, 5.0)]
    with pytest.raises(ValueError):
        _run(text, negative_times)


def test_segments_must_cover_all_non_blank_text() -> None:
    text = "Uno. Dos."

    missing_head = [SegmentInput(uuid4(), 0, "no. Dos.", 1, 9, None, None)]
    with pytest.raises(ValueError, match="before the first segment"):
        _run(text, missing_head)

    missing_tail = [SegmentInput(uuid4(), 0, "Uno.", 0, 4, None, None)]
    with pytest.raises(ValueError, match="after the last segment"):
        _run(text, missing_tail)


def test_blank_text_outside_segments_is_tolerated() -> None:
    # Trailing newline left outside the segment (common in assembled transcripts).
    punct = "Hola mundo."
    text = punct + "\n"
    chunks = _run(text, [SegmentInput(uuid4(), 0, punct, 0, len(punct), None, None)])
    assert chunks
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(punct)

    # Leading whitespace left outside the segment.
    text = "  Hola."
    chunks = _run(text, [SegmentInput(uuid4(), 0, "Hola.", 2, len(text), None, None)])
    assert chunks[0].char_start == 2
    assert chunks[0].content == "Hola."


def test_empty_asr_segments_are_skipped() -> None:
    text = "HelloWorld"
    segments = [
        SegmentInput(uuid4(), 0, "Hello", 0, 5, 0.0, 1.0),
        SegmentInput(uuid4(), 1, "", 5, 5, 1.0, 1.0),  # empty text, zero duration
        SegmentInput(uuid4(), 2, "World", 5, 10, 1.0, 2.0),
    ]

    chunks = _run(text, segments)

    assert "".join(chunk.content for chunk in chunks) == text
    assert all(link.segment_ordinal != 1 for chunk in chunks for link in chunk.segments)


def test_offsets_match_schema_contract() -> None:
    text, segments = _build(["Uno. Dos. ", "Tres. Cuatro."])
    chunks = _run(text, segments, target_tokens=4, overlap_tokens=2)

    for chunk in chunks:
        assert 0 <= chunk.char_start < chunk.char_end <= len(text)
        assert chunk.content == text[chunk.char_start : chunk.char_end]
        for link in chunk.segments:
            segment = segments[link.segment_ordinal]
            assert link.inicio_char >= 0
            assert link.inicio_char < link.fin_char <= len(segment.text)
