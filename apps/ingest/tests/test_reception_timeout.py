"""A slow body must time out and give the admission slot back."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from maulwurf_ingest import admission, cleanup
from maulwurf_ingest.config import Settings, settings
from maulwurf_ingest.credential import CredentialClaims, mint
from maulwurf_ingest.lease import Lease
from maulwurf_ingest.receiver import ReceptionTimeout, receive
from maulwurf_ingest.receptions import (
    _gate,
    active_slot_count,
    put_receive,
    reset_state,
    slots_in_use,
)

_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _hung() -> AsyncIterator[bytes]:
    await asyncio.Event().wait()
    yield b"x"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reset_state()
    admission.reset()
    monkeypatch.setattr(settings, "credential_secret", _HEX)
    monkeypatch.setattr(settings, "credential_kid", "k1")
    monkeypatch.setattr(settings, "metrics_token", None)
    yield
    reset_state()
    admission.reset()


def test_slots_follow_the_environment_and_stay_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_SLOTS", raising=False)
    assert Settings().ingest_slots == 1
    monkeypatch.setenv("INGEST_SLOTS", "30")
    assert Settings().ingest_slots == 30
    monkeypatch.setenv("INGEST_SLOTS", "31")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setattr(settings, "ingest_slots", 30)
    assert active_slot_count() == 30
    monkeypatch.setattr(settings, "ingest_slots", 50)
    assert active_slot_count() == 30
    monkeypatch.setattr(settings, "ingest_slots", 0)
    assert active_slot_count() == 1


async def test_the_gate_reads_the_configured_slot_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ingest_slots", 1)
    first, second = uuid.uuid4(), uuid.uuid4()
    assert await _gate.take(first)
    assert await _gate.take(second) is False
    await _gate.release(first)
    monkeypatch.setattr(settings, "ingest_slots", 2)
    assert await _gate.take(first)
    assert await _gate.take(second)
    # Same attempt cannot double-acquire its own slot.
    assert await _gate.take(first) is False


async def test_a_hung_body_times_out_and_deletes_the_partial_file(tmp_path: Path) -> None:
    dest = tmp_path / "original"
    with pytest.raises(ReceptionTimeout):
        await asyncio.wait_for(
            receive(_hung(), dest, max_bytes=1000, chunk_timeout_s=0.2, wall_timeout_s=1.0),
            timeout=2,
        )
    assert not dest.exists()


async def test_a_fast_body_still_hashes_inside_the_budget(tmp_path: Path) -> None:
    body = b"abc" * 20
    dest = tmp_path / "original"
    result = await receive(
        _chunks(body), dest, max_bytes=10_000, chunk_timeout_s=2.0, wall_timeout_s=2.0,
    )
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert dest.read_bytes() == body


async def test_a_hung_upload_answers_408_and_frees_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, attempt_id, audio_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lease = Lease(attempt_id, user_id, "box", 2, timedelta(seconds=30))
    workdir = tmp_path / "attempt"
    workdir.mkdir()
    monkeypatch.setattr(settings, "reception_chunk_timeout_s", 0.2)
    monkeypatch.setattr(settings, "reception_timeout_s", 1.0)

    async def take_lease(*_args: object, **_kwargs: object) -> Lease:
        return lease

    async def alive(*_args: object, **_kwargs: object) -> bool:
        return True

    async def ok_transition(*_args: object, **_kwargs: object) -> bool:
        return True

    async def no_commit(*_args: object, **_kwargs: object) -> None:
        return None

    async def pool_of(_request: object) -> object:
        return object()

    monkeypatch.setattr("maulwurf_ingest.receptions.leases.acquire_bound", take_lease)
    monkeypatch.setattr("maulwurf_ingest.receptions.leases.transition", ok_transition)
    monkeypatch.setattr("maulwurf_ingest.supervisor.leases.heartbeat", alive)
    monkeypatch.setattr("maulwurf_ingest.supervisor.commit_cleanup", no_commit)
    monkeypatch.setattr("maulwurf_ingest.receptions._service_pool", pool_of)
    monkeypatch.setattr(cleanup, "create_workdir", lambda *_args, **_kwargs: workdir)

    class _Hung:
        def __init__(self) -> None:
            token = mint(
                CredentialClaims(
                    "receive", attempt_id, audio_id, user_id, 2, None, 4_102_444_800,
                ),
                kid="k1",
                secret=bytes.fromhex(_HEX),
            )
            self.headers = {"authorization": "Bearer " + token}
            self.app = SimpleNamespace(state=SimpleNamespace(pool=None, instance_id="box"))

        async def stream(self) -> AsyncIterator[bytes]:
            await asyncio.Event().wait()
            yield b"x"

    response = await asyncio.wait_for(
        put_receive(attempt_id, _Hung()),  # type: ignore[arg-type]
        timeout=3,
    )
    assert response.status_code == 408
    assert b"reception_timeout" in response.body
    assert slots_in_use() == 0
    assert await _gate.take(user_id)
    assert not workdir.exists()
