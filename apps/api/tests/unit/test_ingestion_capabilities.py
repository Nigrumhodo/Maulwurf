"""U-S2-SG-01 and S4.4: capabilities publish only measured values.

The size, duration, formats, languages and slots stay hidden while D4 is blocked. The
effective TTLs (10/30/60 min) are published from S4.4's benchmark and carry their own
source, independently of the still-open size/duration decision.
"""
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app
from app.routers import ingestion
from app.schemas.ingestion import (
    EFFECTIVE_TTL_MINUTES,
    TTL_SOURCE_REPORT,
    TTL_SOURCE_VERSION,
    IngestionAttemptTtl,
    IngestionCapabilitiesOut,
)

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
        "ttl_source_report": TTL_SOURCE_REPORT,
        "ttl_source_version": TTL_SOURCE_VERSION,
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
    assert response.json() == {**published, "privacy_notice_version": "2026-09-v1"}


def test_capabilities_without_a_spike_publication_still_publish_the_effective_ttls(
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
        "privacy_notice_version": "2026-09-v1",
        "max_upload_bytes": None,
        "max_duration_seconds": None,
        "accepted_input_formats": [],
        "languages": [],
        "language_policy": "explicit_select_required_no_multi",
        "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
        "ttl_source_report": TTL_SOURCE_REPORT,
        "ttl_source_version": TTL_SOURCE_VERSION,
        "asr": {
            "provider": "nvidia-riva",
            "model": "whisper-large-v3",
            "timestamp_precision": None,
        },
        "active_slots": None,
        "required_stages": ["index"],
    }


def test_ttls_are_published_without_validating_the_size_or_duration_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4 stays blocked: the TTL publication is its own benchmark, not a full F0 approval."""
    payload = {
        "limits_validated": False,
        "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
        "ttl_source_report": TTL_SOURCE_REPORT,
        "ttl_source_version": TTL_SOURCE_VERSION,
    }
    monkeypatch.setenv("MAULWURF_INGESTION_CAPABILITIES", json.dumps(payload))
    configured = _settings()
    monkeypatch.setattr(ingestion, "settings", configured)

    response = client.get("/ingestion/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["limits_validated"] is False
    assert body["max_upload_bytes"] is None
    assert body["max_duration_seconds"] is None
    assert body["active_slots"] is None
    assert body["ttl"] == EFFECTIVE_TTL_MINUTES
    assert body["ttl_source_report"] == TTL_SOURCE_REPORT
    assert body["ttl_source_version"] == TTL_SOURCE_VERSION


def test_incomplete_or_unvalidated_publication_fails_fast() -> None:
    incomplete = _published_capabilities()
    incomplete["ttl"] = {"upload_start_minutes": 10, "receive_minutes": None, "asr_minutes": 60}
    with pytest.raises(ValidationError, match="TTL"):
        _settings(ingestion_capabilities=incomplete)

    unvalidated = _published_capabilities()
    unvalidated["limits_validated"] = False
    with pytest.raises(ValidationError, match="límites nulos y listas vacías"):
        _settings(ingestion_capabilities=unvalidated)


def test_a_ttl_publication_requires_its_benchmark_source() -> None:
    without_source = _published_capabilities()
    without_source["limits_validated"] = False
    without_source["max_upload_bytes"] = None
    without_source["max_duration_seconds"] = None
    without_source["accepted_input_formats"] = []
    without_source["languages"] = []
    without_source["active_slots"] = None
    without_source["ttl_source_report"] = None
    with pytest.raises(ValidationError, match="ttl_source_report"):
        _settings(ingestion_capabilities=without_source)


def test_a_source_without_ttls_fails_fast() -> None:
    without_ttls = _published_capabilities()
    without_ttls["limits_validated"] = False
    without_ttls["max_upload_bytes"] = None
    without_ttls["max_duration_seconds"] = None
    without_ttls["accepted_input_formats"] = []
    without_ttls["languages"] = []
    without_ttls["active_slots"] = None
    without_ttls["ttl"] = {
        "upload_start_minutes": None,
        "receive_minutes": None,
        "asr_minutes": None,
    }
    with pytest.raises(ValidationError, match="ttl_source"):
        _settings(ingestion_capabilities=without_ttls)


def test_public_response_requires_the_notice_version() -> None:
    """El contrato publicado no puede volver a declarar el aviso como opcional."""
    published = IngestionCapabilitiesOut.model_validate(
        {**_published_capabilities(), "privacy_notice_version": "2026-09-v1"}
    )
    with pytest.raises(ValidationError, match="privacy_notice_version"):
        IngestionCapabilitiesOut.model_validate(
            published.model_dump(exclude={"privacy_notice_version"})
        )


def test_the_effective_ttl_constants_match_the_published_defaults() -> None:
    ttl = IngestionAttemptTtl()
    assert ttl.model_dump() == EFFECTIVE_TTL_MINUTES
    assert ttl.has_values()
