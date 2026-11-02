"""R-S3-JL-06: G6 thresholds on the authored g6-v1 replay. No model call."""

from tools.eval.g6 import DATASET_VERSION, score_language


def test_each_language_meets_the_g6_thresholds_on_the_frozen_partition() -> None:
    assert DATASET_VERSION == "g6-v1"
    for language in ("es", "en"):
        score = score_language(language, "test")
        assert score.gold >= 50
        assert score.rejected >= 1
        assert score.passes(), (
            f"{language} precision={score.precision.numerator}/{score.precision.denominator} "
            f"recall={score.recall.numerator}/{score.recall.denominator} "
            f"exact_date={score.exact_date.numerator}/{score.exact_date.denominator} "
            f"ambiguous={score.ambiguous_in_review} evidence={score.evidence_validated}"
        )
