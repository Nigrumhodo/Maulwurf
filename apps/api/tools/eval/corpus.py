"""Seed the isolated G4 evaluation tenants and build their text index (L2.7).

Each language gets its own tenant, subject and audio/transcript rows. No audio bytes, file,
hash or storage key are ever created: the corpus is authored text only. The reset path is
scoped by the deterministic tenant id and verified against the evaluation marker before it
deletes anything, so it cannot touch a real user.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.models import (
    Audio,
    Chunk,
    ChunkSegment,
    Conversation,
    Embedding,
    IndexGeneration,
    Message,
    MessageSource,
    Segment,
    Subject,
    Transcript,
    User,
)
from app.services.embeddings import EmbeddingProvider
from app.services.indexing import activate_generation, build_generation, create_generation
from tools.eval.dataset import CorpusClass, Dataset, fts_config

_USER_NAMESPACE = uuid.UUID("6f3f5f40-5d2b-4a0e-9f3c-4d1c9b7a2e10")


class CorpusError(RuntimeError):
    """The evaluation corpus cannot be seeded or verified."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True)
class ClassIndex:
    """Database identities of one seeded synthetic class."""

    class_ref: str
    audio_id: uuid.UUID
    transcript_id: uuid.UUID


@dataclass(frozen=True)
class LanguageIndex:
    """Database identities and index metadata for one evaluation language."""

    language: str
    user_id: uuid.UUID
    subject_id: uuid.UUID
    generation: str
    chunks: int
    classes: Mapping[str, ClassIndex]

    def audio_id(self, class_ref: str) -> uuid.UUID:
        """Audio id for an evidence class; raises when the class is unknown."""
        try:
            return self.classes[class_ref].audio_id
        except KeyError as exc:
            raise CorpusError([f"class_ref no sembrado: {class_ref}"]) from exc


@dataclass(frozen=True)
class PreparedIndex:
    """All language indexes of one prepared dataset."""

    dataset_version: str
    dataset_hash: str
    created_at: str
    languages: Mapping[str, LanguageIndex]


class _ClassIndexModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_ref: str
    audio_id: uuid.UUID
    transcript_id: uuid.UUID


class _LanguageIndexModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    user_id: uuid.UUID
    subject_id: uuid.UUID
    generation: str
    chunks: int = Field(ge=0)
    classes: list[_ClassIndexModel]


class _PreparedIndexModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    dataset_hash: str
    created_at: str
    languages: list[_LanguageIndexModel]


def eval_user_id(dataset_version: str, language: str) -> uuid.UUID:
    """Deterministic tenant id; a re-run reuses the same isolated user."""
    return uuid.uuid5(_USER_NAMESPACE, f"maulwurf:g4:{dataset_version}:{language}")


def eval_google_sub(dataset_version: str, language: str) -> str:
    """Marker that must be present before a reset is allowed."""
    return f"g4-eval:{dataset_version}:{language}"


def eval_email(language: str) -> str:
    """Non-routable address; the evaluation tenant never authenticates."""
    return f"g4-eval+{language}@example.invalid"


async def prepare_dataset(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    languages: Sequence[str],
    *,
    provider: EmbeddingProvider,
    reset: bool,
    require_nontrivial: bool = True,
) -> PreparedIndex:
    """Seed every requested language and build/activate its generation."""
    _require_provider_matches(provider)
    indexes: dict[str, LanguageIndex] = {}
    for language in languages:
        await _prepare_language(
            session_factory,
            dataset,
            language,
            provider=provider,
            reset=reset,
        )
        index = await _load_language_index(session_factory, dataset, language)
        failures = await verify_evidence(session_factory, dataset, language, index)
        if failures:
            raise CorpusError(
                [f"{language}: evidencia no recuperable en {item_id}" for item_id in failures]
            )
        if require_nontrivial and index.chunks <= 8:
            raise CorpusError(
                [
                    f"{language}: {index.chunks} chunks; Recall@8 necesita más de 8 "
                    "para no ser trivial"
                ]
            )
        indexes[language] = index
    return PreparedIndex(
        dataset_version=dataset.version,
        dataset_hash=dataset.content_sha256,
        created_at=datetime.now(UTC).isoformat(),
        languages=indexes,
    )


async def _prepare_language(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    language: str,
    *,
    provider: EmbeddingProvider,
    reset: bool,
) -> None:
    classes = dataset.corpus.get(language)
    if not classes:
        raise CorpusError([f"sin corpus para el idioma {language}"])
    user_id = eval_user_id(dataset.version, language)
    expected_sub = eval_google_sub(dataset.version, language)
    version = _generation_version(dataset)

    async with session_factory() as db:
        existing = await db.get(User, user_id)
        if existing is not None:
            if existing.google_sub != expected_sub:
                raise CorpusError(
                    [
                        f"rechazado: el usuario {user_id} no es el tenant G4 esperado "
                        f"({existing.google_sub!r} != {expected_sub!r})"
                    ]
                )
            active = await db.scalar(
                select(IndexGeneration).where(
                    IndexGeneration.user_id == user_id,
                    IndexGeneration.status == "active",
                )
            )
            if not reset and active is not None and active.version == version:
                return  # Idempotent re-use: the same dataset hash is already active.
            await _reset_tenant(db, existing, expected_sub=expected_sub)
        other = await db.scalar(select(User).where(User.google_sub == expected_sub))
        if other is not None:
            raise CorpusError([f"google_sub de evaluación ya usado por otro id: {expected_sub}"])

        user = User(
            id=user_id,
            google_sub=expected_sub,
            email=eval_email(language),
            display_name="G4 evaluation (synthetic)",
            timezone="UTC",
        )
        db.add(user)
        await db.flush()
        subject = Subject(
            user_id=user_id,
            name=f"G4 {dataset.version} {language}",
            teacher="Synthetic corpus",
        )
        db.add(subject)
        await db.flush()
        for corpus_class in classes:
            await _add_class(db, user_id=user_id, subject_id=subject.id, corpus_class=corpus_class)
        await db.commit()

    async with session_factory() as db:
        await create_generation(db, user_id=user_id, version=version)
    async with session_factory() as db:
        stats = await build_generation(
            db,
            user_id=user_id,
            version=version,
            provider=provider,
            text_kind="synthetic",
        )
    async with session_factory() as db:
        await activate_generation(db, user_id=user_id, version=version)
    if stats.chunks != stats.embeddings:
        raise CorpusError(
            [
                f"{language}: {stats.chunks} chunks y {stats.embeddings} embeddings; "
                "la generación quedaría incompleta"
            ]
        )


async def _add_class(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    subject_id: uuid.UUID,
    corpus_class: CorpusClass,
) -> None:
    audio = Audio(
        user_id=user_id,
        subject_id=subject_id,
        title=corpus_class.class_ref,
        teacher="Synthetic corpus",
        class_date=corpus_class.class_date,
        class_timezone="UTC",
        language_code=corpus_class.language,
        active_transcript_version=corpus_class.transcript_version,
    )
    db.add(audio)
    # Flush now so the transcript can reference the generated audio id.
    await db.flush()
    text = corpus_class.text
    transcript = Transcript(
        user_id=user_id,
        audio_id=audio.id,
        version=corpus_class.transcript_version,
        language_requested=corpus_class.language,
        provider="g4-authored",
        model="g4-authored-v1",
        model_config={
            "source": "g4-dataset",
            "class_ref": corpus_class.class_ref,
        },
        timestamp_precision="none",
        text=text,
        char_count=len(text),
        fts_config=fts_config(corpus_class.language),
    )
    db.add(transcript)
    await db.flush()
    cursor = 0
    for position, segment in enumerate(corpus_class.segments):
        # The chunker requires contiguous segment ranges, while the transcript joins the
        # authored segments with "\n". Store the separator on the preceding segment so the
        # ranges cover the exact transcript text and evidence offsets inside the authored
        # text stay valid.
        stored = segment.text
        if position < len(corpus_class.segments) - 1:
            stored += "\n"
        start = cursor
        end = start + len(stored)
        db.add(
            Segment(
                user_id=user_id,
                transcript_id=transcript.id,
                ordinal=segment.ordinal,
                text=stored,
                char_start=start,
                char_end=end,
                t_start=None,
                t_end=None,
            )
        )
        cursor = end
    if cursor != len(text):
        raise CorpusError(
            [f"{corpus_class.class_ref}: offsets de segmento no cubren el transcript"]
        )


async def _reset_tenant(db: AsyncSession, user: User, *, expected_sub: str) -> None:
    if user.google_sub != expected_sub:
        raise CorpusError(
            [
                f"rechazado: el usuario {user.id} no es el tenant G4 esperado "
                f"({user.google_sub!r} != {expected_sub!r})"
            ]
        )
    user_id = user.id
    await db.execute(delete(MessageSource).where(MessageSource.user_id == user_id))
    await db.execute(delete(Message).where(Message.user_id == user_id))
    await db.execute(delete(Conversation).where(Conversation.user_id == user_id))
    await db.execute(delete(Embedding).where(Embedding.user_id == user_id))
    await db.execute(delete(ChunkSegment).where(ChunkSegment.user_id == user_id))
    await db.execute(delete(Chunk).where(Chunk.user_id == user_id))
    await db.execute(delete(Segment).where(Segment.user_id == user_id))
    await db.execute(delete(Transcript).where(Transcript.user_id == user_id))
    await db.execute(delete(IndexGeneration).where(IndexGeneration.user_id == user_id))
    await db.execute(delete(Audio).where(Audio.user_id == user_id))
    await db.execute(delete(Subject).where(Subject.user_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.commit()


async def _load_language_index(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    language: str,
) -> LanguageIndex:
    user_id = eval_user_id(dataset.version, language)
    version = _generation_version(dataset)
    async with session_factory() as db:
        subject = await db.scalar(
            select(Subject).where(
                Subject.user_id == user_id,
                Subject.name == f"G4 {dataset.version} {language}",
            )
        )
        if subject is None:
            raise CorpusError([f"{language}: falta la materia de evaluación"])
        generation = await db.scalar(
            select(IndexGeneration).where(
                IndexGeneration.user_id == user_id,
                IndexGeneration.version == version,
                IndexGeneration.status == "active",
            )
        )
        if generation is None:
            raise CorpusError([f"{language}: la generación no quedó activa"])
        rows = await db.execute(
            select(Audio.title, Audio.id, Transcript.id)
            .join(
                Transcript,
                (Transcript.user_id == Audio.user_id) & (Transcript.audio_id == Audio.id),
            )
            .where(
                Audio.user_id == user_id,
                Audio.active_transcript_version == Transcript.version,
                Audio.deleted_at.is_(None),
            )
        )
        classes: dict[str, ClassIndex] = {}
        for class_ref, audio_id, transcript_id in rows:
            if class_ref is None:
                raise CorpusError([f"{language}: audio sin class_ref"])
            classes[class_ref] = ClassIndex(
                class_ref=class_ref,
                audio_id=audio_id,
                transcript_id=transcript_id,
            )
        return LanguageIndex(
            language=language,
            user_id=user_id,
            subject_id=subject.id,
            generation=generation.version,
            chunks=generation.chunk_count,
            classes=classes,
        )


async def verify_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: Dataset,
    language: str,
    index: LanguageIndex,
) -> tuple[str, ...]:
    """Return item ids whose expected quote is absent from every chunk of its class."""
    async with session_factory() as db:
        rows = await db.execute(
            select(Chunk.audio_id, Chunk.content).where(
                Chunk.user_id == index.user_id,
                Chunk.index_version == index.generation,
            )
        )
        by_audio: dict[uuid.UUID, list[str]] = {}
        for audio_id, content in rows:
            by_audio.setdefault(audio_id, []).append(content)
    failures: list[str] = []
    for item in dataset.items[language]:
        for evidence in item.expected_evidence:
            audio_id = index.audio_id(evidence.class_ref)
            if not any(evidence.cita_textual in content for content in by_audio.get(audio_id, [])):
                failures.append(item.id)
                break
    return tuple(sorted(set(failures)))


def _generation_version(dataset: Dataset) -> str:
    return f"g4-{dataset.content_sha256[:12]}"


def _require_provider_matches(provider: EmbeddingProvider) -> None:
    if provider.model != settings.embedding_model:
        raise CorpusError(
            [f"modelo de embeddings {provider.model!r} != {settings.embedding_model!r}"]
        )
    if provider.dimension != settings.embedding_dimension:
        raise CorpusError([f"dimensión {provider.dimension} != {settings.embedding_dimension}"])


def prepared_index_to_dict(index: PreparedIndex) -> dict[str, Any]:
    """JSON-safe representation of a prepared index."""
    model = _PreparedIndexModel(
        dataset_version=index.dataset_version,
        dataset_hash=index.dataset_hash,
        created_at=index.created_at,
        languages=[
            _LanguageIndexModel(
                language=item.language,
                user_id=item.user_id,
                subject_id=item.subject_id,
                generation=item.generation,
                chunks=item.chunks,
                classes=[
                    _ClassIndexModel(
                        class_ref=entry.class_ref,
                        audio_id=entry.audio_id,
                        transcript_id=entry.transcript_id,
                    )
                    for entry in item.classes.values()
                ],
            )
            for item in index.languages.values()
        ],
    )
    return model.model_dump(mode="json")


def prepared_index_from_dict(data: Mapping[str, Any]) -> PreparedIndex:
    """Validate and rebuild a prepared index from JSON data."""
    model = _PreparedIndexModel.model_validate(data)
    languages: dict[str, LanguageIndex] = {}
    for item in model.languages:
        classes = {
            entry.class_ref: ClassIndex(
                class_ref=entry.class_ref,
                audio_id=entry.audio_id,
                transcript_id=entry.transcript_id,
            )
            for entry in item.classes
        }
        languages[item.language] = LanguageIndex(
            language=item.language,
            user_id=item.user_id,
            subject_id=item.subject_id,
            generation=item.generation,
            chunks=item.chunks,
            classes=classes,
        )
    return PreparedIndex(
        dataset_version=model.dataset_version,
        dataset_hash=model.dataset_hash,
        created_at=model.created_at,
        languages=languages,
    )
