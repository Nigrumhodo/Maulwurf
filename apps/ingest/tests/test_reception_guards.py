"""The reception error paths must not keep the audio or the slot."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from maulwurf_ingest import cleanup
from maulwurf_ingest.config import settings
from maulwurf_ingest.credential import CredentialClaims, mint
from maulwurf_ingest.lease import Lease
from maulwurf_ingest.receptions import (
    _finished,
    _gate,
    _holds,
    _open_hold,
    _remember,
    _service_pool,
    open_service_pool,
    post_admit,
    post_discard,
    put_receive,
    reset_state,
)
from maulwurf_ingest.supervisor import RunResult, _LeaseKeeper

_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


class _Request:
    def __init__(self, headers: dict[str, str], *, body: bytes = b"abc", payload: object = None):
        self.headers = headers
        self.app = SimpleNamespace(state=SimpleNamespace(pool=None, instance_id="box"))
        self._body = body
        self._payload = payload

    async def stream(self) -> AsyncIterator[bytes]:
        yield self._body

    async def json(self) -> object:
        return self._payload


def _claims(user_id: uuid.UUID, audio_id: uuid.UUID, attempt_id: uuid.UUID) -> CredentialClaims:
    return CredentialClaims("receive", attempt_id, audio_id, user_id, 1, None, 4_102_444_800)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_state()
    monkeypatch.setattr(settings, "credential_secret", _HEX)
    monkeypatch.setattr(settings, "credential_kid", "k1")
    yield
    reset_state()


async def test_acquire_failure_releases_the_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    claims = _claims(user_id, uuid.uuid4(), uuid.uuid4())

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("db")

    monkeypatch.setattr("maulwurf_ingest.receptions.leases.acquire_bound", boom)
    assert await _gate.take(user_id)
    with pytest.raises(RuntimeError, match="db"):
        await _open_hold(object(), claims=claims, instance="box")
    assert await _gate.take(user_id)


async def test_discard_without_a_pool_keeps_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, attempt_id, audio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lease = Lease(attempt_id, user_id, "box", 2, timedelta(seconds=30))
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    (workdir / "original").write_bytes(b"audio")

    async def alive(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr("maulwurf_ingest.supervisor.leases.heartbeat", alive)
    keeper = _LeaseKeeper(object(), lease, 30.0)
    from maulwurf_ingest.receptions import _Hold

    stored = _Hold(
        lease=lease, workdir=workdir, keeper=keeper, sha256="ab" * 32,
        received_bytes=5, duration_s=1.0, audio_id=audio_id,
    )
    _holds[attempt_id] = stored
    assert await _gate.take(user_id)

    async def no_pool(_request: _Request) -> None:
        return None

    monkeypatch.setattr("maulwurf_ingest.receptions._service_pool", no_pool)
    token = mint(
        CredentialClaims("discard", attempt_id, audio_id, user_id, 2, "box", 4_102_444_800),
        kid="k1", secret=bytes.fromhex(_HEX),
    )
    try:
        response = await post_discard(
            attempt_id,
            _Request(  # type: ignore[arg-type]
                {"authorization": "Bearer " + token},
                payload={"reason": "client_disconnected"},
            ),
        )
        assert response.status_code == 503
        assert stored.phase == "holding"
        assert stored.finished.is_set() is False
        assert (workdir / "original").is_file()
        assert await _gate.take(user_id) is False
    finally:
        await keeper.stop()


async def test_admit_without_a_pool_keeps_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, attempt_id, audio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lease = Lease(attempt_id, user_id, "box", 2, timedelta(seconds=30))
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    (workdir / "original").write_bytes(b"audio")

    async def alive(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr("maulwurf_ingest.supervisor.leases.heartbeat", alive)
    keeper = _LeaseKeeper(object(), lease, 30.0)
    from maulwurf_ingest.receptions import _Hold

    stored = _Hold(
        lease=lease, workdir=workdir, keeper=keeper, sha256="ab" * 32,
        received_bytes=5, duration_s=1.0, audio_id=audio_id,
    )
    _holds[attempt_id] = stored
    assert await _gate.take(user_id)

    async def no_pool(_request: _Request) -> None:
        return None

    monkeypatch.setattr("maulwurf_ingest.receptions._service_pool", no_pool)
    token = mint(
        CredentialClaims("admit", attempt_id, audio_id, user_id, 2, "box", 4_102_444_800),
        kid="k1", secret=bytes.fromhex(_HEX),
    )
    try:
        response = await post_admit(
            attempt_id,
            _Request(  # type: ignore[arg-type]
                {"authorization": "Bearer " + token},
                payload={"sha256": "ab" * 32, "received_bytes": 5},
            ),
        )
        assert response.status_code == 503
        assert stored.phase == "holding"
        assert stored.finished.is_set() is False
        assert (workdir / "original").is_file()
    finally:
        await keeper.stop()


async def test_probe_cancel_deletes_the_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, attempt_id, audio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lease = Lease(attempt_id, user_id, "box", 2, timedelta(seconds=30))
    closed: list[Path] = []

    async def take_lease(*_args: object, **_kwargs: object) -> Lease:
        return lease

    async def alive(*_args: object, **_kwargs: object) -> bool:
        return True

    async def cancel_probe(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError()

    async def close_attempt(
        _pool: object, _lease: Lease, _attempt_id: uuid.UUID, workdir: Path,
        _job: object, keeper: _LeaseKeeper, result: RunResult, **_kwargs: object,
    ) -> None:
        closed.append(workdir)
        await keeper.stop()
        workdir.joinpath("original").unlink(missing_ok=True)
        result.evidence = {"verified": True}

    monkeypatch.setattr("maulwurf_ingest.receptions.leases.acquire_bound", take_lease)
    monkeypatch.setattr("maulwurf_ingest.supervisor.leases.heartbeat", alive)
    monkeypatch.setattr("maulwurf_ingest.receptions.asyncio.to_thread", cancel_probe)
    monkeypatch.setattr("maulwurf_ingest.receptions._close_attempt", close_attempt)
    monkeypatch.setattr(cleanup, "create_workdir", lambda _name, _tmp=None: tmp_path)

    async def pool_of(_request: _Request) -> object:
        return object()

    async def ok_transition(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr("maulwurf_ingest.receptions._service_pool", pool_of)
    monkeypatch.setattr("maulwurf_ingest.receptions.leases.transition", ok_transition)
    token = mint(_claims(user_id, audio_id, attempt_id), kid="k1", secret=bytes.fromhex(_HEX))
    with pytest.raises(asyncio.CancelledError):
        await put_receive(
            attempt_id,
            _Request({"authorization": "Bearer " + token}),  # type: ignore[arg-type]
        )
    assert closed
    assert not (tmp_path / "original").exists()
    assert await _gate.take(user_id)


def test_finished_map_drops_the_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("maulwurf_ingest.receptions._FINISHED_MAX", 2)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _remember(first, "verified")
    _remember(second, "verified")
    _remember(third, "failed")
    assert len(_finished) == 2
    assert first not in _finished
    assert list(_finished) == [second, third]


async def test_requests_do_not_open_a_second_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    created = 0

    async def slow_create(*_args: object, **_kwargs: object) -> object:
        nonlocal created
        created += 1
        await asyncio.sleep(0.05)
        return object()

    monkeypatch.setattr("asyncpg.create_pool", slow_create)
    monkeypatch.setattr(settings, "database_url", "postgresql://127.0.0.1/maulwurf")
    app = SimpleNamespace(state=SimpleNamespace(pool=None))
    await open_service_pool(app)
    request = SimpleNamespace(app=app)
    await asyncio.gather(
        _service_pool(request),  # type: ignore[arg-type]
        _service_pool(request),  # type: ignore[arg-type]
    )
    assert created == 1
