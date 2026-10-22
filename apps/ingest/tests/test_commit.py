"""I-S2-SG-06: atomic transcript commit. Empty text is not padded."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from test_supervisor import Attempt, _seed  # noqa: E402

from maulwurf_ingest import lease
from maulwurf_ingest.asr_job import STUB_TRANSCRIPT_NAME, _read_stub
from maulwurf_ingest.persistence import (
    CommitOutcome,
    CommitResult,
    TranscriptDraft,
    TranscriptWord,
    commit_transcript,
    draft_from_job,
    enable_text_outbox,
    fts_config_for,
    model_config_document,
    prepare_transcript,
    repetition_warnings,
)

pytestmark_integration = pytest.mark.integration


def _json(value: object) -> object:
    """asyncpg may return jsonb as text. Comparisons use the decoded value."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _draft(text: str = "Hoy repasamos", *, precision: str = "word") -> TranscriptDraft:
    words = (
        (TranscriptWord("Hoy", 0.0, 0.4), TranscriptWord("repasamos", 0.5, 1.1))
        if precision == "word" else ()
    )
    return TranscriptDraft(
        text=text, precision=precision, words=words,
        model="whisper-large-v3", client_version="2.27.0",
    )


def test_blank_text_is_not_padded() -> None:
    assert prepare_transcript(_draft("   ", precision="none")) is None
    assert prepare_transcript(TranscriptDraft("", "none", (), "whisper-large-v3")) is None
    prepared = prepare_transcript(_draft("  hola  ", precision="none"))
    assert prepared is not None
    assert prepared.text == "hola"
    assert prepared.precision == "none"
    assert prepared.segments[0].t_start is None
    assert prepared.warnings == ()


def test_repetition_warning_omits_the_token() -> None:
    warnings = repetition_warnings("La la la derivada")
    assert warnings == ({"code": "repetition", "count": 3},)
    assert "la" not in json.dumps(list(warnings))
    assert repetition_warnings("la la") == ()


def test_invalid_times_drop_to_precision_none() -> None:
    prepared = prepare_transcript(TranscriptDraft(
        text="Hoy hay clase", precision="word",
        words=(TranscriptWord("Hoy", 1.0, 0.2),),
        model="whisper-large-v3",
    ))
    assert prepared is not None
    assert prepared.precision == "none"
    assert prepared.text == "Hoy hay clase"
    assert prepared.segments[0].t_start is None and prepared.segments[0].t_end is None


def test_word_offsets_count_unicode_code_points() -> None:
    prepared = prepare_transcript(TranscriptDraft(
        text="", precision="word",
        words=(TranscriptWord("café", 0.0, 0.2), TranscriptWord("niño", 0.3, 0.6)),
        model="whisper-large-v3",
    ))
    assert prepared is not None
    assert prepared.text == "café niño"
    assert (prepared.segments[0].char_start, prepared.segments[0].char_end) == (0, 4)
    assert (prepared.segments[1].char_start, prepared.segments[1].char_end) == (5, 9)
    assert prepared.segments[0].t_start == 0.0


def test_fts_config_follows_the_selected_language() -> None:
    assert fts_config_for("es") == "spanish"
    assert fts_config_for("en") == "english"
    assert fts_config_for("fr") == "simple"


def test_model_config_has_no_path() -> None:
    document = model_config_document(fragment_seconds=30, overlap_s=0.25, duration_s=float("nan"))
    assert document == {"fragment_seconds": 30, "overlap_s": 0.25}
    assert "/" not in json.dumps(document)


def test_malformed_job_payload_is_rejected() -> None:
    empty = draft_from_job(None)
    assert empty is not None and empty.text == ""
    assert draft_from_job({"text": "hola", "words": [{"text": "hola"}]}) is None


def test_stub_reader_hides_a_bad_fixture(tmp_path: Path) -> None:
    (tmp_path / STUB_TRANSCRIPT_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stub_transcript"):
        _read_stub(tmp_path)


async def _ready(pool: asyncpg.Pool) -> tuple[Attempt, lease.Lease, uuid.UUID]:
    attempt = await _seed(pool)
    owned = await lease.acquire(
        pool, user_id=attempt.user_id, attempt_id=attempt.attempt_id, instance="ingest:a",
        ttl=timedelta(seconds=30),
    )
    assert owned is not None
    assert await lease.transition(
        pool, owned, from_status="awaiting_upload", to_status="receiving",
    )
    assert await lease.transition(
        pool, owned, from_status="receiving", to_status="transcribing",
    )
    audio_id = await pool.fetchval(
        "SELECT audio_id FROM ingestion_attempts WHERE id = $1", attempt.attempt_id,
    )
    assert isinstance(audio_id, uuid.UUID)
    return attempt, owned, audio_id


async def _commit(
    pool: asyncpg.Pool, owned: lease.Lease, draft: TranscriptDraft, *, duration_s: float = 12.5,
) -> CommitResult:
    return await commit_transcript(
        pool, owned, draft, fragments=2, fragment_seconds=30, overlap_s=0.25, duration_s=duration_s,
    )


@pytestmark_integration
async def test_commit_persists_one_transcript_and_a_disabled_outbox(pool: asyncpg.Pool) -> None:
    _attempt, owned, audio_id = await _ready(pool)
    published = await _commit(pool, owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    assert published.version == 1

    transcript = await pool.fetchrow("SELECT * FROM transcripts WHERE audio_id = $1", audio_id)
    assert transcript is not None
    assert transcript["text"] == "Hoy repasamos"
    assert transcript["char_count"] == len("Hoy repasamos")
    assert transcript["language_requested"] == "es"
    assert transcript["fts_config"] == "spanish"
    assert transcript["provider"] == "nvidia-riva"
    assert transcript["model"] == "whisper-large-v3"
    assert transcript["client_version"] == "2.27.0"
    assert transcript["timestamp_precision"] == "word"
    warnings = _json(transcript["warnings"])
    config = _json(transcript["model_config"])
    assert warnings == []
    assert isinstance(config, dict)
    assert "/" not in json.dumps(config)
    assert config["fragment_seconds"] == 30
    assert config["duration_s"] == 12.5

    segments = await pool.fetch(
        "SELECT ordinal, text, char_start, char_end, t_start, t_end"
        " FROM segments WHERE transcript_id = $1 ORDER BY ordinal",
        transcript["id"],
    )
    offsets = [
        (row["ordinal"], row["text"], row["char_start"], row["char_end"]) for row in segments
    ]
    assert offsets == [(0, "Hoy", 0, 3), (1, "repasamos", 4, 13)]
    assert segments[0]["t_start"] == 0.0 and segments[1]["t_end"] == 1.1

    active = await pool.fetchval(
        "SELECT active_transcript_version FROM audios WHERE id = $1", audio_id,
    )
    assert active == 1
    attempt_row = await pool.fetchrow(
        "SELECT status, fragments_done FROM ingestion_attempts WHERE id = $1", owned.attempt_id,
    )
    assert attempt_row is not None
    assert attempt_row["status"] == "transcript_committed_cleanup_pending"
    assert attempt_row["fragments_done"] == 2

    event = await pool.fetchrow(
        "SELECT type, enabled, blocked_reason, payload FROM outbox_events WHERE resource_id = $1",
        audio_id,
    )
    assert event is not None
    assert event["type"] == "index_requested"
    assert event["enabled"] is False
    assert event["blocked_reason"] == "cleanup_pending"
    payload = _json(event["payload"])
    assert isinstance(payload, dict)
    assert set(payload) == {"transcript_id", "version"}
    assert "Hoy" not in json.dumps(payload)


@pytestmark_integration
async def test_blank_and_refused_commits_write_nothing(pool: asyncpg.Pool) -> None:
    _attempt, owned, audio_id = await _ready(pool)
    blank = await _commit(pool, owned, _draft("   ", precision="none"))
    assert blank.outcome is CommitOutcome.NO_USABLE_TEXT
    assert await pool.fetchval(
        "SELECT count(*) FROM transcripts WHERE audio_id = $1", audio_id,
    ) == 0
    status = await pool.fetchval(
        "SELECT status FROM ingestion_attempts WHERE id = $1", owned.attempt_id,
    )
    assert status == "transcribing"

    stale = await _commit(pool, replace(owned, fencing_token=owned.fencing_token + 9), _draft())
    assert stale.outcome is CommitOutcome.NOT_OWNER

    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        owned.attempt_id,
    )
    expired = await _commit(pool, owned, _draft())
    assert expired.outcome is CommitOutcome.NOT_OWNER

    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() + interval '1 minute'"
        " WHERE id = $1",
        owned.attempt_id,
    )
    await pool.execute("UPDATE audios SET deleted_at = now() WHERE id = $1", audio_id)
    tombstone = await _commit(pool, owned, _draft())
    assert tombstone.outcome is CommitOutcome.NOT_OWNER
    assert await pool.fetchval(
        "SELECT count(*) FROM transcripts WHERE audio_id = $1", audio_id,
    ) == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM outbox_events WHERE resource_id = $1", audio_id,
    ) == 0


@pytestmark_integration
async def test_repetition_and_english_config_are_stored(pool: asyncpg.Pool) -> None:
    _attempt, owned, audio_id = await _ready(pool)
    await pool.execute("UPDATE audios SET language_code = 'en' WHERE id = $1", audio_id)
    published = await _commit(
        pool, owned, _draft("la la la derivada", precision="none"),
    )
    assert published.outcome is CommitOutcome.COMMITTED
    row = await pool.fetchrow(
        "SELECT text, fts_config, warnings, quality_flags, timestamp_precision"
        " FROM transcripts WHERE audio_id = $1",
        audio_id,
    )
    assert row is not None
    warnings = _json(row["warnings"])
    assert row["text"] == "la la la derivada"
    assert row["fts_config"] == "english"
    assert row["timestamp_precision"] == "none"
    assert warnings == [{"code": "repetition", "count": 3}]
    assert _json(row["quality_flags"]) == warnings
    assert "la" not in json.dumps(warnings)


@pytestmark_integration
async def test_enable_waits_for_verified_cleanup_and_leaves_other_events(
    pool: asyncpg.Pool,
) -> None:
    _attempt, owned, audio_id = await _ready(pool)
    await pool.execute(
        "UPDATE audios SET required_stages = $2::text[] WHERE id = $1",
        audio_id, ["index", "analyze"],
    )
    await pool.execute(
        """INSERT INTO outbox_events (user_id, type, resource_type, resource_id, payload, enabled)
           VALUES ($1, 'notify', 'audio', $2, '{}'::jsonb, true)""",
        owned.user_id, audio_id,
    )
    published = await _commit(pool, owned, _draft())
    assert published.outcome is CommitOutcome.COMMITTED
    version = published.version
    assert isinstance(version, int)

    gated = await pool.fetch(
        """SELECT type, enabled, blocked_reason FROM outbox_events
            WHERE resource_id = $1 AND type <> 'notify' ORDER BY type""",
        audio_id,
    )
    assert [(row["type"], row["enabled"], row["blocked_reason"]) for row in gated] == [
        ("analyze_requested", False, "cleanup_pending"),
        ("index_requested", False, "cleanup_pending"),
    ]
    assert await enable_text_outbox(pool, owned, audio_id=audio_id, version=version) is False

    assert await lease.record_cleanup(pool, owned, verified=True, evidence={"verified": True})
    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() - interval '1 second'"
        " WHERE id = $1",
        owned.attempt_id,
    )
    assert await enable_text_outbox(pool, owned, audio_id=audio_id, version=version) is False
    still = await pool.fetchval(
        "SELECT enabled FROM outbox_events WHERE resource_id = $1 AND type = 'index_requested'",
        audio_id,
    )
    assert still is False

    await pool.execute(
        "UPDATE ingestion_attempts SET lease_expires_at = now() + interval '1 minute'"
        " WHERE id = $1",
        owned.attempt_id,
    )
    assert await enable_text_outbox(pool, owned, audio_id=audio_id, version=version) is True
    enabled = await pool.fetch(
        """SELECT type, enabled, blocked_reason FROM outbox_events
            WHERE resource_id = $1 ORDER BY type""",
        audio_id,
    )
    assert [(row["type"], row["enabled"], row["blocked_reason"]) for row in enabled] == [
        ("analyze_requested", True, None),
        ("index_requested", True, None),
        ("notify", True, None),
    ]
    status = await pool.fetchval(
        "SELECT status FROM ingestion_attempts WHERE id = $1", owned.attempt_id,
    )
    assert status == "succeeded"
