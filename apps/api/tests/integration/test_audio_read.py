"""I-S2-AN-09 contra PostgreSQL real: listado, detalle y exports TXT/SRT (A2.9, gate G3).

Cubre el listado tenant-scoped (`GET /audios`, filtro `subject_id`), el detalle por etapa con
agregado honesto y último intento (`GET /audios/{id}`), el TXT verbatim con `?segment=`
owner-scoped y la matriz del SRT (200 con tiempos válidos; `409 srt_unavailable` sin precisión,
sin tiempos, desordenado o fuera de la duración), más los 401/404 de aislamiento y el
`Cache-Control: no-store`. El lado decisional puro del SRT vive en
`tests/unit/test_exports.py` (U-S2-AN-09).

Los handlers leen por SQL pero no hacen commit en los GET, así que la sesión del fixture
(savepoint) ve las filas recién `flush`eadas; los IDs se capturan en variables planas.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import ProcessingRun
from app.services.runs import INDEX_CONFIG_VERSION
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


async def test_list_returns_only_the_tenants_live_audios(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _ = await actors.make_class_with_transcript(db_session, actor)
    alien = await actors.make_actor(db_session)
    await actors.make_class_with_transcript(db_session, alien)
    tombstoned = await actors.make_audio(db_session, actor)
    tombstoned.deleted_at = datetime.now(UTC)
    await db_session.flush()

    response = await client.get("/audios", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert {item["id"] for item in items} == {str(audio.id)}
    item = items[0]
    # No index run yet: the required stage is owed, so the honest aggregate is processing.
    assert item["aggregate_status"] == "processing"
    assert item["stages"] == {"index": "pending", "analyze": "not_requested"}
    assert item["attempt"]["status"] == "succeeded"
    assert item["subject"]["id"] == str(audio.subject_id)


async def test_list_filters_by_subject_and_foreign_subject_is_empty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    subject_a = await actors.make_subject(db_session, actor)
    subject_b = await actors.make_subject(db_session, actor)
    audio_a = await actors.make_audio(db_session, actor, subject=subject_a)
    await actors.make_audio(db_session, actor, subject=subject_b)

    only_a = await client.get(f"/audios?subject_id={subject_a.id}", cookies=actor.cookies)
    assert only_a.status_code == 200
    assert {item["id"] for item in only_a.json()["items"]} == {str(audio_a.id)}

    alien = await actors.make_actor(db_session)
    alien_subject = await actors.make_subject(db_session, alien)
    foreign = await client.get(f"/audios?subject_id={alien_subject.id}", cookies=actor.cookies)
    assert foreign.status_code == 200
    assert foreign.json()["items"] == []


async def test_detail_reports_per_stage_aggregate_attempt_and_exports(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, attempt, transcript = await actors.make_class_with_transcript(db_session, actor)
    db_session.add(
        ProcessingRun(
            user_id=actor.user_id,
            audio_id=audio.id,
            stage="index",
            transcript_version=1,
            config_version=INDEX_CONFIG_VERSION,
            status="succeeded",
        )
    )
    await db_session.flush()
    audio_id, attempt_id = audio.id, attempt.id

    response = await client.get(f"/audios/{audio_id}", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["id"] == str(audio_id)
    assert body["aggregate_status"] == "ready"
    assert body["stages"] == {"index": "succeeded", "analyze": "not_requested"}
    assert body["attempt"]["attempt_id"] == str(attempt_id)
    assert body["attempt"]["status"] == "succeeded"
    assert body["attempt"]["received_bytes"] == 0
    assert body["audio_retained"] is False
    assert body["transcript"] == {
        "version": transcript.version,
        "timestamp_precision": "none",
        "char_count": transcript.char_count,
        "quality_warnings": [],
    }
    # No segments and precision `none`: TXT yes, SRT no.
    assert body["exports"] == {"txt": True, "srt": False}


async def test_detail_exposes_only_non_sensitive_quality_warnings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, transcript = await actors.make_class_with_transcript(db_session, actor)
    transcript.quality_flags = [
        {"code": "repetition", "count": 3, "text": "texto de prueba"},
        {"code": "low_confidence", "count": 2, "total": 4},
        {"code": "empty_segments", "count": 1},
        {"code": 3, "count": 1},
    ]
    await db_session.flush()

    response = await client.get(f"/audios/{audio.id}", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.json()["transcript"]["quality_warnings"] == [
        {"code": "repetition", "count": 3},
        {"code": "low_confidence", "count": 2, "total": 4},
        {"code": "empty_segments", "count": 1},
    ]
    assert "texto de prueba" not in response.text


async def test_detail_and_exports_are_404_for_foreign_or_tombstoned(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await actors.make_actor(db_session)
    audio, _, _ = await actors.make_class_with_transcript(db_session, owner)
    stranger = await actors.make_actor(db_session)
    audio_id = audio.id
    paths = (
        f"/audios/{audio_id}",
        f"/audios/{audio_id}/transcript.txt",
        f"/audios/{audio_id}/transcript.srt",
    )

    for path in paths:
        assert (await client.get(path, cookies=stranger.cookies)).status_code == 404

    audio.deleted_at = datetime.now(UTC)
    await db_session.flush()
    for path in paths:
        assert (await client.get(path, cookies=owner.cookies)).status_code == 404


async def test_reads_and_exports_require_a_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _ = await actors.make_class_with_transcript(db_session, actor)

    assert (await client.get("/audios")).status_code == 401
    assert (await client.get(f"/audios/{audio.id}")).status_code == 401
    assert (await client.get(f"/audios/{audio.id}/transcript.txt")).status_code == 401
    assert (await client.get(f"/audios/{audio.id}/transcript.srt")).status_code == 401


async def test_transcript_txt_returns_the_verbatim_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, transcript = await actors.make_class_with_transcript(db_session, actor)

    response = await client.get(f"/audios/{audio.id}/transcript.txt", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.text == transcript.text
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_transcript_txt_without_transcript_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)

    response = await client.get(f"/audios/{audio.id}/transcript.txt", cookies=actor.cookies)

    assert response.status_code == 404


async def test_transcript_txt_segment_returns_only_that_segment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _, segments = await actors.make_class_with_timed_segments(db_session, actor)
    target = segments[1]

    response = await client.get(
        f"/audios/{audio.id}/transcript.txt?segment={target.id}", cookies=actor.cookies
    )

    assert response.status_code == 200
    assert response.text == target.text


async def test_transcript_txt_foreign_segment_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _, _ = await actors.make_class_with_timed_segments(db_session, actor)
    stranger = await actors.make_actor(db_session)
    _, _, _, stranger_segments = await actors.make_class_with_timed_segments(
        db_session, stranger
    )

    response = await client.get(
        f"/audios/{audio.id}/transcript.txt?segment={stranger_segments[0].id}",
        cookies=actor.cookies,
    )

    assert response.status_code == 404


async def test_transcript_srt_with_valid_times(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _, _ = await actors.make_class_with_timed_segments(
        db_session, actor, times=((0.0, 5.0), (5.0, 10.0)), duration_seconds=10.0
    )

    response = await client.get(f"/audios/{audio.id}/transcript.srt", cookies=actor.cookies)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-subrip; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text.startswith("1\n00:00:00,000 --> 00:00:05,000\n")
    assert "2\n00:00:05,000 --> 00:00:10,000\n" in response.text


@pytest.mark.parametrize(
    ("precision", "times", "duration"),
    [
        ("none", ((0.0, 5.0),), 10.0),
        ("segment", ((None, None),), 10.0),
        ("segment", ((5.0, 10.0), (0.0, 5.0)), 10.0),
        ("segment", ((0.0, 5.0),), 3.0),
        ("segment", ((0.0, 5.0),), None),
    ],
)
async def test_transcript_srt_unavailable_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    precision: str,
    times: tuple[tuple[float | None, float | None], ...],
    duration: float | None,
) -> None:
    actor = await actors.make_actor(db_session)
    audio, _, _, _ = await actors.make_class_with_timed_segments(
        db_session, actor, precision=precision, times=times, duration_seconds=duration
    )

    response = await client.get(f"/audios/{audio.id}/transcript.srt", cookies=actor.cookies)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "srt_unavailable"


async def test_transcript_srt_without_transcript_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)
    audio = await actors.make_audio(db_session, actor)

    response = await client.get(f"/audios/{audio.id}/transcript.srt", cookies=actor.cookies)

    assert response.status_code == 404


async def test_detail_of_unknown_audio_is_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    actor = await actors.make_actor(db_session)

    response = await client.get(f"/audios/{uuid.uuid4()}", cookies=actor.cookies)

    assert response.status_code == 404
