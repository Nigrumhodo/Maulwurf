"""The metrics scrape is not anonymous once a bearer is configured."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_a_configured_metrics_token_rejects_an_anonymous_scrape(monkeypatch) -> None:
    monkeypatch.setattr(settings, "env", "local")
    monkeypatch.setattr(settings, "metrics_token", SecretStr("m" * 32))

    response = client.get("/internal/metrics")

    assert response.status_code == 401


def test_a_configured_metrics_token_rejects_the_wrong_bearer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "env", "local")
    monkeypatch.setattr(settings, "metrics_token", SecretStr("m" * 32))

    response = client.get(
        "/internal/metrics", headers={"Authorization": "Bearer " + "n" * 32}
    )

    assert response.status_code == 401
