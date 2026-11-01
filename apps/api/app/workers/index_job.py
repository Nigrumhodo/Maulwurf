"""ARQ `index(audio_id, transcript_version)` job (J2.2, I-S2-JF-02).

One delivery runs in four steps, each in its own transaction:

1. Text gate: `require_text_kind` (D3-Texto, fail closed). While D3 is open, the job is a no-op
   and writes nothing.
2. Revalidate and claim the `index` run (`services/index_job.py`). A failed check or a live
   lease is a no-op.
3. Build the generation and activate it (`services/indexing.py`). Activation is one
   transaction, so a failure before it leaves the generation `building` and invisible.
4. Record the run as `succeeded`, `cancelled` (superseded) or `failed` with a typed code.

Retries: only transient errors are retried (`workers/index_errors.py`), with the outbox backoff
(`DEFAULT_RETRY`). ARQ `max_tries` is `MAX_TRIES` for this function only: one delivery plus
three retries. Permanent errors are logged and not raised, so ARQ does not retry them. Before
a retry is scheduled, the run is `failed` and its lease is released, so the next delivery can
claim it.
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final

from arq.worker import Retry
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import (
    bind_cross_tenant,
    bind_user,
    clear_user,
    reset_cross_tenant,
    session_scope,
)
from app.services import indexing
from app.services.embeddings import EmbeddingProvider, HttpEmbeddingProvider
from app.services.index_job import (
    IndexSkip,
    RunClaim,
    RunFenced,
    RunFenceOutcome,
    activate_claimed_generation,
    claim_run,
    finish_run,
    generation_for_version,
    generation_skip,
    index_version_for,
    require_build_fence,
    revalidate,
)
from app.services.indexing import INDEX_BUILDING
from app.services.outbox_retry import DEFAULT_RETRY
from app.workers.index_errors import classify_index_error
from app.workers.outbox_errors import ErrorKind

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ProviderFactory = Callable[[], EmbeddingProvider]

# ctx keys for injecting a database or a provider. Only tests set them; the worker uses defaults.
SESSIONS_KEY: Final = "index_session_factory"
PROVIDERS_KEY: Final = "index_provider_factory"

# One delivery plus three transient retries (ESPECIFICACION J2.2: max 3 transients).
MAX_TRIES: Final = 4
# Below `services.index_job.LEASE`: a live job must never look expired to another worker.
JOB_TIMEOUT_S: Final = 600
# Class text is "real": indexing it stays closed until D3-Texto is closed for the model.
TEXT_KIND: Final = "real"
SUPERSEDED_CODE: Final = "generation_superseded"
RETRY_EXHAUSTED_CODE: Final = "retry_exhausted"


@dataclass(frozen=True)
class _Claim:
    user_id: uuid.UUID
    run: RunClaim


async def index(ctx: dict[str, Any], audio_id: str, transcript_version: int) -> None:
    """ARQ entry point. Receives only IDs; never raises for a permanent failure."""
    job_try = int(ctx.get("job_try", 1))
    retries_left = job_try < MAX_TRIES
    try:
        await run_index(
            _sessions(ctx),
            _providers(ctx),
            uuid.UUID(audio_id),
            transcript_version,
            retries_left=retries_left,
        )
    except Exception as exc:
        # Only the type: messages can echo provider or database content.
        if classify_index_error(exc) is ErrorKind.TRANSIENT and retries_left:
            delay = DEFAULT_RETRY.delay(job_try).total_seconds()
            logger.warning(
                "index.retry_scheduled audio_id=%s transcript_version=%s attempt=%d error=%s",
                audio_id, transcript_version, job_try, type(exc).__name__,
            )
            raise Retry(defer=delay) from exc
        logger.warning(
            "index.failed audio_id=%s transcript_version=%s attempt=%d error=%s",
            audio_id, transcript_version, job_try, type(exc).__name__,
        )
    finally:
        # The ARQ worker may reuse the task context for the next delivery: never leave a
        # tenant (or the cross-tenant switch) behind.
        clear_user()


async def run_index(
    sessions: SessionFactory,
    providers: ProviderFactory,
    audio_id: uuid.UUID,
    transcript_version: int,
    *,
    retries_left: bool,
) -> None:
    """One delivery: gate, claim, build, activate, record. Re-raises failures after recording."""
    try:
        indexing.require_text_kind(TEXT_KIND)
    except indexing.IndexTextNotAllowed:
        logger.info("index.noop reason=%s audio_id=%s", indexing.IndexTextNotAllowed.code, audio_id)
        return
    claim = await _claim_cross_tenant(sessions, audio_id, transcript_version)
    if isinstance(claim, IndexSkip):
        logger.info("index.noop reason=%s audio_id=%s transcript_version=%s",
                    claim, audio_id, transcript_version)
        return
    # The claim is the last cross-tenant read: the owner is known now, so the build,
    # activation and finish transactions are bound to that user_id by RLS.
    bind_user(claim.user_id)
    version = index_version_for(audio_id, transcript_version)
    try:
        skip = await _index_generation(sessions, providers, claim, version)
    except RunFenced:
        # The reconciler closed and requeued this run: the recovery delivery owns it now.
        logger.info("index.noop reason=run_fenced run_id=%s", claim.run.run_id)
        return
    except Exception as exc:
        await _record(
            sessions, claim.run, "failed", _error_code(exc, retries_left)
        )
        raise
    if skip is None or skip is IndexSkip.ALREADY_INDEXED:
        await _record(sessions, claim.run, "succeeded", None)
        return
    logger.info("index.noop reason=%s audio_id=%s transcript_version=%s",
                skip, audio_id, transcript_version)
    await _record(sessions, claim.run, "cancelled", SUPERSEDED_CODE)


async def _claim_cross_tenant(
    sessions: SessionFactory, audio_id: uuid.UUID, transcript_version: int
) -> _Claim | IndexSkip:
    """Read the audio/run without a known owner, then release the cross-tenant switch.

    The job receives IDs only, so the claim is the one step that must run under the
    explicit `maulwurf_jobs` policy (A2.17). `run_index` binds the returned user_id for
    everything after it.
    """
    token = bind_cross_tenant()
    try:
        return await _claim(sessions, audio_id, transcript_version)
    finally:
        reset_cross_tenant(token)


async def _claim(
    sessions: SessionFactory, audio_id: uuid.UUID, transcript_version: int
) -> _Claim | IndexSkip:
    async with sessions() as db:
        revalidated = await revalidate(
            db, audio_id=audio_id, transcript_version=transcript_version
        )
        if isinstance(revalidated, IndexSkip):
            return revalidated
        claimed = await claim_run(
            db,
            user_id=revalidated,
            audio_id=audio_id,
            transcript_version=transcript_version,
        )
        if isinstance(claimed, IndexSkip):
            return claimed
        return _Claim(user_id=revalidated, run=claimed)


async def _index_generation(
    sessions: SessionFactory,
    providers: ProviderFactory,
    claim: _Claim,
    version: str,
) -> IndexSkip | None:
    """Builds and activates the generation. Returns a skip when it already settled, else None."""
    user_id = claim.user_id

    async def fence_build(db: AsyncSession) -> None:
        """Rejects a stale claim before every build commit (A2.18)."""
        await require_build_fence(
            db,
            run_id=claim.run.run_id,
            fencing_token=claim.run.fencing_token,
        )

    async with sessions() as db:
        existing = await generation_for_version(db, user_id=user_id, version=version)
        if existing is None:
            # The shared run lock held by this fence serializes with the reconciler until the
            # generation row commits, so a stale worker cannot recreate an orphan build.
            await fence_build(db)
            # A commit expires the ORM object, so the status of a new row is known, not re-read.
            await indexing.create_generation(db, user_id=user_id, version=version)
            status = INDEX_BUILDING
        else:
            status = existing.status
    settled = generation_skip(status)
    if settled is not None:
        return settled
    provider = providers()
    async with sessions() as db:
        await indexing.build_generation(
            db,
            user_id=user_id,
            version=version,
            provider=provider,
            text_kind=TEXT_KIND,
            fence=fence_build,
        )
    async with sessions() as db:
        await activate_claimed_generation(
            db, user_id=user_id, version=version, claim=claim.run
        )
    return None


async def _record(
    sessions: SessionFactory, claim: RunClaim, status: str, error_code: str | None
) -> None:
    try:
        async with sessions() as db:
            outcome = await finish_run(
                db,
                run_id=claim.run_id,
                fencing_token=claim.fencing_token,
                status=status,
                error_code=error_code,
            )
        if outcome is RunFenceOutcome.FENCED:
            logger.info("index.noop reason=run_fenced run_id=%s", claim.run_id)
    except Exception as exc:
        # The lease expires on its own; the run is then claimable again.
        logger.warning("index.record_failed run_id=%s error=%s", claim.run_id, type(exc).__name__)


def _error_code(exc: Exception, retries_left: bool) -> str:
    if classify_index_error(exc) is ErrorKind.TRANSIENT and not retries_left:
        return RETRY_EXHAUSTED_CODE
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) else "index_unexpected"


def _sessions(ctx: dict[str, Any]) -> SessionFactory:
    factory: SessionFactory = ctx.get(SESSIONS_KEY, session_scope)
    return factory


def _providers(ctx: dict[str, Any]) -> ProviderFactory:
    factory: ProviderFactory = ctx.get(PROVIDERS_KEY, HttpEmbeddingProvider.from_settings)
    return factory
