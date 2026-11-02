"""G6 extraction score over the frozen g6-v1 fixtures (L3.7).

The activities are synthetic. The recorded output is authored here and is not a provider
capture. Precision, recall and exact date are integer ratios. Each language is scored on
its own frozen ``test`` partition. P-S3-JL-07 is not executed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid5

from app.schemas.analysis import AnalysisResult, DateStatus, ProposedItem
from app.services.analysis import mention_from_payload, parse_extraction_payload
from app.services.analysis_validation import (
    AnalysisValidationError,
    ClassTranscript,
    validate_analysis,
)
from app.services.analysis_windows import WindowCitation, aggregate_window_citations, plan_windows
from app.services.chunking import SegmentInput
from app.services.date_resolution import resolve_due

DATASET_VERSION = "g6-v1"
CLASS_DATE = date(2026, 3, 6)
CLASS_TIMEZONE = "America/New_York"
NOW = datetime(2026, 3, 6, 15, tzinfo=UTC)
_NS = UUID("6f1b0a3e-7c2d-4e5f-8a9b-0c1d2e3f4a5b")
_TYPES = ("examen", "tarea", "entrega", "lectura", "recordatorio", "proyecto")
PRECISION_MIN = 95
RECALL_MIN = 85
EXACT_DATE_MIN = 95


@dataclass(frozen=True)
class Fraction:
    numerator: int
    denominator: int

    def meets(self, percent: int) -> bool:
        if self.denominator == 0:
            return False
        return self.numerator * 100 >= percent * self.denominator


@dataclass(frozen=True)
class LanguageScore:
    language: str
    partition: str
    gold: int
    published: int
    rejected: int
    precision: Fraction
    recall: Fraction
    exact_date: Fraction
    ambiguous_in_review: bool
    evidence_validated: bool

    def passes(self) -> bool:
        return (
            self.gold >= 50
            and self.precision.meets(PRECISION_MIN)
            and self.recall.meets(RECALL_MIN)
            and self.exact_date.meets(EXACT_DATE_MIN)
            and self.ambiguous_in_review
            and self.evidence_validated
        )


@dataclass(frozen=True)
class _Spec:
    index: int
    tipo: str
    quote: str
    mention: dict[str, Any]
    recording: str


def _quote(language: str, index: int) -> str:
    if language == "es":
        return f"La actividad {index} cubre el capitulo {index} de las notas del curso."
    return f"Activity {index} covers chapter {index} in the course notes."


def _mention(index: int) -> dict[str, Any]:
    if index in {4, 7}:
        return {"kind": "weekday", "weekday": 4, "qualifier": "next"}
    if index == 5:
        return {"kind": "local_time", "due_date": "2026-03-08", "local_time": "02:30"}
    if index == 6:
        return {"kind": "absent"}
    return {"kind": "day_offset", "day_offset": (index % 20) + 1}


def _recording(index: int, partition: str) -> str:
    if partition != "test":
        return "faithful"
    if index in {0, 1}:
        return "omit"
    if index == 2:
        return "wrong_date"
    return "faithful"


def _specs(language: str, partition: str) -> list[_Spec]:
    count = 50 if partition == "test" else 4
    return [
        _Spec(
            index=index,
            tipo=_TYPES[index % len(_TYPES)],
            quote=_quote(language, index),
            mention=_mention(index),
            recording=_recording(index, partition),
        )
        for index in range(count)
    ]


def _segment_id(language: str, index: int) -> UUID:
    return uuid5(_NS, f"{DATASET_VERSION}:{language}:{index}")


def _segments(specs: list[_Spec], language: str) -> tuple[str, tuple[SegmentInput, ...]]:
    text = ""
    rows: list[SegmentInput] = []
    for ordinal, spec in enumerate(specs):
        start = len(text)
        text += spec.quote
        rows.append(
            SegmentInput(
                id=_segment_id(language, spec.index),
                ordinal=ordinal,
                text=spec.quote,
                char_start=start,
                char_end=len(text),
                t_start=None,
                t_end=None,
            )
        )
    return text, tuple(rows)


def _item(language: str, spec: _Spec, *, quote: str, mention: dict[str, Any]) -> dict[str, Any]:
    return {
        "tipo": spec.tipo,
        "titulo": f"{spec.tipo} {spec.index}",
        "detalle": None,
        "due_date": None,
        "due_at": "2026-03-08T00:00:00-05:00",
        "timezone": "UTC",
        "all_day": False,
        "date_status": "resolved",
        "confidence_score": 0.5,
        "mencion": mention,
        "fuente": {
            "spans": [
                {
                    "segment_id": str(_segment_id(language, spec.index)),
                    "inicio_char": 0,
                    "fin_char": len(quote),
                    "cita_textual": quote,
                }
            ]
        },
    }


def recorded_payload(language: str, partition: str) -> str:
    """Authored stand-in for one model response. Not a provider capture."""
    specs = _specs(language, partition)
    items = []
    for spec in specs:
        if spec.recording == "omit":
            continue
        mention = dict(spec.mention)
        if spec.recording == "wrong_date":
            mention = {"kind": "day_offset", "day_offset": int(spec.mention["day_offset"]) + 7}
        items.append(_item(language, spec, quote=spec.quote, mention=mention))
    if partition == "test":
        decoy = specs[0]
        items.append(
            _item(language, decoy, quote="x" * len(decoy.quote), mention=dict(decoy.mention))
        )
    return json.dumps(
        {
            "items": items,
            "resumen_clase": "Synthetic class used only to score extraction. No real lecture.",
            "temas": ["synthetic"],
        }
    )


def score_language(language: str, partition: str = "test") -> LanguageScore:
    """Score one language after validation, date resolution and evidence dedupe."""
    specs = _specs(language, partition)
    transcript, segments = _segments(specs, language)
    parsed = parse_extraction_payload(
        recorded_payload(language, partition),
        class_date=CLASS_DATE,
        class_timezone=CLASS_TIMEZONE,
        now=NOW,
    )
    view = ClassTranscript(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        audio_id=UUID("22222222-2222-4222-8222-222222222222"),
        transcript_id=UUID("33333333-3333-4333-8333-333333333333"),
        transcript_version=1,
        segments={row.id: row.text for row in segments},
    )
    kept: list[ProposedItem] = []
    rejected = 0
    for item in parsed.result.items:
        single = AnalysisResult(
            items=[item],
            resumen_clase=parsed.result.resumen_clase,
            temas=list(parsed.result.temas),
        )
        try:
            validate_analysis(single, view)
        except AnalysisValidationError:
            rejected += 1
            continue
        kept.append(item)
    plan = plan_windows(
        transcript_text=transcript,
        segments=segments,
        target_tokens=100_000,
        overlap_tokens=0,
        count_tokens=lambda text: max(1, len(text.split())),
    )
    window = plan.windows[0]
    located: list[tuple[ProposedItem, tuple[WindowCitation, ...]]] = []
    for item in kept:
        span = item.fuente.spans[0]
        part = next(row for row in window.segments if row.segment_id == span.segment_id)
        located.append(
            (
                item,
                (
                    WindowCitation(
                        segment_id=span.segment_id,
                        char_start=part.segment_char_start + span.inicio_char,
                        char_end=part.segment_char_start + span.fin_char,
                    ),
                ),
            )
        )
    published = [proposal.value for proposal in aggregate_window_citations(((window, located),))]
    gold = [_gold(language, spec) for spec in specs]
    return _metrics(language, partition, gold, published, rejected=rejected)


def _gold(language: str, spec: _Spec) -> ProposedItem:
    resolved = resolve_due(
        mention_from_payload(spec.mention),
        class_date=CLASS_DATE,
        class_timezone=CLASS_TIMEZONE,
        now=NOW,
    )
    return ProposedItem.model_validate(
        {
            "tipo": spec.tipo,
            "titulo": f"{spec.tipo} {spec.index}",
            "detalle": None,
            "due_date": None if resolved.due_date is None else resolved.due_date.isoformat(),
            "due_at": None if resolved.due_at is None else resolved.due_at.isoformat(),
            "timezone": resolved.timezone,
            "all_day": resolved.all_day if resolved.date_status is DateStatus.RESOLVED else False,
            "date_status": resolved.date_status.value,
            "confidence_score": 0.5,
            "fuente": {
                "spans": [
                    {
                        "segment_id": str(_segment_id(language, spec.index)),
                        "inicio_char": 0,
                        "fin_char": len(spec.quote),
                        "cita_textual": spec.quote,
                    }
                ]
            },
        }
    )


def _metrics(
    language: str,
    partition: str,
    gold: list[ProposedItem],
    published: list[ProposedItem],
    *,
    rejected: int,
) -> LanguageScore:
    matched: set[int] = set()
    true_positive = 0
    date_hits = 0
    date_compared = 0
    for item in published:
        for index, expected in enumerate(gold):
            if index in matched or not _same_evidence(item, expected) or item.tipo != expected.tipo:
                continue
            matched.add(index)
            if expected.date_status is DateStatus.RESOLVED:
                date_compared += 1
                if _date_key(item) == _date_key(expected):
                    date_hits += 1
            if _pair(item) == _pair(expected):
                true_positive += 1
            break
    ambiguous_gold = [item for item in gold if item.date_status is DateStatus.AMBIGUOUS]
    ambiguous_ok = bool(ambiguous_gold) and all(
        any(
            _same_evidence(item, expected)
            and item.date_status is DateStatus.AMBIGUOUS
            and item.due_date is None
            and item.due_at is None
            for item in published
        )
        for expected in ambiguous_gold
    )
    return LanguageScore(
        language=language,
        partition=partition,
        gold=len(gold),
        published=len(published),
        rejected=rejected,
        precision=Fraction(true_positive, len(published)),
        recall=Fraction(true_positive, len(gold)),
        exact_date=Fraction(date_hits, date_compared),
        ambiguous_in_review=ambiguous_ok,
        evidence_validated=rejected >= 1,
    )


def _same_evidence(left: ProposedItem, right: ProposedItem) -> bool:
    return _evidence(left) == _evidence(right)


def _evidence(item: ProposedItem) -> frozenset[tuple[UUID, int, int, str]]:
    return frozenset(
        (span.segment_id, span.inicio_char, span.fin_char, span.cita_textual)
        for span in item.fuente.spans
    )


def _date_key(item: ProposedItem) -> tuple[str, str | None, str | None]:
    due_at = None if item.due_at is None else item.due_at.astimezone(UTC).isoformat()
    due_date = None if item.due_date is None else item.due_date.isoformat()
    return (item.date_status.value, due_date, due_at)


def _pair(
    item: ProposedItem,
) -> tuple[str, tuple[str, str | None, str | None], frozenset[tuple[UUID, int, int, str]]]:
    return (item.tipo, _date_key(item), _evidence(item))


def main() -> int:
    print(f"dataset {DATASET_VERSION} provider=NO VERIFICADO")
    for language in ("es", "en"):
        score = score_language(language, "test")
        print(
            f"{language} test gold={score.gold} published={score.published} "
            f"rejected={score.rejected} "
            f"precision={score.precision.numerator}/{score.precision.denominator} "
            f"recall={score.recall.numerator}/{score.recall.denominator} "
            f"exact_date={score.exact_date.numerator}/{score.exact_date.denominator} "
            f"pass={score.passes()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
