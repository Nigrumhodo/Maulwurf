"""U-S2-SG-01: capabilities refleja únicamente límites publicados por el spike."""
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app
from app.routers import ingestion

_SECRETS: dict[str, Any] = {
    "secret_key": "test-secret",
    "encryption_key": "test-encryption",
    "oauth_state_secret": "test-oauth-state",
}

client = TestClient(app)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**_SECRETS, **overrides})


def _published_capabilities() -> dict[str, Any]:
    return {
        "source_report": "docs/spike/F0.1-informe-riva.md",
        "source_version": "approved-2026-10-05",
        "limits_validated": True,
        "max_upload_bytes": 960044,
        "max_duration_seconds": 30,
        "accepted_input_formats": ["wav"],
        "languages": ["es"],
        "language_policy": "explicit_select_required_no_multi",
        "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
        "asr": {
            "provider": "nvidia-riva",
            "model": "whisper-large-v3",
            "timestamp_precision": "none",
        },
        "active_slots": 1,
        "required_stages": ["index"],
    }


def test_capabilities_reflects_the_published_spike_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _published_capabilities()
    monkeypatch.setenv("MAULWURF_INGESTION_CAPABILITIES", json.dumps(published))
    configured = _settings()
    monkeypatch.setattr(ingestion, "settings", configured)

    response = client.get("/ingestion/capabilities")

    assert response.status_code == 200
    assert response.json() == published


def test_capabilities_without_publication_hide_provisional_s1_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = _settings(upload_max_bytes=209_715_200, upload_ttl_minutes=15)
    monkeypatch.setattr(ingestion, "settings", configured)

    response = client.get("/ingestion/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "source_report": None,
        "source_version": None,
        "limits_validated": False,
        "max_upload_bytes": None,
        "max_duration_seconds": None,
        "accepted_input_formats": [],
        "languages": [],
        "language_policy": "explicit_select_required_no_multi",
        "ttl": {"upload_start_minutes": None, "receive_minutes": None, "asr_minutes": None},
        "asr": {
            "provider": "nvidia-riva",
            "model": "whisper-large-v3",
            "timestamp_precision": None,
        },
        "active_slots": None,
        "required_stages": ["index"],
    }


def test_incomplete_or_unvalidated_publication_fails_fast() -> None:
    incomplete = _published_capabilities()
    incomplete["ttl"] = {"upload_start_minutes": 10, "receive_minutes": None, "asr_minutes": 60}
    with pytest.raises(ValidationError, match="todos los límites efectivos"):
        _settings(ingestion_capabilities=incomplete)

    unvalidated = _published_capabilities()
    unvalidated["limits_validated"] = False
    with pytest.raises(ValidationError, match="límites nulos y listas vacías"):
        _settings(ingestion_capabilities=unvalidated)
