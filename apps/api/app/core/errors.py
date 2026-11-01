"""Envelope de error tipado de la API (DISENO §3.2): `{"error": {code, message, details}}`.

Los mensajes y detalles nunca llevan PII ni contenido de clases. Única excepción
documentada (plan A2.4, punto C7): el `409 duplicate_variant` lleva el
`variant_confirmation_token` PLANO en `details`, porque DISENO §3.5 exige que el cliente
pueda presentarlo en el segundo `POST`; igual que el `csrf_token` de `GET /me`, solo viaja
en esa respuesta y nunca a logs ni a otros endpoints.
"""
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Statuses Starlette/FastAPI raise before a router runs. Messages are fixed on purpose: the
# framework detail can echo request input and is never forwarded (DISENO §3.2).
_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Solicitud inválida."),
    401: ("auth_required", "Inicia sesión para continuar."),
    403: ("forbidden", "No tienes permiso para realizar esta operación."),
    404: ("not_found", "Recurso no encontrado."),
    405: ("method_not_allowed", "Método HTTP no permitido para esta ruta."),
    409: ("invalid_transition", "La operación no está permitida en el estado actual."),
    410: ("upload_expired", "La subida expiró; vuelve a crear la clase."),
    413: ("payload_too_large", "El contenido supera el límite permitido."),
    415: ("unsupported_format", "El formato del contenido no está admitido."),
    422: ("validation_failed", "Hay campos inválidos."),
    429: ("rate_limited", "Demasiadas solicitudes; inténtalo más tarde."),
}
_INTERNAL_ERROR: tuple[str, str] = ("internal_error", "Error interno del servidor.")


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


def _response(error: ApiError, extra_headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"error": {"code": error.code, "message": error.message, "details": error.details}}
    headers = {**error.headers, **(extra_headers or {})}
    return JSONResponse(status_code=error.status_code, content=body, headers=headers or None)


async def _handle(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 - registrado solo para ApiError
    return _response(exc)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _http_error(status_code: int) -> ApiError:
    if status_code in _HTTP_ERRORS:
        code, message = _HTTP_ERRORS[status_code]
        return ApiError(status_code, code, message)
    if 400 <= status_code < 500:
        # Preserve a client-error status even when the catalog has no specific code yet.
        # Turning it into a 500 would page on malformed input and make clients retry.
        return ApiError(status_code, "bad_request", "Solicitud inválida.")
    # An unmapped 5xx is a bug: fail closed with the typed internal error.
    return ApiError(500, *_INTERNAL_ERROR)


async def _handle_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - registrado solo para ella
    error = _http_error(exc.status_code)
    error.headers.update(exc.headers or {})
    logger.warning(
        "http.error method=%s path=%s status=%s code=%s",
        request.method,
        request.url.path,
        error.status_code,
        error.code,
        extra={"request_id": _request_id(request)},
    )
    return _response(error)


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    # Log the type only: the message, its arguments and the traceback can carry input,
    # transcripts, DSNs or secrets (same rule as `readyz` in health.py).
    logger.error(
        "http.unhandled_exception method=%s path=%s exc_type=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
        extra={"request_id": request_id},
    )
    extra_headers = {"X-Request-ID": request_id} if request_id else None
    return _response(internal_error(), extra_headers)


def not_found() -> ApiError:
    # Inexistente y ajeno responden igual: no se revela si el recurso existe (ADR-0006).
    return ApiError(404, "not_found", "Recurso no encontrado.")


def internal_error() -> ApiError:
    return ApiError(500, *_INTERNAL_ERROR)


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


def google_deletion_unavailable() -> ApiError:
    # A2.7: en S2 no puede existir ningún evento remoto (`calendar_events` y
    # `google_remote_operations` son S3/F2), así que la opción explícita del contrato
    # (DISENO §3.5) se congela con un 409 honesto: nunca se comunica que se pidió un
    # borrado remoto que nadie va a ejecutar.
    return ApiError(
        409,
        "google_deletion_unavailable",
        "La eliminación remota de eventos de Google aún no está disponible.",
    )


def srt_unavailable() -> ApiError:
    # A2.9: the SRT is offered ONLY when the timestamps the ASR measured are usable (real
    # precision, ordered and inside the class duration). Anything else is refused instead of
    # inventing or shifting minutes (ESPECIFICACION.md M2/M3, S2.md §A2.9).
    return ApiError(
        409,
        "srt_unavailable",
        "Esta clase no tiene tiempos válidos para exportar a SRT.",
    )


def google_integration_unavailable() -> ApiError:
    # J3.3: without a configured OAuth client there is no consent URL to send the user to.
    # Fail with a typed 503 instead of a 500 or a redirect to an unconfigured Google app.
    return ApiError(
        503,
        "google_integration_unavailable",
        "La integración con Google Calendar no está configurada.",
    )


def google_not_connected() -> ApiError:
    # J3.3: reconnecting requires a previous grant to repair; a user with no credential row
    # must use `connect` (the UI derives both buttons from GET /integrations/status).
    return ApiError(
        409,
        "google_not_connected",
        "No hay una integración de Google que reconectar.",
    )


def calendar_sync_unavailable(reason: str) -> ApiError:
    # J3.8: a manual refresh needs a connected grant, every Calendar scope and at least one
    # selected read calendar. The reason is a closed set so the UI can tell "connect",
    # "reconnect" and "select calendars" apart without parsing messages.
    return ApiError(
        409,
        "calendar_sync_unavailable",
        "El calendario no se puede sincronizar en el estado actual.",
        {"reason": reason},
    )


def calendar_sync_cooldown(retry_after_seconds: int) -> ApiError:
    # J3.8 (review): each manual refresh rereads every selected calendar against Google, so
    # the route has a per-user floor. `rate_limited` is the API-wide 429 code; the reason and
    # the header let the client schedule the retry without interpreting the message.
    return ApiError(
        429,
        "rate_limited",
        "La sincronización manual se ejecutó hace poco; espera antes de reintentar.",
        {"reason": "calendar_sync_cooldown", "retry_after_seconds": retry_after_seconds},
        headers={"Retry-After": str(retry_after_seconds)},
    )


def calendar_sync_temporarily_unavailable(
    retry_after_seconds: int | None = None,
) -> ApiError:
    # Google Calendar (or the token endpoint) failed after its bounded retries. The mirror
    # is untouched, so the client can retry later instead of treating it as a state error.
    # A bounded row-lock wait uses the same response with `Retry-After`.
    headers = (
        {"Retry-After": str(retry_after_seconds)}
        if retry_after_seconds is not None
        else None
    )
    return ApiError(
        503,
        "calendar_sync_temporarily_unavailable",
        "Google Calendar no está disponible; inténtalo más tarde.",
        headers=headers,
    )


def notifications_test_unavailable(reason: str) -> ApiError:
    # A4.2: a test send needs Gmail opt-in and a verified recipient. The reason is a closed
    # set so the settings screen can explain the next step without parsing the message.
    return ApiError(
        409,
        "notifications_test_unavailable",
        "El envío de prueba no está disponible en el estado actual.",
        {"reason": reason},
    )


def notification_not_resendable(status: str) -> ApiError:
    # A4.2: pending/sending are already queued or in flight, and cancelled occurrences are
    # obsolete. Only an audited terminal delivery may be repeated.
    return ApiError(
        409,
        "notification_not_resendable",
        "Esta entrega no se puede reexpedir en su estado actual.",
        {"status": status},
    )


def acknowledgement_required() -> ApiError:
    # A4.2/M8: a manual resend may duplicate a delivery that Gmail already accepted, so the
    # client must acknowledge the risk explicitly.
    return ApiError(
        422,
        "acknowledgement_required",
        "Confirma el riesgo de entrega duplicada para reexpedir.",
        {"field": "acknowledge_duplicate_risk"},
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
    # Framework 404/405 and any unhandled exception also answer with the envelope (A2.15).
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(Exception, _handle_unexpected)
