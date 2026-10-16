"""U-S2-AN-07: the honest progress snapshot and its SSE encoding, without Postgres.

`app/services/progress.py` builds the state the stream emits (ESPECIFICACION.md M2/DISENO
§3.5): real bytes and fragments, no percentage, no ETA, no interpolated value. Pure
functions and dataclasses, no DB, no HTTP; the polling generator and the endpoint are
covered by `tests/integration/test_progress_sse.py`. No `integration` marker.
"""

from app.services import progress


def _snapshot(**overrides: object) -> progress.ProgressSnapshot:
    values: dict[str, object] = {
        "attempt_status": "transcribing",
        "attempt_error_code": None,
        "received_bytes": 104_857_600,
        "fragments_done": 4,
        "fragments_total": None,
        "stages": {"index": "pending", "analyze": "not_requested"},
        "aggregate": "processing",
    }
    values.update(overrides)
    return progress.ProgressSnapshot(**values)  # type: ignore[arg-type]


def test_snapshot_reports_measured_bytes_and_fragments() -> None:
    payload = _snapshot().snapshot_payload()
    assert payload["attempt"]["received_bytes"] == 104_857_600
    assert payload["attempt"]["fragments_done"] == 4
    assert payload["attempt"]["fragments_total"] is None
    assert payload["stages"] == {"index": "pending", "analyze": "not_requested"}
    assert payload["aggregate"] == "processing"


def test_no_payload_invents_a_percentage_or_eta() -> None:
    snapshot = _snapshot()
    for payload in (snapshot.snapshot_payload(), snapshot.progress_payload()):
        assert "percent" not in str(payload).lower()
        assert "eta" not in {key.lower() for key in payload}


def test_progress_payload_only_reports_measured_values() -> None:
    payload = _snapshot().progress_payload()
    assert set(payload) == {"received_bytes", "fragments_done", "fragments_total"}


def test_encode_event_is_a_valid_sse_frame() -> None:
    frame = progress.encode_event(name="snapshot", id=7, data={"a": 1})
    assert frame == 'event: snapshot\nid: 7\ndata: {"a":1}\n\n'


def test_encode_event_keeps_non_ascii_utf8() -> None:
    frame = progress.encode_event(name="progress", id=1, data={"detalle": "ñ"})
    assert "ñ" in frame


def test_heartbeat_is_a_comment_frame() -> None:
    assert progress.heartbeat() == ": ping\n\n"


def test_progress_key_tracks_only_measured_changes() -> None:
    assert _snapshot().progress_key() == _snapshot().progress_key()
    assert _snapshot().progress_key() != _snapshot(received_bytes=1).progress_key()
    assert _snapshot().progress_key() != _snapshot(attempt_status="receiving").progress_key()


def test_terminal_aggregates_are_the_three_final_states() -> None:
    assert progress.TERMINAL_AGGREGATES == ("ready", "partial", "failed")
