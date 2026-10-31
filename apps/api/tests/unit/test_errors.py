"""A1.8/A2.15: envelope `validation_failed` sin eco de valores y con todos los motivos por campo.

A2.15 adds tests for framework 404/405 and unhandled exceptions: the same envelope, no
exception text in the 500 body, and the request id in the log line.
"""
import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import _handle_validation, install_error_handlers
from app.core.request_context import RequestIdMiddleware

SECRET = "db-password=hunter2"


async def test_keeps_every_reason_for_a_field_and_never_the_input() -> None:
    exc = RequestValidationError(
        [
            {"loc": ("body", "title"), "msg": "too long", "type": "x", "input": "SECRETO"},
            {"loc": ("body", "title"), "msg": "bad chars", "type": "y", "input": "SECRETO"},
            {"loc": ("body",), "msg": "missing", "type": "z", "input": None},
        ]
    )

    response = await _handle_validation(Request({"type": "http"}), exc)
    body = json.loads(bytes(response.body))

    assert response.status_code == 422
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["details"]["fields"] == {
        "title": "too long; bad chars",
        "body": "missing",
    }
    assert "SECRETO" not in bytes(response.body).decode()


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"connect failed {SECRET}")

    return app


def test_unknown_route_uses_the_error_envelope() -> None:
    response = TestClient(_app()).get("/no-such-route", headers={"X-Request-ID": "trace-a215404"})

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "trace-a215404"
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Recurso no encontrado.",
            "details": {},
        }
    }


def test_wrong_method_uses_the_error_envelope() -> None:
    response = TestClient(_app()).post("/ok")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.json() == {
        "error": {
            "code": "method_not_allowed",
            "message": "Método HTTP no permitido para esta ruta.",
            "details": {},
        }
    }


def test_unhandled_exception_uses_the_envelope_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="app.core.errors"):
        response = client.get("/boom", headers={"X-Request-ID": "trace-a215001"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Error interno del servidor.",
            "details": {},
        }
    }
    assert SECRET not in response.text
    assert "RuntimeError" not in response.text
    assert "connect failed" not in response.text
    assert response.headers["x-request-id"] == "trace-a215001"

    records = [r for r in caplog.records if r.name == "app.core.errors"]
    assert records
    record = records[-1]
    assert getattr(record, "request_id", None) == "trace-a215001"
    assert "exc_type=RuntimeError" in record.getMessage()
    assert SECRET not in caplog.text


def test_http_exception_detail_is_not_echoed() -> None:
    app = _app()

    @app.get("/custom-not-found")
    async def custom_not_found() -> None:
        raise StarletteHTTPException(status_code=404, detail=f"missing {SECRET}")

    response = TestClient(app).get("/custom-not-found")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert SECRET not in response.text
