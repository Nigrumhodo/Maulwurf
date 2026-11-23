"""G3 review: frozen migration states and the shielded abort of a cancelled upload."""

import asyncio
import importlib.util
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from app.models.ingestion import ACTIVE_INGESTION_STATES, EXECUTING_INGESTION_STATES
from app.services import dedupe, uploads

MIGRATION_0024 = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0024_s2_global_attempt_count.py"
)
MIGRATION_0025 = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0025_s2_executing_attempt_count.py"
)


def _load_migration(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capacity_function_counts_exactly_the_active_states() -> None:
    migration = _load_migration(MIGRATION_0024, "migration_0024")
    assert migration.ACTIVE_STATES == ACTIVE_INGESTION_STATES


def test_executing_capacity_migration_excludes_idle_reservations() -> None:
    migration = _load_migration(MIGRATION_0025, "migration_0025")
    assert migration.EXECUTING_STATES == EXECUTING_INGESTION_STATES
    assert "awaiting_upload" not in EXECUTING_INGESTION_STATES
    assert set(EXECUTING_INGESTION_STATES) < set(ACTIVE_INGESTION_STATES)


class _Sink:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.abort_started = asyncio.Event()
        self.release_abort = asyncio.Event()

    async def begin(self, **_: Any) -> None:
        self.events.append("begin")

    async def write(self, chunk: bytes) -> None:
        self.events.append("write")

    async def abort(self) -> None:
        self.abort_started.set()
        await self.release_abort.wait()
        self.events.append("abort")


class _Request:
    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    async def stream(self) -> AsyncIterator[bytes]:
        yield b"RIFF"
        self._started.set()
        await asyncio.Event().wait()  # the client stalls until the request is cancelled
        yield b""  # pragma: no cover


class _Db:
    async def commit(self) -> None:
        return None


class _Row:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


async def test_cancelled_upload_still_aborts_the_ingest_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = _Sink()
    streaming = asyncio.Event()
    monkeypatch.setattr(uploads, "_commit_keeping_instances", lambda db: _Db().commit())
    monkeypatch.setattr(dedupe, "identity_of", lambda audio: None)
    audio = _Row(id=uuid.uuid4())
    attempt = _Row(id=uuid.uuid4(), fencing_token=1)

    task = asyncio.create_task(
        uploads.receive_content(
            cast(Any, _Db()),
            cast(Any, _Request(streaming)),
            cast(Any, sink),
            user_id=uuid.uuid4(),
            audio=cast(Any, audio),
            attempt=cast(Any, attempt),
            content_length=None,
        )
    )
    await streaming.wait()
    task.cancel()
    await asyncio.wait_for(sink.abort_started.wait(), timeout=5)
    # A second cancellation while the abort runs must not cut it short.
    task.cancel()
    sink.release_abort.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert sink.events == ["begin", "write", "abort"]
