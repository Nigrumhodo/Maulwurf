"""A1.8: envelope `validation_failed` sin eco de valores y con todos los motivos por campo."""
import json

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from app.core.errors import _handle_validation


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
