"""I-S2-AN-07 contra PostgreSQL real: SSE de progreso con snapshot desde la BD (A2.9).

Cubre que cada conexión/reconexión reenvía un `snapshot` fresco leído de la base (estado, no
incremento), que los terminales cierran con un único `done`/`error`, y el aislamiento
(401/404). El lado puro (snapshot/encoding) vive en `tests/unit/test_progress.py`; el delta no
terminal se prueba iterando `library.stream_progress` directamente porque el `ASGITransport`
de httpx 0.28 bufferiza el cuerpo y no permite leer un stream abierto.

El stream abre sesiones cortas con la factoría inyectada (`progress_session_factory`), así
que aquí esa factoría se sustituye por una real sobre la BD migrada y las filas se confirman
para que el generador (otra conexión) las vea. `get_session` (la dependencia de request con
`yield`) se sustituye por un GUARD que falla si alguien la toca: la ruta SSE debe autenticar y
comprobar ownership en sus propias sesiones cortas, sin retener una conexión de request durante
todo el stream (fix de agotamiento de pool de A2.9).
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import progress_session_factory
from app.main import app
from app.models import IngestionAttempt, ProcessingRun
from app.services import library
from app.services.runs import RUN_CONFIG_VERSION
from tests.integration import actors

pytestmark = pytest.mark.integration


def _frames(body: str) -> list[str]:
    return [frame for frame in body.split("\n\n") if frame and not frame.startswith(":")]


def _event_name(frame: str) -> str:
    return frame.split("\n", 1)[0].removeprefix("event: ")


def _event_data(frame: str) -> dict[str, Any]:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return cast(dict[str, Any], json.loads(line[len("data: ") :]))
    raise AssertionError(f"frame sin data: {frame!r}")


@pytest.fixture
async def sse(
    migrated_database_url: str,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async def forbidden_request_session() -> AsyncSession:
        # The SSE route must NOT depend on the request-scoped `get_session`: FastAPI would
        # keep its yield cleanup open for the whole StreamingResponse, pinning a pool
        # connection per client (A2.9 pool-exhaustion fix). If this is ever called, the
        # route regressed and the test fails loudly.
        raise AssertionError("SSE must not use the request-scoped get_session")

    app.dependency_overrides[get_session] = forbidden_request_session
    app.dependency_overrides[progress_session_factory] = lambda: factory
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=settings.allowed_origin
        ) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _terminal_class(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[actors.Actor, uuid.UUID]:
    async with factory() as setup:
        actor = await actors.make_actor(setup)
        audio, _, _ = await actors.make_class_with_transcript(setup, actor)
        audio.duration_seconds = 60.0
        setup.add(
            ProcessingRun(
                user_id=actor.user_id,
                audio_id=audio.id,
                stage="index",
                transcript_version=1,
                config_version=RUN_CONFIG_VERSION,
                status="succeeded",
            )
        )
        await setup.commit()
        return actor, audio.id


async def test_progress_sends_db_snapshot_then_done(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = sse
    actor, audio_id = await _terminal_class(factory)

    response = await client.get(f"/audios/{audio_id}/progress", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    frames = _frames(response.text)
    assert [_event_name(frame) for frame in frames] == ["snapshot", "done"]
    payload = _event_data(frames[0])
    assert payload["aggregate"] == "ready"
    assert payload["attempt"]["status"] == "succeeded"
    assert payload["attempt"]["received_bytes"] == 0
    assert payload["attempt"]["fragments_done"] is None
    assert payload["stages"] == {"index": "succeeded", "analyze": "not_requested"}
    assert _event_data(frames[1]) == {"aggregate": "ready", "stages": payload["stages"]}


async def test_progress_resent_snapshot_on_reconnect(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = sse
    actor, audio_id = await _terminal_class(factory)

    response = await client.get(
        f"/audios/{audio_id}/progress",
        cookies=actor.cookies,
        headers={"Last-Event-ID": "42"},
    )

    assert response.status_code == 200
    assert _event_name(_frames(response.text)[0]) == "snapshot"


async def test_progress_of_a_failed_class_closes_with_error(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = sse
    async with factory() as setup:
        actor = await actors.make_actor(setup)
        audio = await actors.make_audio(setup, actor)
        attempt = await actors.make_attempt(setup, actor, audio=audio)
        attempt.status = "rejected"
        attempt.error_code = "no_usable_text"
        await setup.commit()
        audio_id = audio.id

    response = await client.get(f"/audios/{audio_id}/progress", cookies=actor.cookies)

    assert response.status_code == 200
    frames = _frames(response.text)
    assert [_event_name(frame) for frame in frames] == ["snapshot", "error"]
    assert _event_data(frames[1]) == {"code": "no_usable_text", "detail": "rejected"}


async def test_progress_without_attempt_or_transcript_uses_aggregate_failed(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = sse
    async with factory() as setup:
        actor = await actors.make_actor(setup)
        audio = await actors.make_audio(setup, actor)  # reservada, sin intento ni texto
        await setup.commit()
        audio_id = audio.id

    response = await client.get(f"/audios/{audio_id}/progress", cookies=actor.cookies)

    assert response.status_code == 200
    frames = _frames(response.text)
    assert [_event_name(frame) for frame in frames] == ["snapshot", "error"]
    # No attempt to name the failure, so the terminal code is the documented
    # `aggregate_failed` fallback (S2.md §A2.9), never an invented `error_code`.
    assert _event_data(frames[1]) == {"code": "aggregate_failed", "detail": None}


async def test_progress_generator_reports_new_measured_bytes(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = sse
    async with factory() as setup:
        actor = await actors.make_actor(setup)
        audio = await actors.make_audio(setup, actor)
        attempt = await actors.make_attempt(setup, actor, audio=audio)
        attempt.status = "transcribing"
        attempt.received_bytes = 100
        attempt.fragments_done = 1
        attempt.fragments_total = 4
        await setup.commit()
        user_id, audio_id, attempt_id = actor.user_id, audio.id, attempt.id

    stream = library.stream_progress(
        factory,
        user_id=user_id,
        audio_id=audio_id,
        poll_seconds=0.02,
        heartbeat_seconds=60.0,
        max_seconds=30.0,
    )
    try:
        snapshot = await anext(stream)
        assert _event_name(snapshot) == "snapshot"
        assert _event_data(snapshot)["attempt"]["received_bytes"] == 100
        assert _event_data(snapshot)["aggregate"] == "processing"

        async with factory() as writer:
            await writer.execute(
                update(IngestionAttempt)
                .where(IngestionAttempt.id == attempt_id)
                .values(received_bytes=200, fragments_done=2)
            )
            await writer.commit()

        progress_frame = await anext(stream)
        assert _event_name(progress_frame) == "progress"
        assert _event_data(progress_frame) == {
            "received_bytes": 200,
            "fragments_done": 2,
            "fragments_total": 4,
        }
    finally:
        await stream.aclose()


async def test_progress_is_owner_only(
    sse: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = sse
    async with factory() as setup:
        owner = await actors.make_actor(setup)
        stranger = await actors.make_actor(setup)
        audio, _, _ = await actors.make_class_with_transcript(setup, owner)
        await setup.commit()
        audio_id = audio.id

    assert (
        await client.get(f"/audios/{audio_id}/progress", cookies=stranger.cookies)
    ).status_code == 404
    assert (await client.get(f"/audios/{audio_id}/progress")).status_code == 401
    assert (
        await client.get(f"/audios/{uuid.uuid4()}/progress", cookies=owner.cookies)
    ).status_code == 404
