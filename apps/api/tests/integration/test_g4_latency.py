"""G4 integration tests for the L2.8 latency harness on PostgreSQL (fake providers).

The suite uses the real ``SearchService``/``RAGService`` paths, the real migrations and the
isolated evaluation tenants. Embeddings are a deterministic hashed bag of words and the LLM
is scripted: no network call, no real provider, no audio and no official L2.8 report.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import bind_user
from app.models import Audio, IndexGeneration, User
from app.services.llm import ChatMessage
from app.services.rag import RAGService
from tools.eval import latency_runner
from tools.eval.corpus import (
    _reset_tenant,
    eval_google_sub,
    eval_user_id,
    prepared_index_from_dict,
)
from tools.eval.dataset import Dataset, load_dataset
from tools.eval.latency_runner import measure_latency
from tools.eval.runner import (
    EXIT_CONFIG,
    EXIT_NOT_EVALUATED,
    EvalExit,
    RunManifest,
    prepare_run,
)

pytestmark = pytest.mark.integration

DATASETS = Path(__file__).resolve().parents[1] / "fixtures" / "g4" / "datasets"
SMOKE = DATASETS / "smoke-v1"
V1 = DATASETS / "v1"


class HashEmbeddingProvider:
    """Deterministic hashed bag-of-words provider with the configured model/dimension."""

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self.batch_size = settings.embedding_batch_size
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        tokens = re.findall(r"[\wÀ-ÿ]+", text.casefold())
        features = tokens + [
            f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False)
        ]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


class ScriptedLLM:
    """Streams one deterministic first delta for every prepared prompt."""

    model = settings.llm_model
    context_window = 32768

    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        prompt = messages[-1].content if messages else ""
        if '<source id="S1"' in prompt:
            yield "Respuesta sintética basada en la evidencia [S1]."
            return
        yield "No encuentro evidencia suficiente en tus clases para responder a eso."


class EmptyLLM(ScriptedLLM):
    """Ends the stream without any delta, simulating a provider that produced no text."""

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        for _ in ():
            yield ""


class FailingLLM(ScriptedLLM):
    """Raises before the first delta so the stream emits an error event."""

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        raise RuntimeError("provider down")
        yield ""  # pragma: no cover - keeps this an async generator


class CloseFailingStream:
    """Yields one valid delta and then fails to close, like a broken cleanup path."""

    def __init__(self) -> None:
        self.sent = False

    def __aiter__(self) -> CloseFailingStream:
        return self

    async def __anext__(self) -> str:
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return 'event: delta\ndata: {"text": "hola"}\n\n'

    async def aclose(self) -> None:
        raise RuntimeError("close failed")


@pytest.fixture(scope="module")
def smoke() -> Dataset:
    return load_dataset(SMOKE)


@pytest.fixture(scope="module")
def v1() -> Dataset:
    return load_dataset(V1)


@pytest.fixture
async def session_factory(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_latency_rows(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
) -> AsyncIterator[None]:
    await _delete_marked_users(session_factory, smoke)
    yield
    await _delete_marked_users(session_factory, smoke)


async def _delete_marked_users(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
) -> None:
    for language in dataset.manifest.languages:
        user_id = eval_user_id(dataset.version, language)
        async with session_factory() as db:
            user = await db.get(User, user_id)
            if user is not None:
                await _reset_tenant(
                    db,
                    user,
                    expected_sub=eval_google_sub(dataset.version, language),
                )


async def _prepare(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    *,
    run_dir: Path,
    language: str = "es",
    require_nontrivial: bool = False,
) -> tuple[RunManifest, HashEmbeddingProvider, ScriptedLLM]:
    embedding = HashEmbeddingProvider()
    llm = ScriptedLLM()
    run = await prepare_run(
        session_factory,
        dataset,
        languages=(language,),
        embedding_provider=embedding,
        llm_provider=llm,
        run_dir=run_dir,
        run_id=f"l2.8-{uuid.uuid4().hex[:8]}",
        reset=True,
        require_nontrivial=require_nontrivial,
    )
    return run, embedding, llm


def _keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        found: set[str] = {str(key) for key in value}
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _keys(item)
        return found
    return set()


async def test_g4_latency_measures_retrieval_and_first_token_on_smoke_corpus(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"

    outcome = await measure_latency(
        session_factory,
        smoke,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        allow_smoke=True,
        publish_dir=publish,
    )

    assert outcome.exit_code == EXIT_NOT_EVALUATED
    assert outcome.payload["status"] == "SMOKE"
    assert publish.is_dir()
    language = outcome.payload["languages"]["es"]
    expected = len(smoke.test_items("es"))
    assert language["expected_items"] == expected
    assert language["retrieval"]["n"] == expected
    assert language["first_token"]["n"] == expected
    assert language["first_token"]["llm_n"] >= 1
    for metric in ("p50_ms", "p95_ms", "max_ms"):
        assert isinstance(language["retrieval"][metric], (int, float))
        assert isinstance(language["first_token"][metric], (int, float))
    assert language["cold_start"]["retrieval_ms"] is not None
    assert language["cold_start"]["first_token_ms"] is not None
    assert embedding.calls > 0
    assert llm.calls > 0


async def test_g4_latency_measurement_requires_active_generation_and_hits(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    index = prepared_index_from_dict(run.prepared).languages["es"]
    bind_user(index.user_id)

    async with session_factory() as db:
        await db.execute(
            update(IndexGeneration)
            .where(
                IndexGeneration.user_id == index.user_id,
                IndexGeneration.version == index.generation,
            )
            .values(status="retired")
        )
        await db.commit()

    retired_publish = tmp_path / "retired"
    with pytest.raises(EvalExit) as retired:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=True,
            publish_dir=retired_publish,
        )
    assert retired.value.exit_code == EXIT_NOT_EVALUATED
    assert not retired_publish.exists()

    async with session_factory() as db:
        await db.execute(
            update(IndexGeneration)
            .where(
                IndexGeneration.user_id == index.user_id,
                IndexGeneration.version == index.generation,
            )
            .values(status="active")
        )
        await db.execute(
            update(Audio)
            .where(Audio.user_id == index.user_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await db.commit()

    empty_publish = tmp_path / "empty"
    with pytest.raises(EvalExit) as empty:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=True,
            publish_dir=empty_publish,
        )
    assert empty.value.exit_code == EXIT_NOT_EVALUATED
    assert not empty_publish.exists()


async def test_g4_latency_official_publication_rejects_fake_providers(
    session_factory: async_sessionmaker[AsyncSession],
    v1: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(
        session_factory,
        v1,
        run_dir=run_dir,
        require_nontrivial=True,
    )
    publish = tmp_path / "published"

    with pytest.raises(EvalExit) as rejected:
        await measure_latency(
            session_factory,
            v1,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=False,
            publish_dir=publish,
        )

    assert rejected.value.exit_code == EXIT_NOT_EVALUATED
    assert "medición oficial rechazada" in rejected.value.message
    assert not publish.exists()


async def test_g4_latency_official_publication_rejects_non_official_dataset(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"

    with pytest.raises(EvalExit) as rejected:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=False,
            publish_dir=publish,
        )

    assert rejected.value.exit_code == EXIT_NOT_EVALUATED
    assert "50/35/15" in rejected.value.message
    assert not publish.exists()


async def test_g4_latency_report_is_redacted(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"
    await measure_latency(
        session_factory,
        smoke,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        allow_smoke=True,
        publish_dir=publish,
    )

    question = smoke.test_items("es")[0].question
    report = (publish / "REPORT.md").read_text(encoding="utf-8")
    data_text = (publish / "latency.json").read_text(encoding="utf-8")
    data = json.loads(data_text)

    assert question not in report
    assert question not in data_text
    keys = _keys(data)
    assert "question" not in keys
    assert "answer" not in keys
    assert "quote" not in keys
    assert "content" not in keys
    assert "transcript" not in keys


async def test_g4_latency_publish_records_date_environment_and_n(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"
    outcome = await measure_latency(
        session_factory,
        smoke,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        allow_smoke=True,
        publish_dir=publish,
    )

    data = json.loads((publish / "latency.json").read_text(encoding="utf-8"))
    report = (publish / "REPORT.md").read_text(encoding="utf-8")

    assert data["measured_at"]
    assert data["environment"]["python"]
    assert data["environment"]["cpu_count"]
    assert data["dataset_hash"] == f"sha256:{smoke.content_sha256}"
    assert data["languages"]["es"]["retrieval"]["n"] == len(smoke.test_items("es"))
    assert data["measured_at"] == outcome.payload["measured_at"]
    assert data["measured_at"] in report
    assert data["dataset_hash"] in report
    assert data["environment"]["python"] in report
    assert data["environment"]["memory_total_mb"]
    assert data["environment"]["postgres"].startswith("PostgreSQL")
    assert data["environment"]["pgvector"]
    assert data["environment"]["redis"] == "not used by the in-process measurement"
    assert any("N=4 per language" in limitation for limitation in data["limitations"])
    assert not any("N=50" in limitation for limitation in data["limitations"])


async def test_g4_latency_official_run_rejects_dirty_tree(
    session_factory: async_sessionmaker[AsyncSession],
    v1: Dataset,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(
        session_factory,
        v1,
        run_dir=run_dir,
        require_nontrivial=True,
    )
    publish = tmp_path / "published"
    monkeypatch.setattr(latency_runner, "_require_official_providers", lambda *args: None)
    monkeypatch.setattr(latency_runner, "_git_metadata", lambda: ("a" * 40, True))

    with pytest.raises(EvalExit) as rejected:
        await measure_latency(
            session_factory,
            v1,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=False,
            publish_dir=publish,
        )

    assert rejected.value.exit_code == EXIT_CONFIG
    assert "árbol de trabajo sucio" in rejected.value.message
    assert not publish.exists()


async def test_g4_latency_budget_exhaustion_publishes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"

    with pytest.raises(EvalExit) as exhausted:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=llm,
            allow_smoke=True,
            max_provider_calls=1,
            publish_dir=publish,
        )

    assert exhausted.value.exit_code == EXIT_CONFIG
    assert "presupuesto" in exhausted.value.message
    assert not publish.exists()


async def test_g4_latency_stream_without_delta_is_not_evaluated(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, _llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"

    with pytest.raises(EvalExit) as missing:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=EmptyLLM(),
            allow_smoke=True,
            publish_dir=publish,
        )

    assert missing.value.exit_code == EXIT_NOT_EVALUATED
    assert "sin primer delta" in missing.value.message
    assert not publish.exists()


async def test_g4_latency_stream_error_is_not_evaluated(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, _llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"

    with pytest.raises(EvalExit) as failed:
        await measure_latency(
            session_factory,
            smoke,
            run_dir=run_dir,
            embedding_provider=embedding,
            llm_provider=FailingLLM(),
            allow_smoke=True,
            publish_dir=publish,
        )

    assert failed.value.exit_code == EXIT_NOT_EVALUATED
    assert "error" in failed.value.message
    assert not publish.exists()


async def test_g4_latency_close_failure_does_not_discard_first_delta(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    publish = tmp_path / "published"
    monkeypatch.setattr(
        RAGService,
        "stream_events",
        lambda self, prepared, **kwargs: CloseFailingStream(),
    )

    outcome = await measure_latency(
        session_factory,
        smoke,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        allow_smoke=True,
        publish_dir=publish,
    )

    assert outcome.exit_code == EXIT_NOT_EVALUATED
    assert outcome.payload["status"] == "SMOKE"
    assert outcome.payload["languages"]["es"]["first_token"]["n"] == len(
        smoke.test_items("es")
    )
    assert any(
        "failed to close" in limitation for limitation in outcome.payload["limitations"]
    )


async def test_g4_latency_without_development_item_keeps_first_test_item(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    _run, embedding, llm = await _prepare(session_factory, smoke, run_dir=run_dir)
    without_development = replace(
        smoke,
        items={
            language: tuple(item for item in items if item.partition != "development")
            for language, items in smoke.items.items()
        },
    )
    publish = tmp_path / "published"

    outcome = await measure_latency(
        session_factory,
        without_development,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        allow_smoke=True,
        publish_dir=publish,
    )

    assert outcome.payload["languages"]["es"]["retrieval"]["n"] == len(
        smoke.test_items("es")
    )
    assert "first test item" in outcome.payload["cold_start"]
    assert any(
        "no development item" in limitation for limitation in outcome.payload["limitations"]
    )
