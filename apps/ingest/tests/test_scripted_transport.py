"""S2.10 and S2.12: one client, cancel, and a heartbeat error that loses the lease."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest

from maulwurf_ingest.asr.riva_client import AsrReupload, NvidiaRivaTranscriptionService
from maulwurf_ingest.asr.scripted import ScriptedTransport
from maulwurf_ingest.lease import Lease
from maulwurf_ingest.supervisor import _LeaseKeeper


def test_one_transport_serves_every_fragment(tmp_path: Path) -> None:
    transport = ScriptedTransport({"text": "hola", "block": False, "words": []}, tmp_path)
    service = NvidiaRivaTranscriptionService(transport)
    first = service.transcribe(b"aa", language_code="es", deadline_s=2)
    second = service.transcribe(b"bb", language_code="es", deadline_s=2)
    assert transport.starts == 2
    assert first.text == "hola"
    assert second.text == "hola"


def test_cancel_unblocks_a_blocked_call(tmp_path: Path) -> None:
    transport = ScriptedTransport({"text": "", "block": True, "words": []}, tmp_path)
    service = NvidiaRivaTranscriptionService(transport)
    done = threading.Event()

    def _call() -> None:
        with pytest.raises(AsrReupload):
            service.transcribe(b"aa", language_code="es", deadline_s=5)
        done.set()

    worker = threading.Thread(target=_call)
    worker.start()
    deadline = time.monotonic() + 2
    while transport.starts == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.starts == 1
    service.cancel_nowait()
    assert done.wait(2)
    worker.join(timeout=1)
    assert not worker.is_alive()


async def test_heartbeat_error_marks_the_lease_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> bool:
        raise asyncpg.InterfaceError("closed")

    monkeypatch.setattr("maulwurf_ingest.supervisor.leases.heartbeat", boom)
    lease = Lease(uuid.uuid4(), uuid.uuid4(), "ingest:test", 1, timedelta(seconds=30))
    keeper = _LeaseKeeper(pool=None, lease=lease, interval_s=0.05)
    await asyncio.wait_for(keeper.lost.wait(), timeout=1)
    await keeper.stop()
