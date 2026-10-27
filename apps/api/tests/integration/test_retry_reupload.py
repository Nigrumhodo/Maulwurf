"""I-S2-AN-08 contra PostgreSQL real: la matriz `retry` vs `reupload` (A2.8, gate G3).

Cubre el camino feliz del retry sobre texto con runs idempotentes (claves únicas reales de
`processing_runs` y `outbox_events`), los cuatro conflictos tipados del contrato
(`transcript_unavailable` sin texto u obsoleto; `invalid_transition` con stage no requerido,
ya `succeeded` o intento aún activo incluido `transcript_committed_cleanup_pending`), la
forma del body (`422`), el `201` del reupload (misma clase, nueva sesión, `sha256` limpiado,
consentimiento copiado, historia intacta) y los 401/403/404 de aislamiento. El lado
decisional puro vive en `tests/unit/test_retry_reupload.py` (U-S2-AN-08).

Los handlers hacen `commit()` y expiran los atributos del ORM, así que cada test captura los
IDs en variables planas ANTES de la petición (patrón de `actors.Actor`); los helpers reciben
esos valores planos y no filas.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import CSRF_HEADER
from app.main import app
from app.models import Audio, IngestionAttempt, OutboxEvent, ProcessingRun
from app.models.base import TenantOwned
from app.services import runs
from app.services.runs import RUN_CONFIG_VERSION
from tests.integration import actors

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _count[T: TenantOwned](db: AsyncSession, model: type[T], user_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(model).where(model.user_id == user_id)
    )
    return int(result.scalar_one())


async def _events(db: AsyncSession, user_id: uuid.UUID, audio_id: uuid.UUID) -> list[OutboxEvent]:
    result = await db.execute(
        select(OutboxEvent).where(
            OutboxEvent.user_id == user_id, OutboxEvent.resource_id == audio_id
        )
    )
    return list(result.scalars())


async def _runs(db: AsyncSession, user_id: uuid.UUID, audio_id: uuid.UUID) -> list[ProcessingRun]:
    result = await db.execute(
        select(ProcessingRun).where(
            ProcessingRun.user_id == user_id, ProcessingRun.audio_id == audio_id
        )
    )
    return list(result.scalars())


async def _seed_failed_run(
    db: AsyncSession, actor: actors.Actor, audio_id: uuid.UUID, *, stage: str = "index"
) -> uuid.UUID:
    """Run terminal `failed` con la clave con la que el retry colisiona; devuelve su ID."""
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio_id,
        stage=stage,
        transcript_version=1,
        config_version=RUN_CONFIG_VERSION,
        status="failed",
        error_code="worker_crashed",
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()
    return run.id


async def _retry(
    client: AsyncClient,
    actor: actors.Actor,
    audio_id: uuid.UUID,
    stages: list[str],
    version: int = 1,
) -> Response:
    return await client.post(
        f"/audios/{audio_id}/retry",
        json={"stages": stages, "transcript_version": version},
        cookies=actor.cookies,
        headers=actor.headers,
    )


async def _reupload(client: AsyncClient, actor: actors.Actor, audio_id: uuid.UUID) -> Response:
    return await client.post(
        f"/audios/{audio_id}/reupload", cookies=actor.cookies, headers=actor.headers
    )


# --- A. Retry: runs idempotentes sobre texto persistido ----------------------------------


async def test_retry_requeues_a_failed_run_and_emits_one_enabled_event(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    run_id = await _seed_failed_run(db_session, actor, audio_id)

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 202
    body = response.json()
    assert body["audio_id"] == str(audio_id)
    assert body["transcript_version"] == 1
    assert body["runs"] == [{"stage": "index", "run_id": str(run_id), "status": "pending"}]
    # Run reseteado en la BD: vuelve a pending sin el error de su ciclo anterior.
    refreshed = await db_session.get(ProcessingRun, run_id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.error_code is None
    assert refreshed.finished_at is None
    assert refreshed.attempt_count == 1
    # Un único evento habilitado por ciclo, con la versión del transcript y el dedupe
    # del run: el dispatcher lo publica solo tras cleanup verificado (defensa en profundidad).
    events = await _events(db_session, actor.user_id, audio_id)
    assert len(events) == 1
    event = events[0]
    assert event.type == "index_requested"
    assert event.enabled is True
    assert event.blocked_reason is None
    assert event.resource_version == 1
    assert event.dedupe_key == f"{run_id}:1"


async def test_retry_commits_so_the_runs_outlive_the_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`get_session` never commits (`core/db.py`): a handler that forgets `commit()` still
    looks green here. The fixture joins the outer transaction with a savepoint, so rows the
    handler merely flushed are visible to the test either way; being outside a transaction
    right after the request is what proves the run and its event were really persisted.
    """
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 202
    assert not db_session.in_transaction()


async def test_retry_creates_the_missing_run_when_none_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 202
    runs = await _runs(db_session, actor.user_id, audio_id)
    assert len(runs) == 1
    assert (runs[0].stage, runs[0].status, runs[0].attempt_count) == ("index", "pending", 0)
    events = await _events(db_session, actor.user_id, audio_id)
    assert len(events) == 1
    assert events[0].dedupe_key == f"{runs[0].id}:0"


async def test_retry_is_idempotent_and_duplicates_no_work(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    first = await _retry(client, actor, audio_id, ["index"])
    second = await _retry(client, actor, audio_id, ["index"])

    assert (first.status_code, second.status_code) == (202, 202)
    first_run = first.json()["runs"][0]
    second_run = second.json()["runs"][0]
    # El mismo run, no uno nuevo: la clave única es la que hace el trabajo idempotente.
    assert second_run["run_id"] == first_run["run_id"]
    assert second_run["status"] == "pending"
    assert len(await _runs(db_session, actor.user_id, audio_id)) == 1
    assert len(await _events(db_session, actor.user_id, audio_id)) == 1


async def test_retry_both_stages_when_both_are_owed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(
        db_session, actor, required_stages=["index", "analyze"]
    )
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["index", "analyze"])

    assert response.status_code == 202
    assert [run["stage"] for run in response.json()["runs"]] == ["index", "analyze"]
    events = await _events(db_session, actor.user_id, audio_id)
    assert {event.type for event in events} == {"index_requested", "analyze_requested"}


async def test_concurrent_retries_in_opposite_stage_order_do_not_deadlock(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two retries of the same stages in opposite order must not deadlock (A2.8).

    `request_retry` takes one `FOR UPDATE` row lock per stage. Iterating the caller's order
    would let one transaction lock `index` while the other locks `analyze`, and each then
    asking for the row the other holds is exactly the cycle PostgreSQL aborts as a deadlock.
    Locking in the canonical `sorted(stages)` order makes both retries contend for the SAME
    first row, so the second one only waits for the first to commit.

    Uses its own committed rows and one real connection per request: the rolled-back per-test
    session cannot hold a lock another connection would see. A barrier before the first lock
    releases both requests together, so each grabs its first row before asking for its second
    and the race is reproduced deterministically.
    """
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def real_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async with factory() as setup:
        actor = await actors.make_actor(setup)
        audio, _attempt, _transcript = await actors.make_class_with_transcript(
            setup, actor, required_stages=["index", "analyze"]
        )
        audio_id = audio.id
        for stage in ("index", "analyze"):
            await _seed_failed_run(setup, actor, audio_id, stage=stage)
        await setup.commit()

    real_locked_run = runs._locked_run
    barrier = asyncio.Barrier(2)
    first_lock_seen: set[int] = set()

    async def synchronized_locked_run(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        stage: str,
        transcript_version: int,
    ) -> ProcessingRun | None:
        task = asyncio.current_task()
        if task is not None and id(task) not in first_lock_seen:
            first_lock_seen.add(id(task))
            # Neither request holds a lock yet: release them together so each takes its first
            # row before asking for its second (the window the deadlock needs).
            await barrier.wait()
        return await real_locked_run(
            db,
            user_id=user_id,
            audio_id=audio_id,
            stage=stage,
            transcript_version=transcript_version,
        )

    monkeypatch.setattr(runs, "_locked_run", synchronized_locked_run)
    app.dependency_overrides[get_session] = real_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            first, second = await asyncio.gather(
                _retry(client, actor, audio_id, ["index", "analyze"]),
                _retry(client, actor, audio_id, ["analyze", "index"]),
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    assert (first.status_code, second.status_code) == (202, 202)


# --- B. Retry: los cuatro conflictos tipados ---------------------------------------------


async def test_retry_without_transcript_is_transcript_unavailable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "transcript_unavailable"
    # I-S2-AN-08 asks for the message of each case, not only its code (DISENO §3.5).
    assert error["message"] == (
        "Esta clase no tiene una transcripción utilizable; vuelve a subir el audio."
    )
    assert error["details"] == {"active_transcript_version": None}
    assert await _runs(db_session, actor.user_id, audio_id) == []


async def test_retry_with_an_obsolete_version_conflicts_and_reports_the_active_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["index"], version=2)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "transcript_unavailable"
    assert error["details"] == {"active_transcript_version": 1}
    assert await _runs(db_session, actor.user_id, audio_id) == []


async def test_retry_of_a_stage_that_is_not_owed_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    # `make_class_with_transcript` deja `required_stages=['index']` (el default de F1).
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    response = await _retry(client, actor, audio_id, ["analyze"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["message"] == "La operación no es válida para el estado actual de esta clase."
    assert error["details"]["stage"] == "analyze"
    assert "index" in error["details"]["required_stages"]


async def test_retry_of_a_succeeded_stage_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    run = ProcessingRun(
        user_id=actor.user_id,
        audio_id=audio_id,
        stage="index",
        transcript_version=1,
        config_version=RUN_CONFIG_VERSION,
        status="succeeded",
    )
    db_session.add(run)
    await db_session.flush()
    run_id = run.id

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["stage_status"] == "succeeded"
    # El run exitoso no se reescribe: re-ejecutar con la misma config es trabajo redundante.
    refreshed = await db_session.get(ProcessingRun, run_id)
    assert refreshed is not None
    assert refreshed.status == "succeeded"
    assert await _events(db_session, actor.user_id, audio_id) == []


async def test_retry_while_cleanup_is_pending_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    # El commit ocurrió pero la limpieza aún no está verificada: texto a medio borrar.
    attempt.status = "transcript_committed_cleanup_pending"
    await db_session.flush()

    response = await _retry(client, actor, audio_id, ["index"])

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["current_status"] == "transcript_committed_cleanup_pending"
    assert await _runs(db_session, actor.user_id, audio_id) == []


# --- C. Retry: forma del body (422) ------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"stages": [], "transcript_version": 1},
        {"stages": ["index", "index"], "transcript_version": 1},
        {"stages": ["asr"], "transcript_version": 1},
        {"stages": ["index"], "transcript_version": 0},
        {"stages": ["index"], "transcript_version": 1, "force": True},
    ],
)
async def test_retry_rejects_bad_shapes_as_422(
    client: AsyncClient, db_session: AsyncSession, body: dict[str, Any]
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id

    response = await client.post(
        f"/audios/{audio_id}/retry", json=body, cookies=actor.cookies, headers=actor.headers
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# --- D. Reupload: nueva sesión efímera sobre la misma clase ------------------------------


async def test_reupload_opens_a_new_session_and_keeps_the_history(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    audio.sha256 = "a" * 64
    # Copias planas de la identidad de la clase y del consentimiento previo, leídas antes
    # de la petición (el commit del handler expira los atributos del ORM).
    audio_id = audio.id
    subject_id = audio.subject_id
    class_date = audio.class_date
    class_timezone = audio.class_timezone
    language_code = audio.language_code
    previous = await actors.make_attempt(db_session, actor, audio=audio)
    previous.status = "requires_reupload"
    previous.error_code = "technical_loss"
    previous_id = previous.id
    previous_notice = previous.privacy_notice_version
    previous_providers = previous.declared_providers
    previous_cloud_at = previous.cloud_processing_accepted_at
    previous_voice_at = previous.third_party_voice_acknowledged_at
    await db_session.flush()

    response = await _reupload(client, actor, audio_id)

    assert response.status_code == 201
    body = response.json()
    assert body["audio_id"] == str(audio_id)
    assert body["outcome"] == "reupload"
    assert body["upload_url"] == f"/audios/{audio_id}/content?attempt_id={body['attempt_id']}"
    assert response.headers["Location"] == body["upload_url"]
    # The handler committed: the new attempt exists beyond this request (see the retry
    # commit test for why an uncommitted handler would otherwise stay invisible).
    assert not db_session.in_transaction()
    # Sesión nueva en la MISMA clase: la identidad no cambia y el hash vuelve a ser
    # desconocido para que el `PUT` posterior decida el dedupe por su cuenta.
    refreshed_audio = await db_session.get(Audio, audio_id)
    assert refreshed_audio is not None
    assert refreshed_audio.sha256 is None
    assert refreshed_audio.subject_id == subject_id
    assert refreshed_audio.class_date == class_date
    assert refreshed_audio.class_timezone == class_timezone
    assert refreshed_audio.language_code == language_code
    new_attempt = await db_session.get(IngestionAttempt, body["attempt_id"])
    assert new_attempt is not None
    assert new_attempt.status == "awaiting_upload"
    assert new_attempt.fencing_token == 1
    assert new_attempt.audio_id == audio_id
    assert new_attempt.owner_instance == "api:unassigned"
    assert new_attempt.privacy_notice_version == previous_notice
    assert new_attempt.declared_providers == previous_providers
    assert new_attempt.cloud_processing_accepted_at == previous_cloud_at
    assert new_attempt.third_party_voice_acknowledged_at == previous_voice_at
    # El intento anterior queda intacto como historia (su causa no se reescribe).
    old_attempt = await db_session.get(IngestionAttempt, previous_id)
    assert old_attempt is not None
    assert old_attempt.status == "requires_reupload"
    assert old_attempt.error_code == "technical_loss"


async def test_reupload_conflicts_when_a_transcript_already_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    attempts_before = await _count(db_session, IngestionAttempt, actor.user_id)

    response = await _reupload(client, actor, audio_id)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["reason"] == "transcript_available"
    # Ni sesión nueva ni hash tocado: el texto utilizable manda «retry», no «subir otra vez».
    assert await _count(db_session, IngestionAttempt, actor.user_id) == attempts_before
    refreshed = await db_session.get(Audio, audio_id)
    assert refreshed is not None
    assert refreshed.active_transcript_version == 1


async def test_reupload_conflicts_while_an_attempt_is_active(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    audio_id = audio.id
    await actors.make_attempt(db_session, actor, audio=audio)  # `awaiting_upload` vigente
    attempts_before = await _count(db_session, IngestionAttempt, actor.user_id)

    response = await _reupload(client, actor, audio_id)

    # El índice parcial `attempts_one_active` lo impediría igualmente; la API lo dice en
    # 409 en vez de dejar que la BD estalle en un 500.
    assert response.status_code == 409
    assert response.json()["error"]["details"]["current_status"] == "awaiting_upload"
    assert await _count(db_session, IngestionAttempt, actor.user_id) == attempts_before


async def test_reupload_after_a_reupload_conflicts_again(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)
    audio_id = audio.id
    previous = await actors.make_attempt(db_session, actor, audio=audio)
    previous.status = "requires_reupload"
    await db_session.flush()

    first = await _reupload(client, actor, audio_id)
    second = await _reupload(client, actor, audio_id)

    assert (first.status_code, second.status_code) == (201, 409)
    # La BD nunca ve dos intentos activos para la misma clase.
    result = await db_session.execute(
        select(IngestionAttempt.status).where(
            IngestionAttempt.user_id == actor.user_id,
            IngestionAttempt.audio_id == audio_id,
            IngestionAttempt.status.in_(("awaiting_upload", "receiving", "transcribing")),
        )
    )
    assert len(result.all()) == 1


# --- E. Aislamiento y guards (401/403/404) -----------------------------------------------


async def test_foreign_class_is_404_on_both_endpoints(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, owner)
    audio_id = audio.id
    stranger = await actors.make_actor(db_session)

    retry = await _retry(client, stranger, audio_id, ["index"])
    reupload = await _reupload(client, stranger, audio_id)

    assert (retry.status_code, reupload.status_code) == (404, 404)
    assert await _runs(db_session, owner.user_id, audio_id) == []


async def test_both_endpoints_require_session_csrf_and_origin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    retry_url = f"/audios/{audio_id}/retry"
    reupload_url = f"/audios/{audio_id}/reupload"
    body = {"stages": ["index"], "transcript_version": 1}

    assert (await client.post(retry_url, json=body)).status_code == 401
    assert (await client.post(reupload_url)).status_code == 401
    no_csrf = await client.post(
        retry_url, json=body, cookies=actor.cookies, headers={"Origin": settings.allowed_origin}
    )
    assert no_csrf.status_code == 403
    bad_origin = await client.post(
        reupload_url,
        cookies=actor.cookies,
        headers={"Origin": "https://evil.test", CSRF_HEADER: actor.csrf_token},
    )
    assert bad_origin.status_code == 403
    # Nada de lo anterior escribe: la clase sigue sin runs ni sesiones nuevas.
    assert await _runs(db_session, actor.user_id, audio_id) == []
    assert await _count(db_session, IngestionAttempt, actor.user_id) == 1


async def test_tombstoned_class_is_404_on_both_endpoints(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _attempt, _transcript = await actors.make_class_with_transcript(db_session, actor)
    audio_id = audio.id
    audio.deleted_at = datetime.now(UTC)
    await db_session.flush()

    retry = await _retry(client, actor, audio_id, ["index"])
    reupload = await _reupload(client, actor, audio_id)

    assert (retry.status_code, reupload.status_code) == (404, 404)
    assert await _runs(db_session, actor.user_id, audio_id) == []
