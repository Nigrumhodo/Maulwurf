"""Envelope de error tipado de la API (DISENO §3.2): `{"error": {code, message, details}}`.

Los mensajes y detalles nunca llevan PII, tokens ni contenido de clases.
"""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def auth_required() -> ApiError:
    return ApiError(401, "auth_required", "Inicia sesión para continuar.")


def csrf_invalid() -> ApiError:
    return ApiError(403, "csrf_invalid", "Solicitud rechazada; recarga la sesión con GET /me.")


async def _handle(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - registrado solo para ApiError
    body = {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return JSONResponse(status_code=exc.status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle)
