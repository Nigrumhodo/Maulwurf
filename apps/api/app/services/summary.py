"""Versioned class summary (L3.8).

The text stored on a transcript is the v1 summary prompt's output, not the first
extraction window. Validation requires 3 to 5 sentences and distinct topics, and rejects
a secret so it cannot be written next to the proposals. A failure raises before the
analyze transaction commits.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from prompts.registry import SUMMARY_PROMPT_VERSION, load_summary_prompts

# Break only where terminal punctuation is followed by whitespace or the end of the text.
# "3.5" and "..." therefore stay inside a sentence. "Dr." and "e.g." still break when a
# space follows: the v1 check stays deterministic and does not keep an abbreviation list.
_SENTENCE_BREAK = re.compile(r"[.!?]+(?=\s|$)")
_SECRETS = (
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"nvapi-", re.IGNORECASE),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|client_secret|nvidia_api_key)\s*[=:]\s*\S+"
    ),
)


class SummaryRejected(Exception):
    """The summary is not the v1 shape. The analyze run must write nothing."""

    code = "analysis_unexpected"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ClassSummary:
    """One validated summary and the prompt version that defines its shape."""

    text: str
    topics: tuple[str, ...]
    prompt_version: str


class SummaryRow(Protocol):
    """The transcript columns L3.8 writes."""

    summary: str | None
    topics: list[str] | None
    analysis_version: int | None


class AnalysisPointer(Protocol):
    """The class pointer kept in step with the transcript's analysis version."""

    active_analysis_version: int | None


def sentence_count(text: str) -> int:
    """How many sentences `text` contains.

    A terminator ends a sentence. A trailing fragment still counts when the text already
    has one, so a summary that only omits the final period is not discarded. A fragment
    with no terminator at all counts as zero.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    parts = [part for part in _SENTENCE_BREAK.split(stripped) if part.strip()]
    if stripped[-1] in ".!?":
        return len(parts)
    if _SENTENCE_BREAK.search(stripped) is None:
        return 0
    return len(parts)


def parse_summary_payload(raw: str) -> ClassSummary:
    """Accept only the v1 object. Extra fields, a bad length, or a secret raise."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SummaryRejected("the summary payload is not JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"resumen_clase", "temas"}:
        raise SummaryRejected("the summary payload does not match the schema")
    text = payload["resumen_clase"]
    topics = payload["temas"]
    if not isinstance(text, str) or not isinstance(topics, list):
        raise SummaryRejected("the summary payload does not match the schema")
    if not all(isinstance(topic, str) for topic in topics):
        raise SummaryRejected("the summary topics are not strings")
    return validate_summary(text, topics)


def validate_summary(text: str, topics: Sequence[str]) -> ClassSummary:
    """Check length, distinct topics, and the closed secret patterns."""
    cleaned = text.strip()
    count = sentence_count(cleaned)
    if count < 3 or count > 5:
        raise SummaryRejected("the summary must be 3 to 5 sentences")
    stored = tuple(topic.strip() for topic in topics)
    if any(not topic for topic in stored):
        raise SummaryRejected("a summary topic is empty")
    folded = [topic.casefold() for topic in stored]
    if len(set(folded)) != len(folded):
        raise SummaryRejected("summary topics are duplicated")
    prompts = load_summary_prompts()
    if prompts.version != SUMMARY_PROMPT_VERSION:
        raise SummaryRejected("the summary prompt version does not match")
    blob = cleaned + "\n" + "\n".join(stored)
    if any(pattern.search(blob) for pattern in _SECRETS):
        raise SummaryRejected("the summary contains sensitive data")
    return ClassSummary(text=cleaned, topics=stored, prompt_version=prompts.version)


def bumped_analysis_version(current: int | None) -> int:
    """First publication is 1. A later config on the same transcript row increments."""
    if current is None:
        return 1
    if isinstance(current, bool) or not isinstance(current, int) or current < 1:
        raise SummaryRejected("the analysis version is not a positive integer")
    return current + 1


def assign_class_summary(
    transcript: SummaryRow,
    audio: AnalysisPointer,
    *,
    text: str,
    topics: Sequence[str],
) -> int:
    """Write the validated summary and copy the new analysis version onto the class.

    The caller owns the transaction. A rejected summary raises and must abort it, so the
    proposals from the same run are not committed either.
    """
    checked = validate_summary(text, topics)
    version = bumped_analysis_version(transcript.analysis_version)
    transcript.summary = checked.text
    transcript.topics = list(checked.topics)
    transcript.analysis_version = version
    audio.active_analysis_version = version
    return version
