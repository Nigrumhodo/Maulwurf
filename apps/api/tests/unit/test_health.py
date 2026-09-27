"""A1.1: sondas de proceso de la API."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_is_ok_without_dependencies() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_is_exposed() -> None:
    assert client.get("/readyz").status_code == 200
