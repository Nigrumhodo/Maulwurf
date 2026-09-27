"""S1.B1: el arranque falla cerrado sin endurecimiento y /readyz refleja cada comprobación."""
import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from maulwurf_ingest import hardening, main
from maulwurf_ingest.config import settings


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise OSError("postgresql://usuario:CANARIO-9d1e@db/x")


def _no_failures(_: hardening.ProcessFacts) -> list[str]:
    return []


DATABASE_URL = "postgresql+asyncpg://maulwurf:x@db/maulwurf"


def test_startup_refuses_to_serve_when_not_hardened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardening, "check", lambda _: ["running_as_root"])
    monkeypatch.setattr(settings, "ingest_hardening", "enforce")
    with pytest.raises(main.HardeningError, match="running_as_root"):
        with TestClient(main.app):
            pass


def test_report_mode_starts_but_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardening, "check", lambda _: ["swap_allowed"])
    monkeypatch.setattr(settings, "ingest_hardening", "report")
    checks: dict[str, Callable[[], Awaitable[None]]] = {
        "hardening": main.check_hardening, "tmpfs": _ok, "postgres": _ok,
    }
    monkeypatch.setattr(main, "CHECKS", checks)
    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": ["hardening"]}


def test_enforce_refuses_to_start_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardening, "check", _no_failures)
    monkeypatch.setattr(settings, "ingest_hardening", "enforce")
    monkeypatch.setattr(settings, "database_url", None)
    with pytest.raises(main.HardeningError, match="MAULWURF_DATABASE_URL"):
        with TestClient(main.app):
            pass


def test_report_mode_starts_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # En report el DSN es opcional: /readyz lo reporta, el proceso no muere.
    monkeypatch.setattr(hardening, "check", _no_failures)
    monkeypatch.setattr(settings, "ingest_hardening", "report")
    monkeypatch.setattr(settings, "database_url", None)
    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code == 200


def test_readyz_ready_when_every_check_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardening, "check", _no_failures)
    monkeypatch.setattr(settings, "database_url", DATABASE_URL)
    monkeypatch.setattr(main, "CHECKS", {"hardening": _ok, "tmpfs": _ok, "postgres": _ok})
    with TestClient(main.app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_logs_only_the_error_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hardening, "check", _no_failures)
    monkeypatch.setattr(settings, "database_url", DATABASE_URL)
    monkeypatch.setattr(main, "CHECKS", {"hardening": _ok, "tmpfs": _ok, "postgres": _boom})
    with TestClient(main.app) as client:
        response = client.get("/readyz")
    assert response.json() == {"status": "not_ready", "failed": ["postgres"]}
    assert "readyz.postgres_failed error=OSError" in caplog.text
    assert "CANARIO-9d1e" not in caplog.text


async def test_tmpfs_check_writes_nothing_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    await main.check_tmpfs()
    assert list(tmp_path.iterdir()) == []


async def test_postgres_check_requires_a_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "database_url", None)
    with pytest.raises(RuntimeError):
        await main.check_postgres()


async def test_postgres_check_bounds_concurrent_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = peak = 0
    gate = asyncio.Lock()

    class FakeConn:
        async def execute(self, _sql: str) -> str:
            return "1"

        async def close(self) -> None:
            return None

    async def fake_connect(_dsn: str, timeout: float) -> FakeConn:
        nonlocal active, peak
        async with gate:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with gate:
            active -= 1
        return FakeConn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    monkeypatch.setattr(settings, "database_url", DATABASE_URL)
    await asyncio.gather(*(main.check_postgres() for _ in range(6)))
    assert peak == 2  # acotado y sin serializar de más
