"""Ingest observability (J2.5): JSON logs, redaction, the `cleanup.failed` alert and metrics.

No PostgreSQL or Redis: the alert is tested with a hand-built `CleanupEvidence`, and the
metrics with a temporary directory owned by the test.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maulwurf_ingest import admission, cleanup, metrics
from maulwurf_ingest.logs import (
    JsonFormatter,
    RedactionFilter,
    correlation,
    is_sensitive_key,
    scrub_text,
)
from maulwurf_ingest.prometheus_text import Sample, render
from maulwurf_ingest.request_context import RequestIdMiddleware, resolve_request_id

SECRET = "sk-live-7f3a9c2e51b84d6a"  # noqa: S105 - seeded fake value for the redaction test
TRANSCRIPT = "el examen de cálculo es el lunes doce de noviembre"


def _capture(name: str, *, redact: bool = True) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    if redact:
        handler.addFilter(RedactionFilter())
    logger = logging.getLogger(f"test.ingest.{name}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


@pytest.fixture(autouse=True)
def _clean_process_state() -> None:
    metrics.reset()
    admission.reset()


def _capture_cleanup_alerts() -> io.StringIO:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    cleanup_logger = logging.getLogger(cleanup.__name__)
    cleanup_logger.handlers[:] = [handler]
    cleanup_logger.propagate = False
    cleanup_logger.setLevel(logging.DEBUG)
    return stream


def _sample_value(samples: list[Sample], name: str) -> float:
    (sample,) = [s for s in samples if s.name == name]
    return float(sample.value)


def test_cleanup_failed_alert_is_error_level_with_counters_and_attempt_id(
    tmp_path: Path,
) -> None:
    attempt = uuid.uuid4()
    evidence = cleanup.CleanupEvidence(
        workdir_absent=True,
        tmp_is_tmpfs=True,
        live_processes=2,
        open_descriptors=1,
        mounts_under_workdir=0,
        unreadable_processes=0,
    )
    stream = _capture_cleanup_alerts()

    cleanup.log_cleanup_failed(str(attempt), evidence)

    (line,) = [json.loads(raw) for raw in stream.getvalue().splitlines()]
    assert line["level"] == "ERROR"
    assert line["msg"] == f"cleanup.failed attempt_id={attempt}"
    assert line["attempt_id"] == str(attempt)
    assert line["live_processes"] == 2
    assert line["open_descriptors"] == 1
    assert line["verified"] is False
    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True
    )
    assert _sample_value(samples, "maulwurf_ingest_cleanup_failed_total") == 1


def test_cleanup_failed_alert_on_exception_records_only_the_error_type() -> None:
    stream = _capture_cleanup_alerts()

    cleanup.log_cleanup_failed(str(uuid.uuid4()), None, error_type="PermissionError")

    (line,) = [json.loads(raw) for raw in stream.getvalue().splitlines()]
    assert line["error"] == "PermissionError"
    assert line["verified"] is False


def test_admission_latch_closes_on_failed_cleanup_and_is_reported() -> None:
    attempt = uuid.uuid4()
    assert admission.uploads_admitted() is True

    admission.halt(attempt)

    assert admission.uploads_admitted() is False
    assert admission.alerts() == (admission.Alert("cleanup_failed", attempt),)


def test_json_line_carries_bound_correlation_ids() -> None:
    logger, stream = _capture("ids")
    with correlation(attempt_id="att-9", run_id="run-2"):
        logger.info("asr.started fragments=%s", 3)

    (line,) = [json.loads(raw) for raw in stream.getvalue().splitlines()]
    assert line["attempt_id"] == "att-9"
    assert line["run_id"] == "run-2"
    assert line["msg"] == "asr.started fragments=3"


def test_redaction_masks_seeded_secret_and_transcript_before_output() -> None:
    logger, stream = _capture("redaction")
    logger.warning(
        "asr.fragment api_key=%s",
        SECRET,
        extra={"fields": {"transcript": TRANSCRIPT, "fragments": 4}},
    )

    output = stream.getvalue()
    assert SECRET not in output
    assert TRANSCRIPT not in output
    assert '"fragments": 4' in output


def test_redaction_keeps_non_sensitive_keys() -> None:
    assert scrub_text("cleanup.failed attempt_id=abc transcript_version=2") == (
        "cleanup.failed attempt_id=abc transcript_version=2"
    )
    assert is_sensitive_key("prompt") is True
    assert is_sensitive_key("context") is False


def test_without_the_filter_the_transcript_reaches_the_output() -> None:
    """Negative control: without the guard the transcript leaks; the filter protects it."""
    logger, stream = _capture("unfiltered", redact=False)
    logger.warning("asr.result text=%s", TRANSCRIPT)
    assert TRANSCRIPT in stream.getvalue()


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_request_id_is_echoed_when_valid_and_regenerated_when_hostile() -> None:
    client = TestClient(_app())
    kept = client.get("/ping", headers={"X-Request-ID": "ingest-req-0001"})
    replaced = client.get("/ping", headers={"X-Request-ID": "x\"}{\"injected\":1"})
    assert kept.headers["x-request-id"] == "ingest-req-0001"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["x-request-id"])


def test_request_id_rejects_short_and_overlong_values() -> None:
    assert re.fullmatch(r"[0-9a-f]{32}", resolve_request_id("short"))
    assert re.fullmatch(r"[0-9a-f]{32}", resolve_request_id("a" * 65))


def test_metrics_expose_resources_without_temporary_directory_names(tmp_path: Path) -> None:
    attempt_dir = tmp_path / f"attempt-{uuid.uuid4()}-abcd"
    attempt_dir.mkdir()
    old = time.time() - 300
    os.utime(attempt_dir, (old, old))

    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=1, slots_total=1, admission_open=False,
        now=time.time(),
    )
    text = render(samples)

    assert "maulwurf_ingest_admission_open 0" in text
    assert "maulwurf_ingest_slots_in_use 1" in text
    assert "maulwurf_ingest_tmpfs_free_bytes " in text
    age = next(s.value for s in samples if s.name.endswith("oldest_age_seconds"))
    assert 290 <= age <= 400
    assert attempt_dir.name not in text
    assert str(tmp_path) not in text


def test_failure_counter_is_labelled_by_stage_and_error_code(tmp_path: Path) -> None:
    metrics.record_failure("cleanup", "verification_failed")
    metrics.record_failure("cleanup", "verification_failed")
    metrics.record_failure("asr", "asr_failed")

    samples = metrics.collect_samples(
        tmp_dir=str(tmp_path), slots_in_use=0, slots_total=1, admission_open=True
    )
    failures = {
        (s.labels["stage"], s.labels["error_code"]): s.value
        for s in samples
        if s.name == "maulwurf_ingest_failures_total"
    }
    assert failures == {("asr", "asr_failed"): 1.0, ("cleanup", "verification_failed"): 2.0}
