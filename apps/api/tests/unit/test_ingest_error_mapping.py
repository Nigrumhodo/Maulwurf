"""Public mapping of ingest answers (S2.13, ADR-0008 §8)."""

from __future__ import annotations

from app.services.uploads import SinkError, map_ingest_code


def test_known_sink_codes_keep_their_public_answers() -> None:
    unsupported = map_ingest_code("unsupported_format")
    assert (unsupported.status_code, unsupported.code) == (415, "unsupported_format")
    capacity = map_ingest_code("capacity_unavailable")
    assert (capacity.status_code, capacity.code) == (503, "capacity_unavailable")
    assert capacity.details["retryable"] is False
    assert capacity.headers["Retry-After"]
    assert map_ingest_code("malformed").code == "unsupported_format"
    assert map_ingest_code("payload_too_large").status_code == 413
    assert map_ingest_code("probe_unavailable").code == "capacity_unavailable"
    assert map_ingest_code("wrong_instance").code == "capacity_unavailable"
    assert map_ingest_code("reception_not_held").code == "capacity_unavailable"
    integrity = map_ingest_code("reception_integrity_failed")
    assert (integrity.status_code, integrity.code) == (500, "internal_error")
    hidden = map_ingest_code("internal_credential_invalid")
    assert (hidden.status_code, hidden.code) == (500, "internal_error")
    assert hidden.code != "internal_credential_invalid"
    active = map_ingest_code("attempt_not_active")
    assert (active.status_code, active.code) == (409, "attempt_not_active")
    unknown = map_ingest_code("not_a_real_code")
    assert (unknown.status_code, unknown.code) == (500, "internal_error")
    assert map_ingest_code(SinkError("capacity_unavailable").code).status_code == 503
