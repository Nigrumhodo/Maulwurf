"""G4 end-to-end harness on PostgreSQL with deterministic fake providers (L2.7).

The suite uses the real ``SearchService``/``RAGService`` code paths, the real migrations and
the isolated evaluation tenants, but never a network provider: embeddings are a deterministic
hashed bag of words and the LLM is scripted. The corpus is the authored synthetic
``smoke-v1`` dataset; no audio, real class or PII is involved and no G4 pass is claimed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import AsyncIterator, Collection, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Embedding,
    IndexGeneration,
    Segment,
    Subject,
    Transcript,
    User,
)
from app.services.llm import ChatMessage
from app.services.search import SearchService
from tools.eval import cli as cli_module
from tools.eval.chat import AnswerOutcome, CitationOutcome, answer_to_dict
from tools.eval.cli import _real_providers
from tools.eval.cli import main as cli_main
from tools.eval.corpus import (
    CorpusError,
    _reset_tenant,
    eval_google_sub,
    eval_user_id,
    prepare_dataset,
)
from tools.eval.dataset import Dataset, load_dataset
from tools.eval.report import build_review_rows, write_review_sheet
from tools.eval.retrieval import _load_links, _match_evidence
from tools.eval.runner import (
    EXIT_ADJUDICATION,
    EXIT_CONFIG,
    EXIT_GATE_FAIL,
    EXIT_NOT_EVALUATED,
    EXIT_PASS,
    BudgetRecord,
    EvalExit,
    RunManifest,
    _exact_official_partition,
    execute_run,
    load_answers,
    prepare_run,
    score_run,
    write_run,
)

pytestmark = pytest.mark.integration

DATASETS = Path(__file__).resolve().parents[1] / "fixtures" / "g4" / "datasets"
SMOKE = DATASETS / "smoke-v1"
V1 = DATASETS / "v1"
LANGUAGES = ("es", "en", "fr")


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
    """Streams a citation for answerables and a refusal for the known unanswerable items."""

    model = settings.llm_model
    context_window = 32768

    def __init__(self, refusals: Collection[str] = ()) -> None:
        self.refusals = set(refusals)
        self.calls = 0

    async def stream(
        self, messages: Sequence[ChatMessage], *, max_tokens: int
    ) -> AsyncIterator[str]:
        self.calls += 1
        prompt = messages[-1].content if messages else ""
        question = prompt.rsplit("Question:", 1)[-1].strip()
        if question in self.refusals:
            yield "No encuentro evidencia suficiente en tus clases para responder a eso."
            return
        if '<source id="S1"' in prompt:
            yield "Respuesta sintética basada en la evidencia [S1]."
            return
        yield "No encuentro evidencia suficiente en tus clases para responder a eso."


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
async def _clean_evaluation_rows(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset, v1: Dataset
) -> AsyncIterator[None]:
    await _delete_marked_users(session_factory, smoke, v1)
    yield
    await _delete_marked_users(session_factory, smoke, v1)


async def _delete_marked_users(
    session_factory: async_sessionmaker[AsyncSession], *datasets: Dataset
) -> None:
    for dataset in datasets:
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
    async with session_factory() as db:
        other_ids = (
            (await db.execute(select(User.id).where(User.google_sub.like("g4-other:%"))))
            .scalars()
            .all()
        )
    for user_id in other_ids:
        async with session_factory() as db:
            user = await db.get(User, user_id)
            if user is not None:
                await _reset_tenant(db, user, expected_sub=user.google_sub)


def _refusals(dataset: Dataset, languages: Sequence[str] = LANGUAGES) -> set[str]:
    return {
        item.question
        for language in languages
        for item in dataset.test_items(language)
        if not item.answerable
    }


def _adjudication_rows(
    rows: Sequence[dict[str, Any]],
    *,
    title: str = "tester",
) -> list[dict[str, Any]]:
    adjudicated: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "citation":
            verdict = "correct"
        else:
            verdict = "abstained" if not row["citations"] else "answered"
        adjudicated.append(
            {
                "row_id": row["row_id"],
                "kind": row["kind"],
                "verdict": verdict,
                "reviewer": title,
                "reviewed_at": "2026-10-30T00:00:00Z",
                "notes": None,
            }
        )
    return adjudicated


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _synthetic_answers(dataset: Dataset, languages: Sequence[str]) -> list[AnswerOutcome]:
    answers: list[AnswerOutcome] = []
    for language in languages:
        for item in dataset.test_items(language):
            if item.answerable:
                citation = CitationOutcome(
                    item_id=item.id,
                    language=language,
                    index=1,
                    chunk_id=str(uuid.uuid4()),
                    segment_id=None,
                    audio_id=str(uuid.uuid4()),
                    transcript_version=1,
                    quote="synthetic evidence",
                    unauthorized=False,
                )
                answers.append(
                    AnswerOutcome(
                        item_id=item.id,
                        language=language,
                        answerable=True,
                        question=item.question,
                        status="completed",
                        answer="Respuesta sintética [S1].",
                        citations=(citation,),
                        sources=1,
                        unauthorized=0,
                        error=None,
                        took_ms=1.0,
                        persisted_citations=1,
                    )
                )
            else:
                answers.append(
                    AnswerOutcome(
                        item_id=item.id,
                        language=language,
                        answerable=False,
                        question=item.question,
                        status="completed",
                        answer="No encuentro evidencia suficiente en tus clases.",
                        citations=(),
                        sources=0,
                        unauthorized=0,
                        error=None,
                        took_ms=1.0,
                        persisted_citations=0,
                    )
                )
    return answers


def _manifest(
    dataset: Dataset,
    languages: Sequence[str],
    *,
    status: str = "complete",
    partition: str = "test",
) -> RunManifest:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return RunManifest(
        run_id=f"g4-test-{uuid.uuid4().hex[:8]}",
        status=status,
        created_at=now,
        updated_at=now,
        dataset_version=dataset.version,
        dataset_hash=dataset.content_sha256,
        dataset_path=str(dataset.path),
        partition=partition,
        languages=list(languages),
        environment={"python": "test", "platform": "test"},
        embedding={"model": settings.embedding_model, "dimension": settings.embedding_dimension},
        llm={"model": settings.llm_model, "context_window": 32768},
        prompt_version="v1",
        budget=BudgetRecord(max_provider_calls=400),
        prepared={},
        items={language: len(dataset.test_items(language)) for language in languages},
    )


def _write_synthetic_run(
    run_dir: Path,
    dataset: Dataset,
    languages: Sequence[str],
    *,
    fail_language: str | None = None,
    status: str = "complete",
    partition: str = "test",
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_run(run_dir, _manifest(dataset, languages, status=status, partition=partition))
    answers = _synthetic_answers(dataset, languages)
    _write_jsonl(run_dir / "answers.jsonl", [answer_to_dict(answer) for answer in answers])
    retrieval: list[dict[str, Any]] = []
    for language in languages:
        answerable_index = 0
        for item in dataset.test_items(language):
            matched = 0
            if item.answerable:
                if language == fail_language:
                    matched = 1 if answerable_index < 29 else 0
                else:
                    matched = len(item.expected_evidence)
                answerable_index += 1
            retrieval.append(
                {
                    "item_id": item.id,
                    "language": language,
                    "answerable": item.answerable,
                    "expected": len(item.expected_evidence),
                    "matched": matched,
                    "took_ms": 1.0,
                    "top_chunk_ids": [],
                }
            )
    _write_jsonl(run_dir / "retrieval.jsonl", retrieval)
    rows = build_review_rows(answers)
    write_review_sheet(run_dir / "review.jsonl", rows)
    _write_jsonl(run_dir / "review.jsonl", _adjudication_rows(rows))
    return run_dir


async def _prepare_es(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> tuple[Any, HashEmbeddingProvider]:
    provider = HashEmbeddingProvider()
    prepared = await prepare_dataset(
        session_factory,
        smoke,
        ("es",),
        provider=provider,
        reset=True,
        require_nontrivial=False,
    )
    return prepared, provider


async def _complete_run(
    session_factory: async_sessionmaker[AsyncSession],
    smoke: Dataset,
    run_dir: Path,
    *,
    languages: Sequence[str] = ("es",),
) -> tuple[RunManifest, HashEmbeddingProvider, ScriptedLLM]:
    embedding = HashEmbeddingProvider()
    llm = ScriptedLLM(refusals=_refusals(smoke, languages))
    await prepare_run(
        session_factory,
        smoke,
        languages=languages,
        embedding_provider=embedding,
        llm_provider=llm,
        run_dir=run_dir,
        run_id=f"g4-{uuid.uuid4().hex[:8]}",
        reset=True,
        require_nontrivial=False,
    )
    run = await execute_run(
        session_factory,
        smoke,
        run_dir=run_dir,
        embedding_provider=embedding,
        llm_provider=llm,
        require_official=False,
    )
    return run, embedding, llm


async def _seed_other_user(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    question: str | None = None,
    provider: HashEmbeddingProvider | None = None,
) -> uuid.UUID:
    async with session_factory() as db:
        user = User(
            google_sub=f"g4-other:{uuid.uuid4()}",
            email="g4-other@example.invalid",
            display_name="Real user",
            timezone="UTC",
        )
        db.add(user)
        await db.flush()
        subject = Subject(user_id=user.id, name="Real subject", teacher="Real teacher")
        db.add(subject)
        await db.flush()
        audio = Audio(
            user_id=user.id,
            subject_id=subject.id,
            title="real-class",
            teacher="Real teacher",
            class_date=date(2026, 1, 1),
            class_timezone="UTC",
            language_code="es",
            active_transcript_version=1,
        )
        db.add(audio)
        await db.flush()
        if question is not None:
            assert provider is not None
            text = question
            transcript = Transcript(
                user_id=user.id,
                audio_id=audio.id,
                version=1,
                language_requested="es",
                provider="synthetic",
                model="synthetic-v1",
                model_config={},
                timestamp_precision="none",
                text=text,
                char_count=len(text),
                fts_config="spanish",
            )
            db.add(transcript)
            await db.flush()
            segment = Segment(
                user_id=user.id,
                transcript_id=transcript.id,
                ordinal=1,
                text=text,
                char_start=0,
                char_end=len(text),
                t_start=None,
                t_end=None,
            )
            db.add(segment)
            await db.flush()
            generation = IndexGeneration(
                user_id=user.id,
                version="real-v1",
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
                status="active",
                chunk_count=1,
            )
            db.add(generation)
            await db.flush()
            chunk = Chunk(
                user_id=user.id,
                audio_id=audio.id,
                transcript_id=transcript.id,
                transcript_version=1,
                index_version="real-v1",
                ordinal=1,
                content=text,
                char_start=0,
                char_end=len(text),
                token_count=len(text.split()),
            )
            db.add(chunk)
            await db.flush()
            vector = (await provider.embed([question]))[0]
            db.add(
                Embedding(
                    user_id=user.id,
                    chunk_id=chunk.id,
                    index_version="real-v1",
                    model=provider.model,
                    dimension=provider.dimension,
                    vector=vector,
                )
            )
            db.add(ChunkSegment(user_id=user.id, chunk_id=chunk.id, segment_id=segment.id))
        await db.commit()
        return user.id


async def test_g4_prepare_seeds_one_isolated_tenant_per_language_and_activates_generation(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> None:
    provider = HashEmbeddingProvider()
    prepared = await prepare_dataset(
        session_factory,
        smoke,
        ("es", "en"),
        provider=provider,
        reset=True,
        require_nontrivial=False,
    )

    assert set(prepared.languages) == {"es", "en"}
    assert prepared.dataset_hash == smoke.content_sha256
    for language in ("es", "en"):
        index = prepared.languages[language]
        assert index.user_id == eval_user_id(smoke.version, language)
        assert index.chunks > 0
        async with session_factory() as db:
            user = await db.get(User, index.user_id)
            assert user is not None
            assert user.google_sub == eval_google_sub(smoke.version, language)
            generation = await db.scalar(
                select(IndexGeneration).where(
                    IndexGeneration.user_id == index.user_id,
                    IndexGeneration.status == "active",
                )
            )
            assert generation is not None
            assert generation.version == index.generation
            assert generation.chunk_count == index.chunks
    assert prepared.languages["es"].user_id != prepared.languages["en"].user_id


async def test_g4_prepare_is_idempotent_and_reset_only_touches_the_eval_tenant(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> None:
    provider = HashEmbeddingProvider()
    first = await prepare_dataset(
        session_factory,
        smoke,
        ("es",),
        provider=provider,
        reset=True,
        require_nontrivial=False,
    )
    other_user_id = await _seed_other_user(session_factory)

    second = await prepare_dataset(
        session_factory,
        smoke,
        ("es",),
        provider=provider,
        reset=False,
        require_nontrivial=False,
    )
    assert second.languages["es"].generation == first.languages["es"].generation

    third = await prepare_dataset(
        session_factory,
        smoke,
        ("es",),
        provider=provider,
        reset=True,
        require_nontrivial=False,
    )
    assert third.languages["es"].generation == first.languages["es"].generation

    async with session_factory() as db:
        other = await db.get(User, other_user_id)
        assert other is not None
        subjects = (
            (
                await db.execute(
                    select(Subject).where(Subject.user_id == first.languages["es"].user_id)
                )
            )
            .scalars()
            .all()
        )
        audios = (
            (await db.execute(select(Audio).where(Audio.user_id == first.languages["es"].user_id)))
            .scalars()
            .all()
        )
        assert len(subjects) == 1
        assert len(audios) == len(smoke.corpus["es"])


async def test_g4_prepare_fails_when_an_evidence_span_is_not_retrievable(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> None:
    provider = HashEmbeddingProvider()
    prepared = await prepare_dataset(
        session_factory,
        smoke,
        ("es",),
        provider=provider,
        reset=True,
        require_nontrivial=False,
    )
    index = prepared.languages["es"]
    item = next(item for item in smoke.test_items("es") if item.answerable)
    evidence = item.expected_evidence[0]
    audio_id = index.audio_id(evidence.class_ref)

    async with session_factory() as db:
        chunks = (
            (
                await db.execute(
                    select(Chunk).where(
                        Chunk.user_id == index.user_id,
                        Chunk.audio_id == audio_id,
                        Chunk.content.contains(evidence.cita_textual),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert chunks
        for chunk in chunks:
            chunk.content = chunk.content.replace(evidence.cita_textual, "texto retirado")
        await db.commit()

    with pytest.raises(CorpusError, match=f"evidencia no recuperable en {item.id}"):
        await prepare_dataset(
            session_factory,
            smoke,
            ("es",),
            provider=provider,
            reset=False,
            require_nontrivial=False,
        )


async def test_g4_retrieval_recall_uses_all_chunk_segment_links(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> None:
    prepared, _ = await _prepare_es(session_factory, smoke)
    index = prepared.languages["es"]
    item = next(item for item in smoke.test_items("es") if item.answerable)
    evidence = item.expected_evidence[0]
    audio_id = index.audio_id(evidence.class_ref)

    async with session_factory() as db:
        chunk = await db.scalar(
            select(Chunk)
            .where(
                Chunk.user_id == index.user_id,
                Chunk.audio_id == audio_id,
                Chunk.content.contains(evidence.cita_textual),
            )
            .limit(1)
        )
        assert chunk is not None
        linked = (
            (
                await db.execute(
                    select(ChunkSegment.segment_id).where(
                        ChunkSegment.user_id == index.user_id,
                        ChunkSegment.chunk_id == chunk.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(linked) < 2:
            other_segment = await db.scalar(
                select(Segment)
                .where(
                    Segment.user_id == index.user_id,
                    Segment.transcript_id == chunk.transcript_id,
                    Segment.id.not_in(linked),
                )
                .limit(1)
            )
            assert other_segment is not None
            db.add(
                ChunkSegment(
                    user_id=index.user_id,
                    chunk_id=chunk.id,
                    segment_id=other_segment.id,
                )
            )
            await db.commit()
        links = await _load_links(db, index.user_id, [chunk.id])
        assert len(links[chunk.id].ordinals) >= 2
        assert _match_evidence(item, index, links) == 1


async def test_g4_retrieval_never_sees_another_language_or_a_real_user(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset
) -> None:
    prepared, provider = await _prepare_es(session_factory, smoke)
    index = prepared.languages["es"]
    item = next(item for item in smoke.test_items("es") if item.answerable)
    other_user_id = await _seed_other_user(
        session_factory, question=item.question, provider=provider
    )
    async with session_factory() as db:
        other_chunk_id = await db.scalar(select(Chunk.id).where(Chunk.user_id == other_user_id))
        hits = await SearchService(db, provider=provider).retrieve(
            user_id=index.user_id,
            query=item.question,
            top_k=8,
        )
        assert hits
        hit_ids = [hit.chunk_id for hit in hits]
        owners = (
            (await db.execute(select(Chunk.user_id).where(Chunk.id.in_(hit_ids)))).scalars().all()
        )
        assert set(owners) == {index.user_id}
        assert other_chunk_id not in hit_ids


async def test_g4_run_writes_a_review_row_for_every_citation_and_unanswerable_item(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    run, _, _ = await _complete_run(session_factory, smoke, run_dir)
    assert run.status == "complete"

    answers = load_answers(run_dir / "answers.jsonl")
    rows = [
        json.loads(line)
        for line in (run_dir / "review.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    expected = sum(len(answer.citations) for answer in answers) + sum(
        1 for answer in answers if not answer.answerable
    )
    assert sum(len(answer.citations) for answer in answers) > 0
    assert len(rows) == expected
    assert all(row["verdict"] is None and row["reviewer"] is None for row in rows)
    assert {row["kind"] for row in rows} <= {"citation", "abstention"}


async def test_g4_run_rejects_an_unauthorized_citation_id(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    await _complete_run(session_factory, smoke, run_dir)
    answers = load_answers(run_dir / "answers.jsonl")
    rows = build_review_rows(answers)
    _write_jsonl(run_dir / "review.jsonl", _adjudication_rows(rows))

    payloads = [
        json.loads(line)
        for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    target = next(row for row in payloads if row["citations"])
    target["unauthorized"] = 1
    target["citations"][0]["unauthorized"] = True
    _write_jsonl(run_dir / "answers.jsonl", payloads)

    outcome = score_run(
        smoke,
        run_dir=run_dir,
        adjudication_path=run_dir / "review.jsonl",
        publish_dir=run_dir / "published",
        require_official=False,
    )
    assert outcome.exit_code == EXIT_GATE_FAIL
    assert outcome.metrics[0].unauthorized == 1


async def test_g4_score_publishes_pass_with_complete_adjudication(
    session_factory: async_sessionmaker[AsyncSession], smoke: Dataset, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    await _complete_run(session_factory, smoke, run_dir)
    answers = load_answers(run_dir / "answers.jsonl")
    rows = build_review_rows(answers)
    _write_jsonl(run_dir / "review.jsonl", _adjudication_rows(rows))

    outcome = score_run(
        smoke,
        run_dir=run_dir,
        adjudication_path=run_dir / "review.jsonl",
        publish_dir=run_dir / "published",
        require_official=False,
    )

    assert outcome.exit_code == EXIT_PASS
    assert outcome.verdict.passed
    payload = json.loads((run_dir / "published" / "metrics.json").read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["dataset_version"] == smoke.version
    assert payload["dataset_hash"] == f"sha256:{smoke.content_sha256}"


async def test_g4_score_exits_3_and_publishes_nothing_with_incomplete_adjudication(
    v1: Dataset, tmp_path: Path
) -> None:
    # The CLI always enforces the official partition, so this path uses ``g4-v1``; smoke
    # runs are scored through ``score_run(require_official=False)`` only.
    run_dir = _write_synthetic_run(tmp_path / "run", v1, LANGUAGES)
    review_path = run_dir / "review.jsonl"
    rows = [
        json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines() if line
    ]
    _write_jsonl(review_path, rows[:-1])

    publish = run_dir / "published"
    exit_code = cli_main(
        [
            "score",
            "--run-id",
            "whatever",
            "--run-dir",
            str(run_dir),
            "--adjudication",
            str(review_path),
            "--publish",
            str(publish),
        ]
    )
    assert exit_code == EXIT_ADJUDICATION
    assert not publish.exists()


async def test_g4_score_fails_a_language_even_when_the_macro_average_passes(
    v1: Dataset, tmp_path: Path
) -> None:
    run_dir = _write_synthetic_run(tmp_path / "run", v1, LANGUAGES, fail_language="es")
    outcome = score_run(
        v1,
        run_dir=run_dir,
        adjudication_path=run_dir / "review.jsonl",
        publish_dir=run_dir / "published",
        require_official=True,
    )

    assert outcome.exit_code == EXIT_GATE_FAIL
    by_language = {item.language: item for item in outcome.metrics}
    macro = sum(item.recall_at_8.value or 0.0 for item in outcome.metrics) / 3
    assert macro >= 0.85
    assert by_language["es"].recall_at_8.numerator == 29
    assert by_language["es"].recall_at_8.denominator == 35
    assert by_language["en"].recall_at_8.value == 1.0
    assert by_language["fr"].recall_at_8.value == 1.0


async def test_g4_score_exits_4_for_errored_or_partial_runs(smoke: Dataset, tmp_path: Path) -> None:
    run_dir = _write_synthetic_run(tmp_path / "run", smoke, ("es",))
    payloads = [
        json.loads(line)
        for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    payloads[0]["status"] = "error"
    payloads[0]["error"] = "scripted failure"
    _write_jsonl(run_dir / "answers.jsonl", payloads)

    with pytest.raises(EvalExit) as failed:
        score_run(
            smoke,
            run_dir=run_dir,
            adjudication_path=run_dir / "review.jsonl",
            publish_dir=run_dir / "published",
            require_official=False,
        )
    assert failed.value.exit_code == EXIT_NOT_EVALUATED

    write_run(run_dir, _manifest(smoke, ("es",), status="incomplete"))
    with pytest.raises(EvalExit) as incomplete:
        score_run(
            smoke,
            run_dir=run_dir,
            adjudication_path=run_dir / "review.jsonl",
            publish_dir=run_dir / "published",
            require_official=False,
        )
    assert incomplete.value.exit_code == EXIT_NOT_EVALUATED


async def test_g4_official_run_requires_real_providers_and_test_partition(
    smoke: Dataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EvalExit) as official:
        _exact_official_partition(smoke)
    assert official.value.exit_code == EXIT_NOT_EVALUATED

    run_dir = _write_synthetic_run(tmp_path / "run", smoke, ("es",))
    with pytest.raises(EvalExit) as scored:
        score_run(
            smoke,
            run_dir=run_dir,
            adjudication_path=run_dir / "review.jsonl",
            publish_dir=run_dir / "published",
            require_official=True,
        )
    assert scored.value.exit_code == EXIT_NOT_EVALUATED

    development = _write_synthetic_run(
        tmp_path / "development", smoke, ("es",), partition="development"
    )
    with pytest.raises(EvalExit) as dev:
        score_run(
            smoke,
            run_dir=development,
            adjudication_path=development / "review.jsonl",
            publish_dir=development / "published",
            require_official=False,
        )
    assert dev.value.exit_code == EXIT_NOT_EVALUATED

    fake = settings.model_copy(
        update={
            "embedding_base_url": None,
            "embedding_api_key": None,
            "llm_base_url": None,
            "llm_api_key": None,
            "llm_model_window": None,
        }
    )
    monkeypatch.setattr(cli_module, "settings", fake)
    with pytest.raises(EvalExit) as providers:
        _real_providers()
    assert providers.value.exit_code == EXIT_NOT_EVALUATED


async def test_g4_report_is_redacted_and_contains_numerator_denominator(
    smoke: Dataset, tmp_path: Path
) -> None:
    run_dir = _write_synthetic_run(tmp_path / "run", smoke, LANGUAGES)
    outcome = score_run(
        smoke,
        run_dir=run_dir,
        adjudication_path=run_dir / "review.jsonl",
        publish_dir=run_dir / "published",
        require_official=False,
    )
    assert outcome.exit_code == EXIT_PASS

    published = run_dir / "published"
    report = (published / "REPORT.md").read_text(encoding="utf-8")
    metrics = (published / "metrics.json").read_text(encoding="utf-8")
    redacted = (published / "adjudication.redacted.jsonl").read_text(encoding="utf-8")

    assert "Recall@8" in report
    assert "3/3" in report
    assert f"sha256:{smoke.content_sha256}" in report
    sample = next(item for item in smoke.test_items("es") if item.answerable)
    for secret in (sample.question, "Respuesta sintética", "synthetic evidence"):
        assert secret not in report
        assert secret not in metrics
        assert secret not in redacted
    assert "row_id" in redacted


async def test_g4_cli_exit_codes_are_stable() -> None:
    assert (EXIT_PASS, EXIT_GATE_FAIL, EXIT_CONFIG, EXIT_ADJUDICATION, EXIT_NOT_EVALUATED) == (
        0,
        1,
        2,
        3,
        4,
    )
    assert cli_main(["validate", "--dataset", str(SMOKE), "--languages", "es,en,fr"]) == EXIT_PASS
    assert (
        cli_main(
            [
                "validate",
                "--dataset",
                str(SMOKE),
                "--languages",
                "es,en,fr",
                "--official",
            ]
        )
        == EXIT_CONFIG
    )
