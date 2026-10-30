"""A2.13 client constants, answer mapping and sink selection (ADR-0008 §0, §4-§5, §8).

The end-to-end contract runs in `tests/integration/test_ingest_reception.py`; these tests pin
the pieces that do not need a socket: the four durations, the envelope-to-catalog mapping and
the choice between the real sink and the 503 sink.
"""

import httpx
import pytest

from app.core.config import settings
from app.services.ingest_sink import (
    CREDENTIAL_TTL_S,
    HOLD_TIMEOUT_S,
    HOP_CONNECT_TIMEOUT_S,
    HOP_IDLE_TIMEOUT_S,
    LEASE_TTL_S,
    IngestReceptionSink,
    _admitted_status,
    _cleanup_status,
    _error_code,
    get_reception_sink,
)
from app.services.uploads import SinkError, UnavailableReceptionSink, map_ingest_code


def _response(
    status: int, body: object | None = None, *, raw: bytes | None = None
) -> httpx.Response:
    if raw is not None:
        return httpx.Response(status, content=raw)
    return httpx.Response(status, json=body)


def test_durations_match_adr_0008_and_stay_within_the_lease() -> None:
    assert (CREDENTIAL_TTL_S, HOLD_TIMEOUT_S, LEASE_TTL_S) == (30, 30, 30)
    assert HOP_CONNECT_TIMEOUT_S == 2.0
    assert HOP_IDLE_TIMEOUT_S == 30.0
    assert CREDENTIAL_TTL_S <= LEASE_TTL_S and HOLD_TIMEOUT_S <= LEASE_TTL_S


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (413, {"error": {"code": "payload_too_large"}}, "payload_too_large"),
        (415, {"error": {"code": "unsupported_format"}}, "unsupported_format"),
        (409, {"error": {"code": "reception_integrity_failed"}}, "reception_integrity_failed"),
        (503, None, "capacity_unavailable"),
        (409, None, "attempt_not_active"),
        (500, None, "internal_error"),
    ],
)
def test_error_code_comes_from_the_body_or_the_status(
    status: int, body: object | None, code: str
) -> None:
    assert _error_code(_response(status, body)) == code


def test_non_json_error_body_falls_back_to_the_status() -> None:
    assert _error_code(_response(503, raw=b"<html>")) == "capacity_unavailable"


def test_ingest_answer_codes_map_to_the_public_catalog() -> None:
    assert map_ingest_code("payload_too_large").status_code == 413
    assert map_ingest_code("unsupported_format").status_code == 415
    assert map_ingest_code("attempt_not_active").status_code == 409
    assert map_ingest_code("capacity_unavailable").status_code == 503
    assert map_ingest_code("reception_integrity_failed").status_code == 500
    assert map_ingest_code("internal_credential_invalid").status_code == 500


def test_cleanup_status_is_read_only_from_a_dict_body() -> None:
    assert _cleanup_status(_response(200, {"cleanup_status": "verified"})) == "verified"
    assert _cleanup_status(_response(200, ["not", "a", "dict"])) is None


def test_admitted_status_requires_a_string() -> None:
    assert _admitted_status(_response(202, {"status": "transcribing"})) == "transcribing"
    with pytest.raises(SinkError):
        _admitted_status(_response(202, {"status": 1}))


def test_sink_is_unavailable_until_both_settings_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ingest_url", None)
    monkeypatch.setattr(settings, "ingest_credential_secret", None)
    assert isinstance(get_reception_sink(), UnavailableReceptionSink)


def test_sink_is_the_ingest_client_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import AnyHttpUrl, SecretStr

    monkeypatch.setattr(settings, "ingest_url", AnyHttpUrl("http://ingest:8000/"))
    monkeypatch.setattr(settings, "ingest_credential_secret", SecretStr("ab" * 32))
    assert isinstance(get_reception_sink(), IngestReceptionSink)
