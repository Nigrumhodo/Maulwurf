"""A1.1 / J1.5: sondas de proceso de la API, sin dependencias reales."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import health

client = TestClient(app)


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("dependency down")


def test_healthz_is_ok_without_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {"postgres": _down, "redis": _down})

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_is_ready_when_all_dependencies_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {"postgres": _ok, "redis": _ok})

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.parametrize("down", ["postgres", "redis"])
def test_readyz_is_503_when_a_dependency_fails(
    down: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = {"postgres": _ok, "redis": _ok, down: _down}
    monkeypatch.setattr(health, "CHECKS", checks)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "failed": [down]}


def test_readyz_treats_a_slow_dependency_as_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _hangs() -> None:
        import asyncio

        await asyncio.sleep(10)

    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(health, "CHECKS", {"postgres": _ok, "redis": _hangs})

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["failed"] == ["redis"]
