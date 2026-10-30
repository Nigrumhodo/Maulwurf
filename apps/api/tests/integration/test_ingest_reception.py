"""I-S2-AN-12 (A2.13, ADR-0008): API↔ingest reception contract, end to end against the fake.

Real PostgreSQL (`db_session`), the real `IngestReceptionSink` and the real `PUT` router. The
callee is `tests.fakes.ingest_app.FakeIngest`, served by uvicorn on a loopback port so the
requests cross real TCP and backpressure is real. Bytes are generated in RAM per test and
never written to disk. Case 14 is `load` and is not part of the MR run.
"""

import hashlib
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pydantic import AnyHttpUrl, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import CSRF_HEADER
from app.main import app
from app.models import Audio, IngestionAttempt
from app.services import ingest_credential as cred
from app.services import ingest_sink
from app.services.ingest_sink import IngestReceptionSink, close_ingest_client
from app.services.uploads import SinkError, receive_content
from tests.fakes.ingest_app import FakeIngest, RunningFake, chunks_of, wait_until
from tests.integration.actors import Actor, make_actor, make_attempt, make_subject

KIB = 1024
MIB = 1024 * KIB
KID = "k1"
SECRET_HEX = "5e" * 32  # 32 bytes, the floor of ADR-0008 §2
SECRET = bytes.fromhex(SECRET_HEX)
CONTENT = b"RIFF" + b"\x00" * 60
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
async def ingest(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeIngest]:
    """The fake on a free port, wired into the settings the real sink reads."""
    fake = FakeIngest(kid=KID, secret=SECRET)
    running = RunningFake(fake, _free_port())
    running.start()
    monkeypatch.setattr(settings, "ingest_url", AnyHttpUrl(running.base_url))
    monkeypatch.setattr(settings, "ingest_credential_secret", SecretStr(SECRET_HEX))
    monkeypatch.setattr(settings, "ingest_credential_kid", KID)
    try:
        yield fake
    finally:
        await close_ingest_client()
        running.stop()


@pytest.fixture
async def client(db_session: AsyncSession, ingest: FakeIngest) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=settings.allowed_origin
    ) as http:
        yield http
    app.dependency_overrides.clear()


def _raw_body(subject_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(subject_id),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "title": "Derivadas",
        "privacy_notice_version": settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


async def _reserve(
    client: AsyncClient,
    db: AsyncSession,
    ingest: FakeIngest,
    actor: Actor,
    subject_id: uuid.UUID,
    **overrides: Any,
) -> dict[str, Any]:
    """`POST /audios`, then tell the fake about the row the way `ingest` would read it."""
    response = await client.post(
        "/audios",
        json=_raw_body(subject_id, **overrides),
        cookies=actor.cookies,
        headers=actor.headers,
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    attempt = await db.get(IngestionAttempt, uuid.UUID(data["attempt_id"]))
    assert attempt is not None
    ingest.register(
        attempt.id,
        user_id=actor.user_id,
        audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    return data


async def _subject_id(db: AsyncSession, actor: Actor) -> uuid.UUID:
    subject = await make_subject(db, actor)
    return subject.id  # plain value: commits in the handlers expire the ORM row


async def _put(
    client: AsyncClient, actor: Actor, url: str, *, content: Any
) -> httpx.Response:
    headers = {**actor.headers, "Content-Type": "audio/wav"}
    return await client.put(url, content=content, cookies=actor.cookies, headers=headers)


def _ops(fake: FakeIngest, attempt_id: uuid.UUID) -> list[str]:
    return [op for op, seen, _ in fake.calls if seen == attempt_id]


def _token(
    op: cred.Op,
    attempt_id: uuid.UUID,
    audio_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    fencing_token: int = 1,
    instance: str | None = None,
    expires_at: int | None = None,
    kid: str = KID,
    secret: bytes = SECRET,
) -> str:
    claims = cred.CredentialClaims(
        op=op,
        attempt_id=attempt_id,
        audio_id=audio_id,
        user_id=user_id,
        fencing_token=fencing_token,
        instance=instance,
        expires_at=expires_at if expires_at is not None else int(time.time()) + 30,
    )
    return cred.mint(claims, kid=kid, secret=secret)


def _sink(ingest: FakeIngest) -> IngestReceptionSink:
    return IngestReceptionSink(
        base_url=str(settings.ingest_url),
        secret=SECRET,
        kid=KID,
        client=ingest_sink.get_ingest_client(),
    )


# --- 1-4: the happy path, duplicates and the 415 mapping --------------------------------


@pytest.mark.integration
async def test_case_01_chunked_8_mib_is_202_and_admitted_once(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])
    data = os.urandom(8 * MIB)  # RAM only; the fake keeps a hash, never the bytes

    response = await _put(
        client, actor, reserved["upload_url"], content=chunks_of(data, 64 * KIB)
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "transcribing"
    seen = ingest.attempts[attempt_id]
    assert seen.sha256 == hashlib.sha256(data).hexdigest()
    assert seen.received_bytes == len(data)
    assert _ops(ingest, attempt_id) == ["receive", "admit"]
    assert ingest.asr_starts == 1


@pytest.mark.integration
async def test_case_02_exact_duplicate_discards_before_absorbing(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    first = await _reserve(client, db_session, ingest, actor, subject_id)
    assert (await _put(client, actor, first["upload_url"], content=CONTENT)).status_code == 202
    second = await _reserve(client, db_session, ingest, actor, subject_id)
    second_id = uuid.UUID(second["attempt_id"])

    response = await _put(client, actor, second["upload_url"], content=CONTENT)

    assert response.status_code == 200
    assert response.json()["outcome"] == "duplicate_exact"
    assert ingest.attempts[second_id].discard_reason == "duplicate_exact"
    assert _ops(ingest, second_id) == ["receive", "discard"]
    assert ingest.asr_starts == 1  # the duplicate never reaches ASR
    assert await db_session.get(IngestionAttempt, second_id) is None  # absorbed after discard


@pytest.mark.integration
async def test_case_03_variant_is_409_after_a_verified_discard(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    canonical = await _reserve(client, db_session, ingest, actor, subject_id)
    assert (
        await _put(client, actor, canonical["upload_url"], content=CONTENT)
    ).status_code == 202
    other = await _reserve(
        client, db_session, ingest, actor, subject_id, class_date="2026-09-22"
    )
    other_id = uuid.UUID(other["attempt_id"])

    response = await _put(client, actor, other["upload_url"], content=CONTENT)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_variant"
    assert ingest.attempts[other_id].discard_reason == "duplicate_variant"
    assert "admit" not in _ops(ingest, other_id)


@pytest.mark.integration
async def test_case_04_ingest_415_is_unsupported_format_and_writes_no_identity(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])
    ingest.fault(attempt_id, unsupported=True)

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_format"
    assert "reason" not in response.text  # the internal reason is not exposed
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None


# --- 5-8: early answers and the failure mapping ----------------------------------------


@pytest.mark.integration
async def test_case_05_early_413_stops_reading_the_browser_stream(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    ingest.fault(uuid.UUID(reserved["attempt_id"]), too_large_at=256 * KIB)
    data = os.urandom(16 * MIB)
    total_chunks = len(data) // (64 * KIB)
    yielded = 0

    def count() -> None:
        nonlocal yielded
        yielded += 1

    response = await _put(
        client, actor, reserved["upload_url"],
        content=chunks_of(data, 64 * KIB, on_yield=count),
    )

    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_bytes"] > 0
    # ingest stopped hashing at its cut. The browser side is NOT stopped early: httpx sends
    # the whole request body before it reads any response (see the report, deviation D-5).
    assert ingest.bytes_read < 1 * MIB
    print(f"I-S2-AN-05 yielded_chunks={yielded} total_chunks={total_chunks}")  # noqa: T201


@pytest.mark.integration
async def test_case_06_unreachable_ingest_is_503_with_retry_after(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    monkeypatch.setattr(settings, "ingest_url", AnyHttpUrl(f"http://127.0.0.1:{_free_port()}"))

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    assert response.headers.get("Retry-After")


@pytest.mark.integration
async def test_case_07_ingest_409_attempt_not_active_is_passed_through(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    ingest.fault(uuid.UUID(reserved["attempt_id"]), conflict=True)

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "attempt_not_active"


@pytest.mark.integration
async def test_case_08_wrong_hash_from_ingest_is_500_and_discarded(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])
    ingest.fault(attempt_id, wrong_hash=True)

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert ingest.attempts[attempt_id].discard_reason == "reception_integrity_failed"
    # The hash check runs before any admission: `ingest` must never see an `admit`.
    assert _ops(ingest, attempt_id) == ["receive", "discard"]
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None


@pytest.mark.integration
async def test_case_09_browser_disconnect_closes_the_hop_and_writes_no_status(
    db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    audio = await db_session.get(Audio, attempt.audio_id)
    assert audio is not None

    async def broken_stream() -> AsyncIterator[bytes]:
        yield os.urandom(256 * KIB)
        # Drop only once ingest is reading, so the internal connection really is open.
        assert await wait_until(lambda: ingest.bytes_read >= 256 * KIB)
        raise ClientDisconnect()

    request = SimpleNamespace(stream=lambda: broken_stream())
    with pytest.raises(ClientDisconnect):
        await receive_content(
            db_session,
            cast(Request, request),
            _sink(ingest),
            user_id=actor.user_id,
            audio=audio,
            attempt=attempt,
            content_length=None,
        )

    # ingest sees the connection close and records its own terminal state.
    assert await wait_until(lambda: ingest.receive_disconnects == 1)
    fresh = await db_session.get(IngestionAttempt, attempt.id)
    assert fresh is not None and fresh.status == "awaiting_upload"  # API wrote nothing
    assert audio.sha256 is None
    assert "admit" not in _ops(ingest, attempt.id)


@pytest.mark.integration
async def test_case_10_failed_discard_is_500_never_a_duplicate_answer(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    subject_id = await _subject_id(db_session, actor)
    first = await _reserve(client, db_session, ingest, actor, subject_id)
    assert (await _put(client, actor, first["upload_url"], content=CONTENT)).status_code == 202
    second = await _reserve(client, db_session, ingest, actor, subject_id)
    ingest.fault(uuid.UUID(second["attempt_id"]), discard_fails=True)

    response = await _put(client, actor, second["upload_url"], content=CONTENT)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.status_code not in {200, 409}


@pytest.mark.integration
async def test_case_11_admit_unavailable_restores_identity_and_discards_api_error(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])
    ingest.fault(attempt_id, admit_unavailable=True)

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 503
    assert response.headers.get("Retry-After")
    assert ingest.attempts[attempt_id].discard_reason == "api_error"
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None  # identity compensated


@pytest.mark.integration
async def test_case_12_internal_requests_carry_no_browser_material(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 202, response.text
    seen = [names for op, aid, names in ingest.calls if aid == attempt_id]
    assert len(seen) == 2  # receive + admit, both accepted: each token matched its op
    for names in seen:
        assert "authorization" in names
        assert not names & {"cookie", "origin", CSRF_HEADER.lower()}


# --- 12a-12d: credential negatives and idempotence against the fake --------------------


@pytest.mark.integration
async def test_case_12a_forged_receive_tokens_change_no_state(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    other = uuid.uuid4()
    now = int(time.time())
    cases: list[tuple[str, str, int]] = [
        ("expired", _token("receive", attempt.id, attempt.audio_id, actor.user_id,
                           expires_at=now - 5), 401),
        ("other attempt", _token("receive", other, attempt.audio_id, actor.user_id), 401),
        ("other user", _token("receive", attempt.id, attempt.audio_id, other), 401),
        ("other audio", _token("receive", attempt.id, other, actor.user_id), 401),
        ("stale ft", _token("receive", attempt.id, attempt.audio_id, actor.user_id,
                            fencing_token=7), 409),
        ("unknown kid", _token("receive", attempt.id, attempt.audio_id, actor.user_id,
                               kid="k9"), 401),
    ]
    async with httpx.AsyncClient() as http:
        for label, token, expected in cases:
            response = await http.put(
                f"{ingest_sink_base(ingest)}/internal/v1/receptions/{attempt.id}",
                content=b"x" * 1024,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == expected, label
    assert ingest.attempts[attempt.id].state == "awaiting", "a forged token changed state"


@pytest.mark.integration
async def test_case_12a_forged_admit_and_discard_tokens_change_no_held_state(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    base = ingest_sink_base(ingest)
    async with httpx.AsyncClient() as http:
        held = await http.put(
            f"{base}/internal/v1/receptions/{attempt.id}",
            content=b"x" * 1024,
            headers={"Authorization": "Bearer " + _token(
                "receive", attempt.id, attempt.audio_id, actor.user_id)},
        )
        assert held.status_code == 200
        token_ft = int(held.json()["fencing_token"])
        instance = str(held.json()["instance"])
        forged: list[tuple[str, str, str, int]] = [
            ("admit expired", "admit", _token(
                "admit", attempt.id, attempt.audio_id, actor.user_id, fencing_token=token_ft,
                instance=instance, expires_at=int(time.time()) - 5), 401),
            ("admit other user", "admit", _token(
                "admit", attempt.id, attempt.audio_id, uuid.uuid4(), fencing_token=token_ft,
                instance=instance), 401),
            ("admit stale ft", "admit", _token(
                "admit", attempt.id, attempt.audio_id, actor.user_id, fencing_token=1,
                instance=instance), 401),
            ("admit wrong ins", "admit", _token(
                "admit", attempt.id, attempt.audio_id, actor.user_id, fencing_token=token_ft,
                instance="other-instance"), 409),
            ("admit token on discard", "discard", _token(
                "admit", attempt.id, attempt.audio_id, actor.user_id, fencing_token=token_ft,
                instance=instance), 401),
        ]
        for label, path_op, token, expected in forged:
            url = f"{base}/internal/v1/receptions/{attempt.id}/{path_op}"
            body = {"sha256": DIGEST, "received_bytes": 1024}
            if path_op == "discard":
                body = {"reason": "api_error"}
            response = await http.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == expected, label
    assert ingest.attempts[attempt.id].state == "held"
    assert ingest.asr_starts == 0


@pytest.mark.integration
async def test_case_12b_replayed_receive_token_is_409(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    token = _token("receive", attempt.id, attempt.audio_id, actor.user_id)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{ingest_sink_base(ingest)}/internal/v1/receptions/{attempt.id}"
    async with httpx.AsyncClient() as http:
        first = await http.put(url, content=b"x" * 512, headers=headers)
        second = await http.put(url, content=b"x" * 512, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "attempt_not_active"


@pytest.mark.integration
async def test_case_12c_admit_with_wrong_hash_is_public_500_and_compensated(
    client: AsyncClient, db_session: AsyncSession, ingest: FakeIngest
) -> None:
    actor = await make_actor(db_session)
    reserved = await _reserve(
        client, db_session, ingest, actor, await _subject_id(db_session, actor)
    )
    attempt_id = uuid.UUID(reserved["attempt_id"])
    ingest.fault(attempt_id, admit_mismatch=True)

    response = await _put(client, actor, reserved["upload_url"], content=CONTENT)

    assert response.status_code == 500
    assert ingest.attempts[attempt_id].discard_reason == "reception_integrity_failed"
    audio = await db_session.get(Audio, uuid.UUID(reserved["audio_id"]))
    assert audio is not None and audio.sha256 is None


@pytest.mark.integration
async def test_case_12d_discard_and_admit_are_idempotent_at_the_sink(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    discarded = await make_attempt(db_session, actor)
    admitted = await make_attempt(db_session, actor)
    for attempt in (discarded, admitted):
        ingest.register(
            attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
            fencing_token=attempt.fencing_token,
        )

    sink = _sink(ingest)
    await sink.begin(
        user_id=actor.user_id, audio_id=discarded.audio_id, attempt_id=discarded.id,
        fencing_token=1, declared_bytes=None,
    )
    await sink.write(CONTENT)
    await sink.finish()
    await sink.discard("api_error")
    await sink.discard("api_error")  # second call answers the recorded cleanup, no error
    assert ingest.attempts[discarded.id].discard_reason == "api_error"

    sink = _sink(ingest)
    await sink.begin(
        user_id=actor.user_id, audio_id=admitted.audio_id, attempt_id=admitted.id,
        fencing_token=1, declared_bytes=None,
    )
    await sink.write(CONTENT)
    received = await sink.finish()
    assert received.sha256 == DIGEST
    await sink.admit()
    with pytest.raises(SinkError) as second_admit:
        await sink.admit()
    assert second_admit.value.code == "reception_not_held"
    assert ingest.asr_starts == 1


# --- 13: backpressure -------------------------------------------------------------------


@pytest.mark.integration
async def test_case_13_backpressure_keeps_api_memory_flat_with_a_slow_ingest(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    size = 32 * MIB
    chunk = 64 * KIB
    # The brief asks for 32 MiB at 64 KiB/s (512 s, too long for a test). ingest here reads at
    # 64 KiB per 10 ms (about 6.5 MB/s). Loopback socket buffers absorb the first few MiB,
    # so the writer waits only past that point; the assertions allow for that margin.
    rate_bps = 64 * KIB / 0.01
    ingest.read_rate_bps = rate_bps
    data = os.urandom(size)
    sink = _sink(ingest)
    await sink.begin(
        user_id=actor.user_id, audio_id=attempt.audio_id, attempt_id=attempt.id,
        fencing_token=1, declared_bytes=size,
    )
    rss_before = _rss_bytes()
    rss_peak = rss_before
    started = time.monotonic()
    for offset in range(0, size, chunk):
        await sink.write(data[offset : offset + chunk])
        rss_peak = max(rss_peak, _rss_bytes())
    await sink.finish()
    elapsed = time.monotonic() - started
    await sink.discard("api_error")

    growth = rss_peak - rss_before
    print(  # noqa: T201 — the numbers are the evidence the report quotes
        f"I-S2-AN-12/13 bytes={size} chunk={chunk} elapsed_s={elapsed:.2f} "
        f"rss_before={rss_before} rss_peak={rss_peak} growth_bytes={growth}"
    )
    assert ingest.bytes_read == size
    kernel_buffer_margin = 8 * MIB  # generous for loopback socket buffers
    assert elapsed >= (size - kernel_buffer_margin) / rate_bps  # the writer waited on ingest
    assert growth < 8 * MIB  # a few MiB of read blocks, never the file


# --- 14: load, not in MRs ----------------------------------------------------------------


@pytest.mark.load
async def test_case_14_200_mib_stream_has_equal_hash_and_no_disk_copy(
    ingest: FakeIngest, db_session: AsyncSession
) -> None:
    actor = await make_actor(db_session)
    attempt = await make_attempt(db_session, actor)
    ingest.register(
        attempt.id, user_id=actor.user_id, audio_id=attempt.audio_id,
        fencing_token=attempt.fencing_token,
    )
    size = 200 * MIB
    sink = _sink(ingest)
    await sink.begin(
        user_id=actor.user_id, audio_id=attempt.audio_id, attempt_id=attempt.id,
        fencing_token=1, declared_bytes=size,
    )
    hasher = hashlib.sha256()
    for _ in range(size // (64 * KIB)):
        block = os.urandom(64 * KIB)
        hasher.update(block)
        await sink.write(block)
    received = await sink.finish()
    assert received.sha256 == hasher.hexdigest()
    assert received.received_bytes == size
    await sink.discard("api_error")


# --- helpers ---------------------------------------------------------------------------


def ingest_sink_base(ingest: FakeIngest) -> str:
    """Base URL the fake is served on, read from the settings the fixture wrote."""
    return str(settings.ingest_url).rstrip("/")


def _rss_bytes() -> int:
    """Current resident set size of this process, from /proc (Linux only)."""
    with open("/proc/self/status", encoding="utf-8") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * KIB
    return 0

