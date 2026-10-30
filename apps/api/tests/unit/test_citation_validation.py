"""U-S2-JL-05: citations only reference DB-validated source identities (L2.6)."""

import asyncio
import math
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from app.services import rag as rag_module
from app.services.citations import (
    CitationBuffer,
    CitationRef,
    CitationValidator,
    citation_event,
    citation_label,
    citation_record,
    citation_source_matches,
    resolve_citation,
    validate_citation_record,
    validate_citation_source,
)
from app.services.llm import ChatMessage
from app.services.rag import (
    PreparedStream,
    RAGService,
    SourceItem,
    _record_offset,
)
from app.services.search import RetrievedChunk, SearchService


def _chunk(
    *,
    t_start: float | None = 65.0,
    t_end: float | None = 70.0,
    segment_ordinal: int | None = 2,
    **overrides: Any,
) -> RetrievedChunk:
    chunk = RetrievedChunk(
        chunk_id=uuid.uuid4(),
        audio_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        subject_name="Materia",
        subject_color="#123456",
        class_date=date(2026, 9, 21),
        content="La integral definida acumula área.",
        snippet="La integral definida acumula área.",
        segment_id=uuid.uuid4(),
        t_start=t_start,
        t_end=t_end,
        score_rrf=1.0,
        transcript_id=uuid.uuid4(),
        transcript_version=1,
        segment_ordinal=segment_ordinal,
    )
    return replace(chunk, **overrides)


async def _validator_returning(
    returned: RetrievedChunk | None, *, calls: list[RetrievedChunk] | None = None
) -> CitationValidator:
    async def validate(user_id: uuid.UUID, source: RetrievedChunk) -> RetrievedChunk | None:
        del user_id
        if calls is not None:
            calls.append(source)
        return returned

    return validate


def test_valid_source_matches_fresh_db_identity() -> None:
    chunk = _chunk()
    assert citation_source_matches(chunk, chunk)


@pytest.mark.parametrize(
    "field",
    ["chunk_id", "audio_id", "transcript_id", "transcript_version", "segment_id"],
)
def test_identity_fields_are_part_of_the_match(field: str) -> None:
    source = _chunk()
    assert citation_source_matches(_mismatched(source, field), source) is False


def _mismatched(source: RetrievedChunk, field: str) -> RetrievedChunk:
    if field == "chunk_id":
        return replace(source, chunk_id=uuid.uuid4())
    if field == "audio_id":
        return replace(source, audio_id=uuid.uuid4())
    if field == "transcript_id":
        return replace(source, transcript_id=uuid.uuid4())
    if field == "transcript_version":
        assert source.transcript_version is not None
        return replace(source, transcript_version=source.transcript_version + 1)
    if field == "segment_id":
        return replace(source, segment_id=uuid.uuid4())
    raise AssertionError(f"campo desconocido: {field}")


async def test_validate_source_rejects_a_changed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _chunk()
    fresh = replace(source, segment_id=uuid.uuid4())

    async def fake_rehydrate(
        self: SearchService, *, user_id: uuid.UUID, source: RetrievedChunk
    ) -> RetrievedChunk | None:
        del self, user_id, source
        return fresh

    monkeypatch.setattr(SearchService, "rehydrate_source", fake_rehydrate)

    assert (
        await validate_citation_source(cast(Any, object()), user_id=uuid.uuid4(), source=source)
        is None
    )


async def test_validate_source_rejects_missing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_rehydrate(
        self: SearchService, *, user_id: uuid.UUID, source: RetrievedChunk
    ) -> RetrievedChunk | None:
        nonlocal called
        del self, user_id, source
        called = True
        return None

    monkeypatch.setattr(SearchService, "rehydrate_source", fake_rehydrate)
    missing_transcript = replace(_chunk(), transcript_id=None)
    missing_version = replace(_chunk(), transcript_version=None)

    for source in (missing_transcript, missing_version):
        assert (
            await validate_citation_source(
                cast(Any, object()), user_id=uuid.uuid4(), source=source
            )
            is None
        )
    assert called is False


async def test_validate_source_returns_the_fresh_row(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _chunk()
    fresh = replace(source, snippet="lectura fresca")
    seen: dict[str, object] = {}

    async def fake_rehydrate(
        self: SearchService, *, user_id: uuid.UUID, source: RetrievedChunk
    ) -> RetrievedChunk | None:
        del self
        seen["user_id"] = user_id
        seen["source"] = source
        return fresh

    monkeypatch.setattr(SearchService, "rehydrate_source", fake_rehydrate)
    user_id = uuid.uuid4()

    result = await validate_citation_source(
        cast(Any, object()), user_id=user_id, source=source
    )

    assert result is fresh
    assert seen == {"user_id": user_id, "source": source}


async def test_resolve_citation_uses_only_the_validated_row() -> None:
    source = _chunk(t_start=65.0, t_end=70.0)
    fresh = replace(source, t_start=125.0, t_end=130.0, segment_ordinal=9)
    validate = await _validator_returning(fresh)
    user_id = uuid.uuid4()
    seen: list[RetrievedChunk] = []

    async def tracking(user: uuid.UUID, item: RetrievedChunk) -> RetrievedChunk | None:
        assert user == user_id
        seen.append(item)
        return await validate(user, item)

    ref = await resolve_citation(
        index=1,
        source=source,
        offset=12,
        user_id=user_id,
        validate=tracking,
    )

    assert seen == [source]
    assert ref is not None
    assert ref.chunk is fresh
    assert ref.index == 1
    assert ref.offset == 12
    event = citation_event(ref)
    assert event["t_start"] == 125.0
    assert event["label"] == "[Materia · 2026-09-21 · 02:05]"


async def test_rejected_citation_emits_no_event_and_no_record() -> None:
    validate = await _validator_returning(None)
    ref = await resolve_citation(
        index=1,
        source=_chunk(),
        offset=3,
        user_id=uuid.uuid4(),
        validate=validate,
    )

    assert ref is None


@pytest.mark.parametrize(
    ("t_start", "t_end"),
    [
        (None, 70.0),
        (65.0, None),
        (70.0, 65.0),
        (math.nan, 70.0),
        (65.0, math.nan),
        (math.inf, 70.0),
        (-1.0, 70.0),
    ],
)
def test_partial_or_invalid_offsets_fall_back_to_segment(
    t_start: float | None, t_end: float | None
) -> None:
    label = citation_label(_chunk(t_start=t_start, t_end=t_end, segment_ordinal=7))
    assert label == "[Materia · 2026-09-21 · segment 7]"


def test_label_does_not_invent_a_segment_without_ordinal() -> None:
    label = citation_label(_chunk(t_start=None, t_end=None, segment_ordinal=None))
    assert label == "[Materia · 2026-09-21]"


def test_out_of_range_and_malformed_markers_are_literal() -> None:
    buffer = CitationBuffer(source_count=2)
    text, indices = buffer.feed("[S0] [S3] [S01] [s1] [Sx] [S2]")

    assert text == "[S0] [S3] [S01] [s1] [Sx] "
    assert indices == (2,)


async def test_marker_split_across_deltas_is_validated_once_complete() -> None:
    buffer = CitationBuffer(source_count=1)
    calls: list[RetrievedChunk] = []
    validate = await _validator_returning(_chunk(), calls=calls)

    assert buffer.feed("Dato [S") == ("Dato ", ())
    text, indices = buffer.feed("1] fin")
    assert text == " fin"
    assert indices == (1,)

    refs = [
        await resolve_citation(
            index=index,
            source=_chunk(),
            offset=len(text),
            user_id=uuid.uuid4(),
            validate=validate,
        )
        for index in indices
    ]

    assert len(calls) == 1
    assert len(refs) == 1 and refs[0] is not None


async def test_db_error_fails_closed_without_citation() -> None:
    async def failing(user_id: uuid.UUID, source: RetrievedChunk) -> RetrievedChunk | None:
        del user_id, source
        raise OperationalError("SELECT 1", {}, Exception("database unavailable"))

    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=None,
        citation_validator=failing,
    )

    assert await service._validate_citation(uuid.uuid4(), _chunk()) is None


async def test_unexpected_error_fails_closed_without_citation() -> None:
    async def failing(user_id: uuid.UUID, source: RetrievedChunk) -> RetrievedChunk | None:
        del user_id, source
        raise RuntimeError("unexpected validator failure")

    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=None,
        citation_validator=failing,
    )

    assert await service._validate_citation(uuid.uuid4(), _chunk()) is None


async def test_validation_timeout_fails_closed_without_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hanging(user_id: uuid.UUID, source: RetrievedChunk) -> RetrievedChunk | None:
        del user_id, source
        await asyncio.sleep(60)
        return None

    monkeypatch.setattr(rag_module, "CITATION_VALIDATION_TIMEOUT_SECONDS", 0.01)
    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=None,
        citation_validator=hanging,
    )

    assert await service._validate_citation(uuid.uuid4(), _chunk()) is None


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"offset": None},
        {"offset": True},
        {"offset": False},
        {"offset": -1},
        {"offset": 1.5},
        {"offset": "3"},
        {"offset": []},
    ],
)
def test_malformed_offsets_are_rejected(record: dict[str, object]) -> None:
    assert _record_offset(record) is None


@pytest.mark.parametrize("offset", [0, 1, 42])
def test_non_negative_integer_offsets_are_accepted(offset: int) -> None:
    assert _record_offset({"offset": offset}) == offset


async def test_replay_rejects_malformed_offsets_before_validation() -> None:
    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=None,
    )
    record = citation_record(CitationRef(index=1, chunk=_chunk(), offset=0))

    for malformed in (True, -1, 1.5, None):
        record["offset"] = malformed
        assert await service._replay_citation_ref(uuid.uuid4(), record) is None


async def test_repeated_markers_validate_once_per_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _chunk()
    calls: list[RetrievedChunk] = []

    async def validate(
        user_id: uuid.UUID, item: RetrievedChunk
    ) -> RetrievedChunk | None:
        del user_id
        calls.append(item)
        return item

    class RepeatedLLM:
        model = "test-model"
        context_window = 4096

        async def stream(
            self, messages: Sequence[ChatMessage], *, max_tokens: int
        ) -> AsyncIterator[str]:
            del messages, max_tokens
            yield "Dato [S1]"
            yield " y de nuevo [S1]."

    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=RepeatedLLM(),
        citation_validator=validate,
        checkpoint_seconds=60.0,
        cancel_poll_seconds=60.0,
        heartbeat_seconds=60.0,
    )

    async def streaming(user_id: uuid.UUID, message_id: uuid.UUID) -> str:
        del user_id, message_id
        return "streaming"

    async def checkpoint(
        prepared: PreparedStream,
        content: str,
        citations: Sequence[dict[str, object]],
    ) -> bool:
        del prepared, content, citations
        return True

    async def finalize(
        prepared: PreparedStream,
        status: str,
        content: str,
        citations: Sequence[dict[str, object]],
    ) -> str:
        del prepared, content, citations
        return status

    monkeypatch.setattr(service, "_message_status", streaming)
    monkeypatch.setattr(service, "_checkpoint", checkpoint)
    monkeypatch.setattr(service, "_finalize", finalize)

    prepared = PreparedStream(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        messages=(ChatMessage(role="user", content="¿Qué es la integral?"),),
        sources=(SourceItem(index=1, chunk=source, block=""),),
    )
    frames = [frame async for frame in service._live_events(prepared)]

    assert len(calls) == 1
    assert sum(frame.startswith("event: citation") for frame in frames) == 2


async def test_rehydrate_source_does_not_reuse_the_old_snippet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _chunk(snippet="snippet viejo")
    seen: dict[str, object] = {}

    async def fake_rehydrate(
        self: SearchService,
        *,
        user_id: uuid.UUID,
        chunk_id: uuid.UUID,
        audio_id: uuid.UUID,
        query: str = "",
        score_rrf: float = 0.0,
    ) -> RetrievedChunk | None:
        del self
        seen.update(
            {
                "user_id": user_id,
                "chunk_id": chunk_id,
                "audio_id": audio_id,
                "query": query,
                "score_rrf": score_rrf,
            }
        )
        return source

    monkeypatch.setattr(SearchService, "rehydrate_chunk_ids", fake_rehydrate)
    service = SearchService(cast(Any, object()), provider=None)

    assert await service.rehydrate_source(user_id=uuid.uuid4(), source=source) is source
    assert seen["query"] == ""
    assert seen["chunk_id"] == source.chunk_id
    assert seen["audio_id"] == source.audio_id


async def test_model_text_cannot_inject_ids_or_urls() -> None:
    source = _chunk()
    fake_text = (
        "Mira https://evil.example/biblioteca/"
        "00000000-0000-0000-0000-000000000000 y source=otra [S1]"
    )
    buffer = CitationBuffer(source_count=1)
    text, indices = buffer.feed(fake_text)

    assert "00000000-0000-0000-0000-000000000000" in text
    assert indices == (1,)

    validate = await _validator_returning(source)
    ref = await resolve_citation(
        index=1,
        source=source,
        offset=len(text),
        user_id=uuid.uuid4(),
        validate=validate,
    )

    assert ref is not None
    event = citation_event(ref)
    assert event["audio_id"] == str(source.audio_id)
    assert event["chunk_id"] == str(source.chunk_id)
    assert event["url"] == f"/biblioteca/{source.audio_id}?segment={source.segment_id}"
    assert "evil.example" not in str(event["url"])
    assert "00000000-0000-0000-0000-000000000000" not in str(event)


async def test_validate_record_rehydrates_the_persisted_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = _chunk()
    record = citation_record(CitationRef(index=1, chunk=chunk, offset=4))
    seen: dict[str, object] = {}

    async def fake_rehydrate(
        self: SearchService,
        *,
        user_id: uuid.UUID,
        chunk_id: uuid.UUID,
        audio_id: uuid.UUID,
        query: str = "",
        score_rrf: float = 0.0,
    ) -> RetrievedChunk | None:
        del self
        seen.update(
            {
                "user_id": user_id,
                "chunk_id": chunk_id,
                "audio_id": audio_id,
                "query": query,
                "score_rrf": score_rrf,
            }
        )
        return chunk

    monkeypatch.setattr(SearchService, "rehydrate_chunk_ids", fake_rehydrate)
    user_id = uuid.uuid4()

    result = await validate_citation_record(
        cast(Any, object()), user_id=user_id, record=record
    )

    assert result is chunk
    assert seen["user_id"] == user_id
    assert seen["chunk_id"] == chunk.chunk_id
    assert seen["audio_id"] == chunk.audio_id


@pytest.mark.parametrize(
    "change",
    [
        {"audio_id": "not-a-uuid"},
        {"chunk_id": None},
        {"transcript_id": None},
        {"transcript_version": 0},
        {"transcript_version": True},
        {"segment_id": "not-a-uuid"},
    ],
)
async def test_malformed_citation_record_fails_closed(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    chunk = _chunk()
    record = citation_record(CitationRef(index=1, chunk=chunk, offset=4)) | change
    called = False

    async def fake_rehydrate(
        self: SearchService,
        *,
        user_id: uuid.UUID,
        chunk_id: uuid.UUID,
        audio_id: uuid.UUID,
        query: str = "",
        score_rrf: float = 0.0,
    ) -> RetrievedChunk | None:
        nonlocal called
        del self, user_id, chunk_id, audio_id, query, score_rrf
        called = True
        return chunk

    monkeypatch.setattr(SearchService, "rehydrate_chunk_ids", fake_rehydrate)

    assert (
        await validate_citation_record(cast(Any, object()), user_id=uuid.uuid4(), record=record)
        is None
    )
    assert called is False


async def test_validate_record_rejects_a_changed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = _chunk()
    record = citation_record(CitationRef(index=1, chunk=chunk, offset=4))
    fresh = replace(chunk, transcript_version=9)

    async def fake_rehydrate(
        self: SearchService,
        *,
        user_id: uuid.UUID,
        chunk_id: uuid.UUID,
        audio_id: uuid.UUID,
        query: str = "",
        score_rrf: float = 0.0,
    ) -> RetrievedChunk | None:
        del self, user_id, chunk_id, audio_id, query, score_rrf
        return fresh

    monkeypatch.setattr(SearchService, "rehydrate_chunk_ids", fake_rehydrate)

    assert (
        await validate_citation_record(cast(Any, object()), user_id=uuid.uuid4(), record=record)
        is None
    )
