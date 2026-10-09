"""U-S2-SG-04: fronteras, solape y offsets globales."""

from __future__ import annotations

from maulwurf_ingest.audio.reconcile import (
    FragmentTranscript,
    LocalWord,
    reconcile,
)


def _word(text: str, start_s: float, end_s: float) -> LocalWord:
    return LocalWord(text, start_s, end_s)


def test_silence_keeps_both_words_in_order() -> None:
    fragments = (
        FragmentTranscript(0.0, 2.0, words=(_word("hoy", 0.2, 0.6),)),
        FragmentTranscript(2.0, 4.0, words=(_word("clase", 0.3, 0.8),)),
    )
    result = reconcile(fragments)
    assert result.precision == "word"
    assert [word.text for word in result.words] == ["hoy", "clase"]
    assert result.words[0].start_s == 0.2
    assert result.words[1].start_s == 2.3
    assert result.text == "hoy clase"


def test_mid_word_boundary_keeps_the_first_copy() -> None:
    fragments = (
        FragmentTranscript(0.0, 2.0, words=(_word("derivadas", 1.2, 1.8),)),
        FragmentTranscript(
            1.6,
            3.5,
            words=(_word("Derivadas", 0.0, 0.4), _word("parcial", 0.6, 1.2)),
        ),
    )
    result = reconcile(fragments)
    assert [word.text for word in result.words] == ["derivadas", "parcial"]
    assert result.words[1].start_s == 2.2


def test_full_overlap_adds_nothing() -> None:
    words = (_word("hola", 0.1, 0.4), _word("clase", 0.5, 0.9))
    result = reconcile(
        (
            FragmentTranscript(0.0, 2.0, words=words),
            FragmentTranscript(0.0, 2.0, words=words),
        )
    )
    assert [word.text for word in result.words] == ["hola", "clase"]
    assert result.words[0].start_s == 0.1
    assert result.words[1].end_s == 0.9


def test_a_later_repeat_outside_the_overlap_is_kept() -> None:
    result = reconcile(
        (
            FragmentTranscript(0.0, 2.0, words=(_word("el", 0.1, 0.3),)),
            FragmentTranscript(1.75, 4.0, words=(_word("el", 1.0, 1.3),)),
        )
    )
    assert [word.text for word in result.words] == ["el", "el"]
    assert result.words[1].start_s == 2.75


def test_global_time_does_not_add_the_overlap() -> None:
    result = reconcile(
        (
            FragmentTranscript(0.0, 10.25, words=(_word("antes", 0.2, 0.5),)),
            FragmentTranscript(10.0, 12.0, words=(_word("despues", 1.0, 1.4),)),
        )
    )
    assert result.words[1].start_s == 11.0
    assert result.words[1].start_s != 11.25


def test_text_without_words_strips_the_echo_and_invents_no_times() -> None:
    result = reconcile(
        (
            FragmentTranscript(0.0, 2.0, text="hoy repasamos derivadas"),
            FragmentTranscript(1.75, 4.0, text="derivadas parciales"),
        )
    )
    assert result.precision == "none"
    assert result.words == ()
    assert result.text == "hoy repasamos derivadas parciales"


def test_text_outside_the_overlap_is_not_stripped() -> None:
    result = reconcile(
        (
            FragmentTranscript(0.0, 2.0, text="hoy repasamos derivadas"),
            FragmentTranscript(2.0, 4.0, text="derivadas parciales"),
        )
    )
    assert result.text == "hoy repasamos derivadas derivadas parciales"
    assert result.precision == "none"
