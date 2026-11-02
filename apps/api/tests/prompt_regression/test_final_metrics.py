"""R-S4-JL-01 (L4.1): published G4/G6 ratios match the harness and approve nothing."""

from pathlib import Path

from tools.eval.g6 import score_language

_DOC = Path(__file__).resolve().parents[4] / "docs" / "eval" / "final-metrics.md"


def test_the_published_report_matches_the_replay_and_does_not_approve_the_gates() -> None:
    text = _DOC.read_text(encoding="utf-8")

    assert "NOT EVALUATED" in text
    assert "NO VERIFICADO" in text
    assert "no numerator" in text
    assert "G4 is approved" not in text
    assert "G6 is approved" not in text
    for label in ("omitted", "day_offset", "invented quote", "P-S3-JL-07"):
        assert label in text
    for language in ("es", "en"):
        score = score_language(language, "test")
        assert (score.precision.numerator, score.precision.denominator) == (47, 48)
        assert (score.recall.numerator, score.recall.denominator) == (47, 50)
        assert (score.exact_date.numerator, score.exact_date.denominator) == (44, 45)
        assert f"{score.precision.numerator}/{score.precision.denominator}" in text
        assert f"{score.recall.numerator}/{score.recall.denominator}" in text
        assert f"{score.exact_date.numerator}/{score.exact_date.denominator}" in text
