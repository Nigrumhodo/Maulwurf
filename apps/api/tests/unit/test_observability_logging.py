"""Unit tests for JSON logs, redaction, `X-Request-ID` and the Prometheus renderer (J2.5)."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import (
    JsonFormatter,
    RedactionFilter,
    correlation,
    current_ids,
    is_sensitive_key,
    scrub_text,
)
from app.core.prometheus import Sample, render
from app.core.request_context import RequestIdMiddleware, resolve_request_id

SECRET = "sk-live-7f3a9c2e51b84d6a"
TRANSCRIPT = "la prueba de cálculo es el lunes doce de noviembre"
PASSWORD = "hunter2-very-secret"
DSN_PASSWORD = "dsn-pass-93ka"


def _capture(name: str, *, redact: bool = True) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    if redact:
        handler.addFilter(RedactionFilter())
    logger = logging.getLogger(f"test.logs.{name}")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def _lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def test_json_line_carries_level_message_and_bound_ids() -> None:
    logger, stream = _capture("ids")
    with correlation(attempt_id="att-1", audio_id="aud-9", request_id="req-abc12345"):
        logger.info("upload.started bytes=%s", 1024)

    (line,) = _lines(stream)
    assert line["level"] == "INFO"
    assert line["msg"] == "upload.started bytes=1024"
    assert line["attempt_id"] == "att-1"
    assert line["audio_id"] == "aud-9"
    assert line["request_id"] == "req-abc12345"
    assert "run_id" not in line


def test_ids_are_restored_after_the_block() -> None:
    with correlation(run_id="run-7"):
        assert current_ids() == {"run_id": "run-7"}
    assert current_ids() == {}


def test_redaction_filter_masks_seeded_secret_and_transcript_fields() -> None:
    logger, stream = _capture("fields")
    logger.warning(
        "provider.call api_key=%s status=%s",
        SECRET,
        200,
        extra={"fields": {"transcript": TRANSCRIPT, "password": PASSWORD, "rows": 3}},
    )

    output = stream.getvalue()
    assert SECRET not in output
    assert TRANSCRIPT not in output
    assert PASSWORD not in output
    (line,) = _lines(stream)
    assert line["transcript"] == "[REDACTED]"
    assert line["password"] == "[REDACTED]"
    assert line["rows"] == 3
    assert "status=200" in str(line["msg"])


def test_redaction_masks_bearer_tokens_and_dsn_passwords_in_free_text() -> None:
    logger, stream = _capture("free_text")
    logger.error(
        "db failed dsn=postgresql+asyncpg://maulwurf:%s@127.0.0.1/maulwurf auth=Bearer %s",
        DSN_PASSWORD,
        SECRET,
    )

    output = stream.getvalue()
    assert DSN_PASSWORD not in output
    assert SECRET not in output
    assert "asyncpg://[REDACTED]@127.0.0.1" in output


def test_redaction_masks_secrets_inside_exception_text() -> None:
    logger, stream = _capture("exc")
    try:
        raise RuntimeError(f"connect postgresql://u:{DSN_PASSWORD}@db/x failed")
    except RuntimeError:
        logger.exception("reconciler.startup_failed")

    (line,) = _lines(stream)
    assert DSN_PASSWORD not in str(line["exc"])
    assert "RuntimeError" in str(line["exc"])


@pytest.mark.parametrize(
    ("key", "sensitive"),
    [
        ("authorization", True),
        ("Cookie", True),
        ("access_token", True),
        ("client_secret", True),
        ("password", True),
        ("api_key", True),
        ("transcript", True),
        ("prompt", True),
        ("text", True),
        ("transcript_version", False),
        ("token_count", False),
        ("context", False),
        ("next", False),
        ("audio_id", False),
    ],
)
def test_sensitive_key_classification(key: str, sensitive: bool) -> None:
    assert is_sensitive_key(key) is sensitive


def test_free_text_keeps_non_sensitive_pairs() -> None:
    assert scrub_text("index.noop transcript_version=2 audio_id=abc") == (
        "index.noop transcript_version=2 audio_id=abc"
    )


def test_without_the_filter_the_secret_reaches_the_output() -> None:
    """Negative control: without the guard the value leaks, so the guard is what protects it."""
    logger, stream = _capture("unfiltered", redact=False)
    logger.warning("provider.call api_key=%s", SECRET)
    assert SECRET in stream.getvalue()


@pytest.mark.parametrize(
    "incoming",
    ["abc-123_DEF.456", "req-0000000001", "7f3a9c2e51b84d6a7f3a9c2e51b84d6a"],
)
def test_valid_incoming_request_id_is_kept(incoming: str) -> None:
    assert resolve_request_id(incoming) == incoming


@pytest.mark.parametrize(
    "incoming",
    [None, "", "short", "bad value", "line\nbreak", "x" * 65, "-leading-dash", "semi;colon"],
)
def test_invalid_incoming_request_id_is_replaced(incoming: str | None) -> None:
    generated = resolve_request_id(incoming)
    assert re.fullmatch(r"[0-9a-f]{32}", generated)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_middleware_echoes_a_valid_request_id() -> None:
    client = TestClient(_app())
    response = client.get("/ping", headers={"X-Request-ID": "trace-abcdef12"})
    assert response.headers["x-request-id"] == "trace-abcdef12"


def test_middleware_replaces_a_malicious_request_id() -> None:
    client = TestClient(_app())
    response = client.get("/ping", headers={"X-Request-ID": "evil\"},{\"admin\":true"})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_access_log_has_no_query_string_or_body(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(_app())
    with caplog.at_level(logging.INFO, logger="app.core.request_context"):
        client.get(f"/ping?token={SECRET}")

    messages = [r.getMessage() for r in caplog.records if r.name == "app.core.request_context"]
    assert messages, "el middleware debe registrar una línea de acceso"
    assert all(SECRET not in message for message in messages)
    assert any("path=/ping" in message and "status=200" in message for message in messages)


def test_prometheus_render_declares_each_metric_once_and_escapes_labels() -> None:
    text = render(
        [
            Sample("maulwurf_outbox_events", "gauge", "Eventos.", 2, {"status": "pending"}),
            Sample("maulwurf_outbox_events", "gauge", "Eventos.", 0, {"status": 'fa"il\\ed'}),
            Sample("maulwurf_outbox_lag_seconds", "gauge", "Lag.", 12.5),
        ]
    )
    assert text.count("# TYPE maulwurf_outbox_events gauge") == 1
    assert 'maulwurf_outbox_events{status="pending"} 2' in text
    assert 'maulwurf_outbox_events{status="fa\\"il\\\\ed"} 0' in text
    assert "maulwurf_outbox_lag_seconds 12.5" in text
    assert text.endswith("\n")


def test_prometheus_render_rejects_invalid_names() -> None:
    with pytest.raises(ValueError):
        render([Sample("bad name", "gauge", "x", 1)])
    with pytest.raises(ValueError):
        render([Sample("ok_name", "gauge", "x", 1, {"bad-label": "v"})])
