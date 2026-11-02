"""Atomic transcript commit and the cleanup gate that releases it (S2.5, S2.6).

One transaction writes the transcript, its segments, and the disabled index/analyze
outbox rows, then moves the attempt to ``transcript_committed_cleanup_pending``.
A second transaction, after ``cleanup_status=verified``, enables those rows and
moves the attempt to ``succeeded``. A lost fencing token rolls the write back.

Warnings carry a code and a count. They do not carry the spoken words: the
ticket forbids PII in the quality signal, and the words already live in the
transcript.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import asyncpg

from maulwurf_ingest import rls
from maulwurf_ingest.audio.reconcile import parse_confidence
from maulwurf_ingest.lease import Lease

PROVIDER = "nvidia-riva"
PRECISIONS = frozenset({"word", "segment", "none"})
_TIMED = frozenset({"word", "segment"})
_GATED_STAGES = frozenset({"index", "analyze"})
_STAGE_EVENT = {"index": "index_requested", "analyze": "analyze_requested"}
_REPETITION_RUN = 3
_FTS = {"es": "spanish", "en": "english"}
# Provisional quality signal: confidence is uncalibrated, so the warning only says
# «review this text» and never blocks or auto-deletes anything (M3).
LOW_CONFIDENCE_THRESHOLD = 0.6
LOW_CONFIDENCE_SHARE = 0.5


class CommitOutcome(StrEnum):
    COMMITTED = "committed"
    NO_USABLE_TEXT = "no_usable_text"
    NOT_OWNER = "not_owner"
    INVALID = "invalid"


class _Rollback(Exception):
    """The publish conditions failed. The open transaction must not commit."""


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start_s: float
    end_s: float
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptDraft:
    text: str
    precision: str
    words: tuple[TranscriptWord, ...]
    model: str
    client_version: str | None = None


@dataclass(frozen=True)
class SegmentDraft:
    ordinal: int
    text: str
    char_start: int
    char_end: int
    t_start: float | None
    t_end: float | None


@dataclass(frozen=True)
class PreparedTranscript:
    text: str
    precision: str
    segments: tuple[SegmentDraft, ...]
    warnings: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class CommitResult:
    outcome: CommitOutcome
    transcript_id: uuid.UUID | None = None
    version: int | None = None
    audio_id: uuid.UUID | None = None


def fts_config_for(language_code: str) -> str:
    """Postgres regconfig for the language the student selected."""
    return _FTS.get(language_code, "simple")


def repetition_warnings(text: str) -> tuple[dict[str, object], ...]:
    """Consecutive identical tokens. The token itself is not stored."""
    tokens = [token.casefold() for token in text.split()]
    warnings: list[dict[str, object]] = []
    index = 0
    while index < len(tokens):
        end = index + 1
        while end < len(tokens) and tokens[end] == tokens[index]:
            end += 1
        count = end - index
        if count >= _REPETITION_RUN:
            warnings.append({"code": "repetition", "count": count})
        index = end
    return tuple(warnings)


def quality_warnings(
    text: str, words: Sequence[TranscriptWord] = ()
) -> tuple[dict[str, object], ...]:
    """Non-sensitive ASR quality signals: repetition, empty entries and low confidence.

    Empty word entries are counted before the segment builder drops them. Confidence is
    only considered when the provider actually reported it; at least half of the scored
    words below the threshold raises one warning. No spoken word or provider payload is
    stored in the signal (M3/M9).
    """
    warnings: list[dict[str, object]] = list(repetition_warnings(text))
    if not words:
        return tuple(warnings)
    empty = sum(1 for word in words if not word.text.strip())
    if empty:
        warnings.append({"code": "empty_segments", "count": empty})
    scored = [
        confidence
        for word in words
        if word.text.strip()
        if (confidence := _valid_confidence(word.confidence)) is not None
    ]
    low = sum(1 for confidence in scored if confidence < LOW_CONFIDENCE_THRESHOLD)
    if scored and low >= len(scored) * LOW_CONFIDENCE_SHARE:
        warnings.append({"code": "low_confidence", "count": low, "total": len(scored)})
    return tuple(warnings)


def _valid_confidence(value: float | None) -> float | None:
    # 0.0 is unknown (protobuf default and parse_confidence), never a scored low word.
    if value is None or not math.isfinite(value) or not 0.0 < value <= 1.0:
        return None
    return value


def prepare_transcript(draft: TranscriptDraft) -> PreparedTranscript | None:
    """Build segments from the draft. ``None`` means there is nothing to store.

    Word and segment times are kept only when every span is valid. One invalid
    span drops the precision to ``none`` and stores null times, instead of
    inventing the missing ones. Empty input is not replaced with filler.
    """
    timed = _timed_segments(draft) if draft.precision in _TIMED else None
    if timed is not None:
        segments, text = timed
        precision = draft.precision
    else:
        text = draft.text.strip()
        if not text:
            return None
        segments = (SegmentDraft(0, text, 0, len(text), None, None),)
        precision = "none"
    return PreparedTranscript(text, precision, segments, quality_warnings(text, draft.words))


def model_config_document(
    *, fragment_seconds: int, overlap_s: float, duration_s: float | None,
) -> dict[str, object]:
    """Non-sensitive ASR settings. Paths and audio bytes never belong here."""
    document: dict[str, object] = {
        "fragment_seconds": fragment_seconds,
        "overlap_s": overlap_s,
    }
    if duration_s is not None and math.isfinite(duration_s) and duration_s >= 0:
        document["duration_s"] = duration_s
    return document


def draft_from_job(body: object) -> TranscriptDraft | None:
    """Parse the ASR job's ``transcript`` object. ``None`` is a malformed payload.

    A missing object is an empty draft: the caller rejects it as no usable text.
    A present object with a word that is not a span is malformed.
    """
    if body is None:
        return TranscriptDraft(text="", precision="none", words=(), model="")
    if not isinstance(body, dict):
        return None
    text = body.get("text", "")
    precision = body.get("precision", "none")
    model = body.get("model") or ""
    client = body.get("client_version")
    words_raw = body.get("words", [])
    if not isinstance(text, str) or not isinstance(precision, str) or not isinstance(model, str):
        return None
    if client is not None and not isinstance(client, str):
        return None
    words = _words_from_job(words_raw)
    if words is None:
        return None
    client_version = client if isinstance(client, str) and client.strip() else None
    return TranscriptDraft(
        text=text,
        precision=precision,
        words=words,
        model=model,
        client_version=client_version,
    )


async def commit_transcript(
    pool: asyncpg.Pool,
    lease: Lease,
    draft: TranscriptDraft,
    *,
    fragments: int,
    fragment_seconds: int,
    overlap_s: float,
    duration_s: float | None,
) -> CommitResult:
    """Publish the transcript or leave the database unchanged."""
    if _no_usable_text(draft):
        return CommitResult(CommitOutcome.NO_USABLE_TEXT)
    if draft.precision not in PRECISIONS or not draft.model.strip():
        return CommitResult(CommitOutcome.INVALID)
    prepared = prepare_transcript(draft)
    if prepared is None:
        return CommitResult(CommitOutcome.NO_USABLE_TEXT)

    document = model_config_document(
        fragment_seconds=fragment_seconds, overlap_s=overlap_s, duration_s=duration_s,
    )
    flags = json.dumps(list(prepared.warnings))
    try:
        async with pool.acquire() as conn, conn.transaction():
            await rls.bind_transaction(conn, lease.user_id)
            row = await conn.fetchrow(
                """
                SELECT ia.audio_id, a.language_code, a.required_stages
                  FROM ingestion_attempts ia
                  JOIN audios a ON a.user_id = ia.user_id AND a.id = ia.audio_id
                 WHERE ia.id = $1 AND ia.user_id = $2 AND ia.fencing_token = $3
                   AND ia.status = 'transcribing' AND ia.lease_expires_at > now()
                 FOR UPDATE OF ia, a
                """,
                lease.attempt_id, lease.user_id, lease.fencing_token,
            )
            if row is None:
                raise _Rollback
            audio_id = row["audio_id"]
            if not isinstance(audio_id, uuid.UUID):
                raise _Rollback
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM transcripts WHERE audio_id = $1",
                audio_id,
            )
            if not isinstance(version, int):
                raise _Rollback
            language = str(row["language_code"])
            transcript_id = await conn.fetchval(
                """
                INSERT INTO transcripts (
                    user_id, audio_id, version, language_requested, provider, model,
                    model_config, client_version, timestamp_precision, text, char_count,
                    quality_flags, warnings, fts_config
                ) VALUES (
                    $1, $2, $3, $4, 'nvidia-riva', $5,
                    $6::jsonb, $7, $8, $9, char_length($9),
                    $10::jsonb, $11::jsonb, $12
                )
                RETURNING id
                """,
                lease.user_id, audio_id, version, language, draft.model.strip(),
                json.dumps(document), draft.client_version, prepared.precision, prepared.text,
                flags, flags, fts_config_for(language),
            )
            if not isinstance(transcript_id, uuid.UUID):
                raise _Rollback
            await conn.executemany(
                """
                INSERT INTO segments (
                    user_id, transcript_id, ordinal, text, char_start, char_end, t_start, t_end
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                [
                    (
                        lease.user_id, transcript_id, segment.ordinal, segment.text,
                        segment.char_start, segment.char_end, segment.t_start, segment.t_end,
                    )
                    for segment in prepared.segments
                ],
            )
            payload = json.dumps({"transcript_id": str(transcript_id), "version": version})
            for stage in _required_stages(row["required_stages"]):
                await conn.execute(
                    """
                    INSERT INTO outbox_events (
                        user_id, type, resource_type, resource_id, resource_version,
                        payload, enabled, blocked_reason, dedupe_key
                    ) VALUES (
                        $1, $2, 'audio', $3, $4, $5::jsonb, false, 'cleanup_pending', $6
                    )
                    """,
                    lease.user_id, _STAGE_EVENT[stage], audio_id, version, payload, stage,
                )
            audio_updated = await conn.execute(
                """
                UPDATE audios
                   SET active_transcript_version = $3, updated_at = now()
                 WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
                """,
                audio_id, lease.user_id, version,
            )
            attempt_updated = await conn.execute(
                """
                UPDATE ingestion_attempts
                   SET status = 'transcript_committed_cleanup_pending',
                       fragments_done = $4, fragments_total = $4, updated_at = now()
                 WHERE id = $1 AND user_id = $2 AND fencing_token = $3
                   AND status = 'transcribing' AND lease_expires_at > now()
                """,
                lease.attempt_id, lease.user_id, lease.fencing_token, fragments,
            )
            if audio_updated != "UPDATE 1" or attempt_updated != "UPDATE 1":
                raise _Rollback
            return CommitResult(
                CommitOutcome.COMMITTED,
                transcript_id=transcript_id,
                version=version,
                audio_id=audio_id,
            )
    except _Rollback:
        return CommitResult(CommitOutcome.NOT_OWNER)


async def enable_text_outbox(
    pool: asyncpg.Pool, lease: Lease, *, audio_id: uuid.UUID, version: int,
) -> bool:
    """Enable index/analyze for this version and mark the attempt ``succeeded``.

    Both writes are one transaction. The attempt update requires a live lease,
    the fencing token, and ``cleanup_status=verified``. Any other outbox type
    is left as it was. A miss rolls the enablement back.
    """
    try:
        async with pool.acquire() as conn, conn.transaction():
            await rls.bind_transaction(conn, lease.user_id)
            await conn.execute(
                """
                UPDATE outbox_events
                   SET enabled = true, blocked_reason = NULL
                 WHERE user_id = $1 AND resource_id = $2 AND resource_version = $3
                   AND type IN ('index_requested', 'analyze_requested')
                   AND status = 'pending' AND enabled = false
                   AND blocked_reason IS NOT DISTINCT FROM 'cleanup_pending'
                """,
                lease.user_id, audio_id, version,
            )
            updated = await conn.execute(
                """
                UPDATE ingestion_attempts
                   SET status = 'succeeded', updated_at = now()
                 WHERE id = $1 AND user_id = $2 AND fencing_token = $3
                   AND status = 'transcript_committed_cleanup_pending'
                   AND cleanup_status = 'verified' AND lease_expires_at > now()
                """,
                lease.attempt_id, lease.user_id, lease.fencing_token,
            )
            if updated != "UPDATE 1":
                raise _Rollback
    except _Rollback:
        return False
    return True


def _no_usable_text(draft: TranscriptDraft) -> bool:
    """Blank text is not a transcript, even when the pipeline also returned words."""
    return not draft.text.strip()


def _valid_span(word: TranscriptWord) -> bool:
    return (
        math.isfinite(word.start_s)
        and math.isfinite(word.end_s)
        and word.start_s >= 0
        and word.start_s < word.end_s
    )


def _timed_segments(draft: TranscriptDraft) -> tuple[tuple[SegmentDraft, ...], str] | None:
    words = [word for word in draft.words if word.text.strip()]
    if not words or any(not _valid_span(word) for word in words):
        return None
    pieces = [word.text.strip() for word in words]
    text = " ".join(pieces)
    segments: list[SegmentDraft] = []
    cursor = 0
    for ordinal, (piece, word) in enumerate(zip(pieces, words, strict=True)):
        end = cursor + len(piece)
        if ordinal + 1 < len(pieces):
            # The separator belongs to the preceding word so the ranges partition the
            # transcript: the chunker (L2.1) rejects any non-contiguous segment.
            end += 1
        segments.append(
            SegmentDraft(ordinal, text[cursor:end], cursor, end, word.start_s, word.end_s)
        )
        cursor = end
    return tuple(segments), text


def _required_stages(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    seen: dict[str, None] = {}
    for item in raw:
        if isinstance(item, str) and item in _GATED_STAGES:
            seen.setdefault(item, None)
    return tuple(seen)


def _words_from_job(raw: object) -> tuple[TranscriptWord, ...] | None:
    if not isinstance(raw, list):
        return None
    words: list[TranscriptWord] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        text = item.get("text")
        start = item.get("start_s")
        end = item.get("end_s")
        if not isinstance(text, str) or isinstance(start, bool) or isinstance(end, bool):
            return None
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            return None
        words.append(
            TranscriptWord(
                text,
                float(start),
                float(end),
                parse_confidence(item.get("confidence")),
            )
        )
    return tuple(words)
