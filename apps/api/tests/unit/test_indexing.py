"""U-S2-JL-02: versioned index generation with atomic activation (L2.2, M6/D5).

No database and no network: `StubSession` records every statement and returns prepared data
(same pattern as `tests/unit/test_create_audio.py`). That is how the ticket promise is tested
— **a single activation transaction and never a mixture of active versions** — without
PostgreSQL. The database guarantee (`index_generations_one_active`) is evidenced at the `I-`
layer (`I-S2-AN-17`, A2.18).
"""

import uuid
from collections.abc import Sequence
from typing import Any, cast

import pytest
from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Chunk, Embedding, IndexGeneration, Segment, Transcript
from app.services import indexing
from app.services.embeddings import EmbeddingDimensionMismatch

VERSION = "v1"


# --- Test infrastructure --------------------------------------------------------


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _Rows:
    """Minimal `execute()` result: only what the application actually reads."""

    def __init__(self, *, rows: Sequence[Any] = (), scalar: Any = None, rowcount: int = 0) -> None:
        self._rows = list(rows)
        self._scalar = scalar
        self.rowcount = rowcount

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class StubSession:
    """Fake `AsyncSession`: routes by statement shape and fails on anything else."""

    def __init__(
        self,
        *,
        generation: IndexGeneration | None = None,
        transcripts: Sequence[Transcript] = (),
        segments: Sequence[Segment] = (),
        chunked_audio_ids: Sequence[uuid.UUID] = (),
        eligible_audio_ids: Sequence[uuid.UUID] = (),
        chunks: int = 0,
        embeddings: int = 0,
        chunks_without_embeddings: int = 0,
        retire_rowcount: int = 1,
        activate_rowcount: int = 1,
    ) -> None:
        self.generation = generation
        self.transcripts = list(transcripts)
        self.segments = list(segments)
        self.chunked_audio_ids = list(chunked_audio_ids)
        self.eligible_audio_ids = list(eligible_audio_ids)
        self.chunks = chunks
        self.embeddings = embeddings
        self.chunks_without_embeddings = chunks_without_embeddings
        self.retire_rowcount = retire_rowcount
        self.activate_rowcount = activate_rowcount
        self.events: list[str] = []
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.events.append(f"add:{type(obj).__name__}")

    async def flush(self) -> None:
        self.events.append("flush")

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("rollback")

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Rows:
        sql = str(statement)
        params: dict[str, Any] = dict(statement.compile().params)
        self.statements.append((sql, params))
        event, rows = self._route(sql, params)
        self.events.append(event)
        return rows

    def persisted_chunks(self) -> list[Chunk]:
        return [obj for obj in self.added if isinstance(obj, Chunk)]

    def persisted_embeddings(self) -> list[Embedding]:
        return [obj for obj in self.added if isinstance(obj, Embedding)]

    def _route(self, sql: str, params: dict[str, Any]) -> tuple[str, _Rows]:
        if sql.startswith("UPDATE index_generations"):
            if params.get("status") == indexing.INDEX_RETIRED:
                return "sql:retire_active", _Rows(rowcount=self.retire_rowcount)
            return "sql:activate", _Rows(rowcount=self.activate_rowcount)
        if sql.startswith("UPDATE audios"):
            if "EXISTS" in sql:
                return "sql:audio_versions", _Rows(rowcount=1)
            return "sql:clear_stale_audio_versions", _Rows(rowcount=0)
        if sql.startswith("DELETE FROM chunks"):
            # Regression guard: activation must never delete retired chunks (test 14).
            return "sql:purge_retired_chunks", _Rows(rowcount=1)
        if "FROM index_generations" in sql:
            return "sql:generation", _Rows(scalar=self.generation)
        if "FROM embeddings" in sql and "chunks" not in sql:
            return "sql:count_embeddings", _Rows(scalar=self.embeddings)
        if "FROM chunks" in sql:
            if "embeddings" in sql:
                if "count(*)" in sql:
                    return "sql:without_embeddings", _Rows(scalar=self.chunks_without_embeddings)
                return "sql:without_embeddings_rows", _Rows(rows=self.persisted_chunks())
            if "count(*)" in sql:
                return "sql:count_chunks", _Rows(scalar=self.chunks)
            if "DISTINCT" in sql:
                return "sql:chunked_audios", _Rows(rows=self.chunked_audio_ids)
        if "FROM transcripts" in sql:
            return "sql:eligible_transcripts", _Rows(rows=self.transcripts)
        if "FROM audios" in sql:
            return "sql:eligible_audio_ids", _Rows(rows=self.eligible_audio_ids)
        if "FROM segments" in sql:
            return "sql:segments", _Rows(rows=self.segments)
        raise AssertionError(f"consulta no preparada en el stub: {sql}")


class FakeProvider:
    """Deterministic provider: one vector per text, no network."""

    def __init__(
        self, *, model: str | None = None, dimension: int | None = None, batch_size: int = 100
    ) -> None:
        self._model = settings.embedding_model if model is None else model
        self._dimension = settings.embedding_dimension if dimension is None else dimension
        self._batch_size = batch_size
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.25] * self._dimension for _ in texts]


def _db(session: StubSession) -> AsyncSession:
    """The stub fulfils the contract the services use; the cast declares it for mypy."""
    return cast(AsyncSession, session)


def _generation(user_id: uuid.UUID, *, status: str = indexing.INDEX_BUILDING) -> IndexGeneration:
    return IndexGeneration(
        id=uuid.uuid4(),
        user_id=user_id,
        version=VERSION,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        status=status,
        chunk_count=0,
    )


def _transcript(user_id: uuid.UUID, *, text: str = "Hola, mundo. Adiós.") -> Transcript:
    return Transcript(
        id=uuid.uuid4(),
        user_id=user_id,
        audio_id=uuid.uuid4(),
        version=1,
        language_requested="es",
        model="whisper-large-v3",
        model_config={},
        timestamp_precision="none",
        text=text,
        char_count=len(text),
        fts_config="spanish",
    )


def _session_with_audio(
    user_id: uuid.UUID, *, status: str = indexing.INDEX_BUILDING
) -> StubSession:
    transcript = _transcript(user_id)
    segment = Segment(
        id=uuid.uuid4(),
        user_id=user_id,
        transcript_id=transcript.id,
        ordinal=0,
        text=transcript.text,
        char_start=0,
        char_end=len(transcript.text),
    )
    session = StubSession(
        generation=_generation(user_id, status=status),
        transcripts=[transcript],
        segments=[segment],
        chunks=1,
        embeddings=1,
    )
    session.eligible_audio_ids = [transcript.audio_id]
    return session


# --- 9. Opening the generation ------------------------------------------------------


async def test_indexing_creates_building_generation() -> None:
    """`create_generation` opens the generation in `building` with model and dimension."""
    user_id = uuid.uuid4()
    db = StubSession()

    generation = await indexing.create_generation(_db(db), user_id=user_id, version=VERSION)

    assert generation.status == indexing.INDEX_BUILDING
    assert generation.activated_at is None
    assert (generation.model, generation.dimension) == (
        settings.embedding_model,
        settings.embedding_dimension,
    )
    assert db.events == ["add:IndexGeneration", "flush", "commit"]


# --- 10. Activation in a single transaction -------------------------------------------


async def test_indexing_activation_is_one_transaction() -> None:
    """Activation chains retire→activate→pointers with **one single** commit."""
    user_id = uuid.uuid4()
    audio_id = uuid.uuid4()
    db = StubSession(
        generation=_generation(user_id),
        eligible_audio_ids=[audio_id],
        chunked_audio_ids=[audio_id],
        chunks=3,
        embeddings=3,
    )

    generation = await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert db.events == [
        "sql:generation",
        "sql:count_chunks",
        "sql:count_embeddings",
        "sql:without_embeddings",
        "sql:eligible_audio_ids",
        "sql:chunked_audios",
        "sql:retire_active",
        "sql:activate",
        "sql:audio_versions",
        "sql:clear_stale_audio_versions",
        "commit",
    ]
    assert db.commits == 1
    assert generation.status == indexing.INDEX_ACTIVE
    assert generation.chunk_count == 3


async def test_indexing_activation_lost_rolls_back() -> None:
    """If the activation `UPDATE` does not affect one row, everything is rolled back."""
    user_id = uuid.uuid4()
    audio_id = uuid.uuid4()
    db = StubSession(
        generation=_generation(user_id),
        eligible_audio_ids=[audio_id],
        chunked_audio_ids=[audio_id],
        chunks=3,
        embeddings=3,
        activate_rowcount=0,
    )

    with pytest.raises(indexing.GenerationConflict) as failure:
        await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert failure.value.details["reason"] == "activation_lost"
    assert db.events[-1] == "rollback"
    assert db.commits == 0


# --- 11. Never a partial index ------------------------------------------------------


async def test_indexing_refuses_incomplete_activation() -> None:
    """A chunk without embeddings blocks activation: no partial indexes."""
    user_id = uuid.uuid4()
    audio_id = uuid.uuid4()
    db = StubSession(
        generation=_generation(user_id),
        eligible_audio_ids=[audio_id],
        chunked_audio_ids=[audio_id],
        chunks=3,
        embeddings=2,
        chunks_without_embeddings=1,
    )

    with pytest.raises(indexing.GenerationIncomplete) as failure:
        await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert failure.value.details == {"chunks_without_embeddings": 1, "missing_audios": 0}
    assert not any(event.startswith("sql:retire") for event in db.events)


async def test_indexing_refuses_activation_when_audio_is_missing() -> None:
    """An eligible class without chunks also leaves the generation incomplete."""
    user_id = uuid.uuid4()
    db = StubSession(
        generation=_generation(user_id),
        eligible_audio_ids=[uuid.uuid4()],
        chunked_audio_ids=[],
        chunks=3,
        embeddings=3,
    )

    with pytest.raises(indexing.GenerationIncomplete) as failure:
        await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert failure.value.details["missing_audios"] == 1
    assert db.commits == 0


# --- 12. Empty corpus -----------------------------------------------------------------


async def test_indexing_refuses_empty_corpus() -> None:
    """Activating an empty generation would hide the good index: `generation_empty`."""
    user_id = uuid.uuid4()
    db = StubSession(generation=_generation(user_id), chunks=0, embeddings=0)

    with pytest.raises(indexing.GenerationEmpty) as failure:
        await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert failure.value.code == "generation_empty"
    assert db.events[:3] == ["sql:generation", "sql:count_chunks", "sql:count_embeddings"]
    assert not any(event.startswith("sql:retire") for event in db.events)
    assert db.commits == 0


# --- 13. Model and dimension drift -------------------------------------------------


async def test_indexing_rejects_dimension_drift_before_writing() -> None:
    """A provider of another dimension is rejected before touching the database."""
    user_id = uuid.uuid4()
    db = _session_with_audio(user_id)

    with pytest.raises(EmbeddingDimensionMismatch) as failure:
        await indexing.build_generation(
            _db(db),
            user_id=user_id,
            version=VERSION,
            provider=FakeProvider(dimension=1024),
            text_kind="synthetic",
        )

    assert failure.value.details["provider"] == 1024
    assert db.added == []
    assert db.commits == 0


async def test_indexing_rejects_model_drift_before_writing() -> None:
    """A provider of another model cannot write vectors into the registered generation."""
    user_id = uuid.uuid4()
    db = _session_with_audio(user_id)

    with pytest.raises(indexing.GenerationConflict) as failure:
        await indexing.build_generation(
            _db(db),
            user_id=user_id,
            version=VERSION,
            provider=FakeProvider(model="otro-modelo"),
            text_kind="synthetic",
        )

    assert failure.value.details["reason"] == "model_drift"
    assert db.added == []


# --- 14. The retired version is retired, not deleted (review of PR #71) ----------------


async def test_indexing_activation_never_deletes_retired_chunks() -> None:
    """Activation flips status/pointers only: no `DELETE` runs inside the transaction.

    The retained rows keep a rollback to the previous version possible (physical cleanup of
    retired generations is J2.4's retention policy, plan §5), so a `DELETE` here would be a
    regression: the stub still routes it to `sql:purge_retired_chunks` and this test fails.
    """
    user_id = uuid.uuid4()
    audio_id = uuid.uuid4()
    db = StubSession(
        generation=_generation(user_id),
        eligible_audio_ids=[audio_id],
        chunked_audio_ids=[audio_id],
        chunks=2,
        embeddings=2,
    )

    await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert "sql:purge_retired_chunks" not in db.events
    assert not [sql for sql, _ in db.statements if sql.startswith("DELETE")]
    assert db.events.index("sql:activate") < db.events.index("sql:audio_versions")
    assert db.events.index("sql:clear_stale_audio_versions") < db.events.index("commit")
    assert db.events.count("commit") == 1


# --- 15. Idempotency and conflicts ----------------------------------------------------


async def test_indexing_activation_is_idempotent_for_active_version() -> None:
    """Reactivating the already active version is a no-op: no second `retire`."""
    user_id = uuid.uuid4()
    db = StubSession(generation=_generation(user_id, status=indexing.INDEX_ACTIVE))

    generation = await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert generation.status == indexing.INDEX_ACTIVE
    assert db.events == ["sql:generation"]


async def test_indexing_rejects_unknown_and_retired_versions() -> None:
    """Unknown or already retired version: typed conflict, nothing gets touched."""
    user_id = uuid.uuid4()

    with pytest.raises(indexing.GenerationConflict) as unknown:
        await indexing.activate_generation(
            _db(StubSession(generation=None)), user_id=user_id, version=VERSION
        )
    assert unknown.value.details["reason"] == "unknown_version"

    retired = StubSession(generation=_generation(user_id, status=indexing.INDEX_RETIRED))
    with pytest.raises(indexing.GenerationConflict) as stale:
        await indexing.activate_generation(_db(retired), user_id=user_id, version=VERSION)
    assert stale.value.details["reason"] == "not_building"
    assert retired.commits == 0


# --- 16. D3-Texto gate -------------------------------------------------------------


def test_indexing_real_text_requires_closed_d3(monkeypatch: pytest.MonkeyPatch) -> None:
    """While D3-Texto is open only `synthetic`/`redacted` pass; `real` needs the closure."""
    assert indexing.require_text_kind("synthetic") == "synthetic"
    assert indexing.require_text_kind("redacted") == "redacted"

    with pytest.raises(indexing.IndexTextNotAllowed) as failure:
        indexing.require_text_kind("real")
    assert failure.value.details == {"model": settings.embedding_model}

    monkeypatch.setattr(settings, "d3_text_closed_models", (settings.embedding_model,))
    assert indexing.require_text_kind("real") == "real"


# --- 17. Chunking with the building version --------------------------------------


async def test_indexing_uses_chunker_with_the_new_version() -> None:
    """`Chunk`s (L2.1/L2.9) are sealed with the new version and each one gets its vector."""
    user_id = uuid.uuid4()
    db = _session_with_audio(user_id)
    provider = FakeProvider(batch_size=100)

    stats = await indexing.build_generation(
        _db(db), user_id=user_id, version=VERSION, provider=provider, text_kind="synthetic"
    )

    chunks = db.persisted_chunks()
    embeddings = db.persisted_embeddings()
    assert len(chunks) == len(embeddings) == stats.chunks == stats.embeddings == 1
    assert {chunk.index_version for chunk in chunks} == {VERSION}
    assert {chunk.id for chunk in chunks} == {row.chunk_id for row in embeddings}
    assert provider.calls == [[chunk.content for chunk in chunks]]


# --- 17b. Lexical vector materialized (L2.3) ------------------------------------


def test_indexing_chunk_tsvector_uses_the_transcript_config() -> None:
    """`tsv` is built with the given `fts_config`, not with a global language."""
    compiled = str(
        indexing.chunk_tsvector("english", "trigonometric substitution").compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert compiled == "to_tsvector(CAST('english' AS REGCONFIG), 'trigonometric substitution')"


async def test_indexing_persists_chunk_tsv_from_transcript_config() -> None:
    """Every persisted chunk carries its `tsv` so the lexical GIN index stays usable."""
    user_id = uuid.uuid4()
    db = _session_with_audio(user_id)

    await indexing.build_generation(
        _db(db), user_id=user_id, version=VERSION, provider=FakeProvider(), text_kind="synthetic"
    )

    (chunk,) = db.persisted_chunks()
    assert chunk.tsv is not None
    tsv = cast(ColumnElement[Any], chunk.tsv)
    compiled = str(tsv.compile(compile_kwargs={"literal_binds": True}))
    assert "to_tsvector" in compiled
    assert "spanish" in compiled
    assert chunk.content in compiled


# --- 18. Tenant isolation --------------------------------------------------------


async def test_indexing_every_statement_is_tenant_scoped() -> None:
    """Every query and every written row carries the authenticated `user_id`."""
    user_id = uuid.uuid4()
    db = _session_with_audio(user_id)

    await indexing.build_generation(
        _db(db), user_id=user_id, version=VERSION, provider=FakeProvider(), text_kind="synthetic"
    )
    db.chunked_audio_ids = [chunk.audio_id for chunk in db.persisted_chunks()]
    await indexing.activate_generation(_db(db), user_id=user_id, version=VERSION)

    assert db.statements
    for sql, params in db.statements:
        scoped = {value for name, value in params.items() if name.startswith("user_id")}
        assert scoped == {user_id}, sql
    assert {obj.user_id for obj in db.added} == {user_id}
    assert db.rollbacks == 0
