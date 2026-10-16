"""U-S2-AN-09: TXT/SRT export rules of A2.9, without Postgres.

`app/services/exports.py` is the single authority that decides when an SRT is offered
(ESPECIFICACION.md M3: never invent or distribute times proportionally; `409 srt_unavailable`
otherwise). Pure functions and `SimpleNamespace` doubles, no DB, no HTTP. The HTTP-level
behavior (200/404/409) is covered by `tests/integration/test_audio_read.py`. No `integration`
marker: runs in `python-unit`.
"""

from types import SimpleNamespace

from app.services import exports


def _segments(*pairs: tuple[float | None, float | None]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(t_start=start, t_end=end, text=f"segmento {index}")
        for index, (start, end) in enumerate(pairs, start=1)
    ]


def test_srt_exportable_with_word_precision_in_range() -> None:
    assert exports.srt_exportable(
        timestamp_precision="word",
        segments=_segments((0.0, 10.0), (10.0, 20.0)),
        duration_seconds=20.0,
    )


def test_srt_exportable_with_segment_precision_inside_duration() -> None:
    assert exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((0.0, 4.5), (4.5, 9.0)),
        duration_seconds=9.0,
    )


def test_srt_not_exportable_without_real_precision() -> None:
    assert not exports.srt_exportable(
        timestamp_precision="none",
        segments=_segments((0.0, 1.0)),
        duration_seconds=1.0,
    )


def test_srt_not_exportable_with_missing_times() -> None:
    assert not exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((None, None)),
        duration_seconds=1.0,
    )


def test_srt_not_exportable_when_start_is_not_before_end() -> None:
    assert not exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((5.0, 5.0)),
        duration_seconds=10.0,
    )


def test_srt_not_exportable_when_out_of_order_or_overlapping() -> None:
    out_of_order = exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((10.0, 20.0), (0.0, 10.0)),
        duration_seconds=20.0,
    )
    overlapping = exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((0.0, 10.0), (5.0, 15.0)),
        duration_seconds=20.0,
    )
    assert not out_of_order
    assert not overlapping


def test_srt_not_exportable_beyond_duration_or_unknown_duration() -> None:
    beyond = exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((0.0, 5.0)),
        duration_seconds=3.0,
    )
    unknown = exports.srt_exportable(
        timestamp_precision="segment",
        segments=_segments((0.0, 5.0)),
        duration_seconds=None,
    )
    assert not beyond
    assert not unknown


def test_srt_not_exportable_without_segments() -> None:
    assert not exports.srt_exportable(
        timestamp_precision="segment",
        segments=[],
        duration_seconds=10.0,
    )


def test_render_srt_numbers_blocks_and_formats_timestamps() -> None:
    rendered = exports.render_srt(_segments((0.0, 1.5), (61.25, 62.0)))
    assert rendered == (
        "1\n00:00:00,000 --> 00:00:01,500\nsegmento 1\n\n"
        "2\n00:01:01,250 --> 00:01:02,000\nsegmento 2\n"
    )


def test_render_txt_returns_the_text_verbatim() -> None:
    text = "acentos á é í ñ y saltos\nsegunda línea"
    assert exports.render_txt(text=text) == text
