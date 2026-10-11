"""Envelope de error tipado de la API (DISENO §3.2): `{"error": {code, message, details}}`.

Los mensajes y detalles nunca llevan PII ni contenido de clases. Única excepción
documentada (plan A2.4, punto C7): el `409 duplicate_variant` lleva el
`variant_confirmation_token` PLANO en `details`, porque DISENO §3.5 exige que el cliente
pueda presentarlo en el segundo `POST`; igual que el `csrf_token` de `GET /me`, solo viaja
en esa respuesta y nunca a logs ni a otros endpoints.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        # Cabeceras de contrato, p. ej. `Retry-After` en 429/503 (DISENO §3.1).
        self.headers = headers or {}


def auth_required() -> ApiError:
    return ApiError(401, "auth_required", "Inicia sesión para continuar.")


def csrf_invalid() -> ApiError:
    return ApiError(403, "csrf_invalid", "Solicitud rechazada; recarga la sesión con GET /me.")


async def _handle(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - registrado solo para ApiError
    body = {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers or None)


def not_found() -> ApiError:
    # Inexistente y ajeno responden igual: no se revela si el recurso existe (ADR-0006).
    return ApiError(404, "not_found", "Recurso no encontrado.")


def consent_required(privacy_notice_version: str) -> ApiError:
    # Solo la versión vigente: la enviada por el cliente está obsoleta y no aporta nada.
    return ApiError(
        422,
        "consent_required",
        "Acepta el aviso de privacidad vigente y el procesamiento en la nube para continuar.",
        {"privacy_notice_version": privacy_notice_version},
    )


def language_not_allowed(allowed: Sequence[str]) -> ApiError:
    # `allowed` es la allowlist publicada (D3), no la que pidió el cliente.
    return ApiError(
        422,
        "language_not_allowed",
        "Idioma no disponible para transcripción.",
        {"allowed": list(allowed)},
    )


def validation_failed(fields: dict[str, str]) -> ApiError:
    return ApiError(422, "validation_failed", "Hay campos inválidos.", {"fields": fields})


def duplicate_variant(existing_audio_id: uuid.UUID, token: str, expires_at: datetime) -> ApiError:
    """`409` con el token opaco de un solo uso y su expiración (DISENO §3.5, M2/A2.4).

    Ver docstring del módulo: es el ÚNICO error cuyo `details` lleva un token, y en plano
    porque el segundo `POST` lo necesita; en BD solo se persiste su SHA-256.
    """
    return ApiError(
        409,
        "duplicate_variant",
        "Existe una clase con el mismo contenido y otro contexto; confirma para continuar.",
        {
            "existing_audio_id": str(existing_audio_id),
            "variant_confirmation_token": token,
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        },
    )


def variant_hash_mismatch() -> ApiError:
    # El segundo PUT fallido deja su intento `rejected` con este código (S2.md §A2.5).
    return ApiError(
        422,
        "variant_hash_mismatch",
        "El contenido no coincide con la clase confirmada; vuelve a subir el archivo original.",
    )


async def _handle_validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registrado solo para ella
    # Solo ubicación y mensaje por campo: nunca el valor recibido (puede traer PII).
    fields: dict[str, str] = {}
    for error in exc.errors():
        name = ".".join(str(part) for part in error["loc"][1:]) or "body"
        # Un campo puede fallar por varios motivos: se conservan todos, no solo el último.
        fields[name] = f"{fields[name]}; {error['msg']}" if name in fields else error["msg"]
    return await _handle(_, validation_failed(fields))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle)
    app.add_exception_handler(RequestValidationError, _handle_validation)
