"""Recepción binaria del `PUT`: streaming sin spool, límite y compensación (A2.4, S2.md §A2.4).

El cuerpo NUNCA se materializa: ni `UploadFile`, ni `request.body()`, ni `SpooledTemporaryFile`
(espec. M2). El router valida sesión/CSRF/propiedad/estado/expiración/tamaño declarado ANTES
de delegar aquí; este módulo abre el sink, recorre `request.stream()` con SHA-256 incremental,
corta al superar el límite en caliente y decide el dedupe DESPUÉS de limpiar: `200
duplicate_exact` y `409 duplicate_variant` solo salen con `discard()` verificado.

El sink (`ReceptionSink`) es el destino efímero del pipe-through (tmpfs privado por intento)
del lado de la ingesta. Su transporte todavía no existe: S2.1 congelará la ruta interna con
la API (C2), así que el sink por defecto responde `503 capacity_unavailable` ANTES de leer un
byte —el mismo precedente honesto de A1.8— y los tests inyectan un doble vía
`get_reception_sink`. El sink es además quien valida el formato real con ffprobe: este módulo
solo mapea su rechazo a `415 unsupported_format` (C9) y su estado tras admitir al `202`
(C12); la API no transiciona estados de ingesta.

Compensación (plan §8.1): todo fallo posterior a recibir deja la reserva limpia —temporales
descartados y, si ya se escribió la identidad, restaurados— salvo que ni siquiera eso sea
posible (BD caída), caso que deja al reconciliador de S2.7. Un `202` jamás se emite sin que
el sink haya admitido el contenido.
"""

import hashlib
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError, duplicate_variant, variant_hash_mismatch
from app.models import Audio, IngestionAttempt
from app.services import dedupe
from app.services.audios import RETRY_AFTER_SECONDS
from app.services.tenant import get_owned


class SinkError(Exception):
    """Fallo del sink de recepción; `_sink_api_error` lo mapea al catálogo de DISENO §3.2."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Admission:
    """Lo que el sink devuelve al admitir el intento (estado real: S2.1, ver C12)."""

    status: str


class ReceptionSink(Protocol):
    """Destino efímero del pipe-through: la API escribe, nunca retiene.

    Contrato mínimo de A2.4 (`begin/write/finish/discard/admit`); S2.1 lo ampliará cuando
    congele la ruta interna (C2). `discard()` debe poder VERIFICARSE antes de cualquier
    respuesta `200/409` (M2: «limpiar primero»).
    """

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        expected_bytes: int | None,
    ) -> None: ...

    async def write(self, chunk: bytes) -> None: ...

    async def finish(self) -> None: ...

    async def discard(self) -> None: ...

    async def admit(self) -> Admission: ...


class UnavailableReceptionSink:
    """Sink por defecto: recepción no disponible hasta que S2.1 congele el contrato (C2)."""

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        expected_bytes: int | None,
    ) -> None:
        raise SinkError("capacity_unavailable")

    async def write(self, chunk: bytes) -> None:
        raise SinkError("capacity_unavailable")

    async def finish(self) -> None:
        raise SinkError("capacity_unavailable")

    async def discard(self) -> None:
        raise SinkError("capacity_unavailable")

    async def admit(self) -> Admission:
        raise SinkError("capacity_unavailable")


def get_reception_sink() -> ReceptionSink:
    """Dependencia FastAPI: en producción, el «aún no disponible» hasta S2.1; tests la pisan."""
    return UnavailableReceptionSink()


@dataclass(frozen=True)
class UploadResult:
    """Payload plano construido SIEMPRE antes del `commit` (el ORM expira tras él)."""

    status_code: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class _PreviousWrite:
    """Valores previos a la escritura de identidad, por si hay que compensar (§8.1)."""

    sha256: str | None
    original_bytes: int | None
    received_bytes: int
    expected_bytes: int | None

    @classmethod
    def of(cls, audio: Audio, attempt: IngestionAttempt) -> "_PreviousWrite":
        return cls(
            sha256=audio.sha256,
            original_bytes=audio.original_bytes,
            received_bytes=attempt.received_bytes,
            expected_bytes=attempt.expected_bytes,
        )


def effective_upload_limit() -> int:
    """Límite de corte vigente (C5): lo publicado por el spike manda, como en
    `allowed_languages()`; sin `limits_validated` se usa el provisional de S1."""
    capabilities = settings.ingestion_capabilities
    if capabilities.limits_validated and capabilities.max_upload_bytes is not None:
        return capabilities.max_upload_bytes
    return settings.upload_max_bytes


def _sink_api_error(exc: SinkError) -> ApiError:
    """Solo códigos del catálogo §3.2: un código desconocido del sink jamás se expone."""
    if exc.code == "unsupported_format":
        return ApiError(415, "unsupported_format", "La ingesta rechazó el formato del audio.")
    if exc.code == "capacity_unavailable":
        return ApiError(
            503,
            "capacity_unavailable",
            "La recepción de audio no está disponible; inténtalo más tarde.",
            {"retryable": False},
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    return ApiError(500, "internal_error", "No se pudo procesar la recepción.")


async def _discard_lenient(sink: ReceptionSink) -> None:
    """Limpieza best-effort: la reserva sigue viva, así que los temporales quedan justificados."""
    try:
        await sink.discard()
    except SinkError:
        return  # S2.7 reconcilia lo que no se pudo descartar; no hay respuesta dependiente.


async def _discard_verified(sink: ReceptionSink) -> None:
    """Sin respuesta `200/409` hasta que la limpieza termine (invariante de M2 y C10)."""
    try:
        await sink.discard()
    except SinkError as exc:
        raise ApiError(
            500, "internal_error", "No se pudo limpiar el contenido temporal recibido."
        ) from exc


async def _abort_disconnected(
    db: AsyncSession, *, attempt: IngestionAttempt, sink: ReceptionSink
) -> None:
    """Desconexión del cliente (C6): limpieza verificada + intento `cancelled`, sin respuesta."""
    # Un temporal huérfano por fallo de red lo reconcilia S2.7: la reserva se cancela igual,
    # que es lo que exige el contrato («desconectar cancela y limpia el intento»).
    with suppress(SinkError):
        await sink.discard()
    attempt.status = "cancelled"
    await db.commit()


async def _restore_identity(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    attempt_id: uuid.UUID,
    previous: _PreviousWrite,
) -> None:
    """Compensación tras un `admit()` fallido (plan §8.1): la reserva vuelve a estar limpia."""
    try:
        audio = await get_owned(db, Audio, user_id, audio_id)
        attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id)
        if audio is None or attempt is None:
            return
        audio.sha256 = previous.sha256
        audio.original_bytes = previous.original_bytes
        attempt.received_bytes = previous.received_bytes
        attempt.expected_bytes = previous.expected_bytes
        await db.commit()
    except Exception:  # la compensación es best-effort por diseño (plan §8.1)
        # Ni la compensación puede escribir (BD caída): el 503 sigue siendo honesto,
        # `exclude_audio_id` evita que esa identidad restante se confunda con un duplicado
        # en el reintento, y el reconciliador de S2.7 se ocupa del resto.
        with suppress(Exception):
            await db.rollback()


async def _resolve_duplicate(
    db: AsyncSession,
    sink: ReceptionSink,
    *,
    user_id: uuid.UUID,
    audio: Audio,
    attempt: IngestionAttempt,
    identity: dedupe.Identity,
    sha256_hex: str,
) -> UploadResult | None:
    """Decisión de dedupe tras recepción completa; `None` si el contenido es nuevo.

    Aquí se cumple el orden no negociable: `discard()` verificado → absorber la reserva →
    commit → responder. Si `discard()` falla, NO se responde duplicado ni se toca la
    reserva (invariante (a) del plan).
    """
    canonical = await dedupe.lookup_exact_identity(
        db,
        user_id=user_id,
        sha256_hex=sha256_hex,
        identity=identity,
        exclude_audio_id=audio.id,
    )
    if canonical is not None:
        canonical_id = canonical.id
        await _discard_verified(sink)
        payload = {"audio_id": str(canonical_id), "outcome": "duplicate_exact"}
        await dedupe.absorb_reservation(db, audio=audio, attempt=attempt)
        await db.commit()
        return UploadResult(200, payload)

    variant = await dedupe.lookup_variant(
        db,
        user_id=user_id,
        sha256_hex=sha256_hex,
        identity=identity,
        exclude_audio_id=audio.id,
    )
    if variant is None:
        return None
    canonical_id = variant.id
    await _discard_verified(sink)
    await dedupe.absorb_reservation(db, audio=audio, attempt=attempt)
    issued = await dedupe.issue_variant_token(
        db, canonical=variant, identity=identity, content_sha256=sha256_hex
    )
    # `ApiError` construido con valores planos YA leídos: nada del ORM tras el commit.
    error = duplicate_variant(canonical_id, issued.plaintext, issued.expires_at)
    await db.commit()
    raise error


async def receive_content(
    db: AsyncSession,
    request: Request,
    sink: ReceptionSink,
    *,
    user_id: uuid.UUID,
    audio: Audio,
    attempt: IngestionAttempt,
    content_length: int | None,
) -> UploadResult:
    """Recibe el cuerpo en streaming (sin spool), decide dedupe y admite o compensa.

    Orden (S2.md §A2.4): sink `begin` → stream + SHA-256 + corte → `finish` → verificar
    hash de variante → limpiar primero → decidir → commit → `admit` → `202`. Cualquier
    fallo posterior a recibir compensa temporales (y la identidad si ya se escribió).
    """
    audio_id = audio.id
    attempt_id = attempt.id
    identity = dedupe.identity_of(audio)
    limit = effective_upload_limit()

    try:
        await sink.begin(
            user_id=user_id,
            audio_id=audio_id,
            attempt_id=attempt_id,
            expected_bytes=content_length,
        )
    except SinkError as exc:
        raise _sink_api_error(exc) from exc  # 503 ANTES de leer un solo byte.

    hasher = hashlib.sha256()
    received = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > limit:
                # Corte en caliente (C5/C6): no se lee el resto ni se escribe nada en BD;
                # la reserva queda `awaiting_upload` y los temporales justificados.
                await _discard_lenient(sink)
                raise ApiError(
                    413,
                    "payload_too_large",
                    "El archivo supera el tamaño máximo permitido.",
                    {"max_bytes": limit},
                )
            hasher.update(chunk)
            await sink.write(chunk)
    except ApiError:
        raise
    except SinkError as exc:
        await _discard_lenient(sink)
        raise _sink_api_error(exc) from exc
    except Exception:
        # Desconexión/lectura rota a mitad de subida (C6): no hay respuesta que enviar.
        await _abort_disconnected(db, attempt=attempt, sink=sink)
        raise

    if received == 0:
        # C10: un cuerpo vacío nunca se admite (jamás `202`); `415` del catálogo §3.2.
        await _discard_lenient(sink)
        raise ApiError(415, "unsupported_format", "El contenido de audio está vacío.")

    try:
        await sink.finish()
    except SinkError as exc:
        await _discard_lenient(sink)
        raise _sink_api_error(exc) from exc

    sha256_hex = hasher.hexdigest()

    # Segundo `PUT` de una variante confirmada: el hash se verifica ANTES de admisión/ASR.
    confirmed = await dedupe.find_confirmed_variant(db, user_id=user_id, audio_id=audio_id)
    if confirmed is not None and not dedupe.verify_variant_hash(confirmed, sha256_hex):
        await _discard_verified(sink)
        attempt.status = "rejected"
        attempt.error_code = "variant_hash_mismatch"
        await db.commit()
        raise variant_hash_mismatch()

    if confirmed is None:
        duplicate = await _resolve_duplicate(
            db,
            sink,
            user_id=user_id,
            audio=audio,
            attempt=attempt,
            identity=identity,
            sha256_hex=sha256_hex,
        )
        if duplicate is not None:
            return duplicate

    # Contenido nuevo o variante confirmada con hash correcto: escribir identidad y admitir.
    previous = _PreviousWrite.of(audio, attempt)
    try:
        raced = await dedupe.admit_identity(
            db,
            audio=audio,
            attempt=attempt,
            sha256_hex=sha256_hex,
            received_bytes=received,
            expected_bytes=content_length,
        )
    except ApiError:
        raise
    except Exception as exc:
        # BD falló DESPUÉS de recibir (plan §16): revertir + temporales; reserva intacta.
        with suppress(Exception):
            await db.rollback()
        await _discard_lenient(sink)
        raise ApiError(
            500, "internal_error", "No se pudo registrar el contenido recibido."
        ) from exc

    if raced is not None:
        # Carrera ganada por otra subida exacta: convergencia hacia la canónica (M2).
        raced_id = raced.id
        await _discard_verified(sink)
        payload = {"audio_id": str(raced_id), "outcome": "duplicate_exact"}
        fresh_audio = await get_owned(db, Audio, user_id, audio_id)
        fresh_attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id)
        if fresh_audio is not None and fresh_attempt is not None:
            # Relectura obligatoria: el `rollback` de `admit_identity` expiró los objetos.
            await dedupe.absorb_reservation(db, audio=fresh_audio, attempt=fresh_attempt)
        await db.commit()
        return UploadResult(200, payload)

    try:
        admission = await sink.admit()
    except SinkError as exc:
        # La identidad YA se confirmó: compensarla para no dejar identidad sin contenido
        # admitido, y descartar el temporal (§8.1). El `503` invita a reintentar dentro de
        # la ventana; `exclude_audio_id` hace idempotente ese reintento.
        await _restore_identity(
            db, user_id=user_id, audio_id=audio_id, attempt_id=attempt_id, previous=previous
        )
        await _discard_lenient(sink)
        raise _sink_api_error(exc) from exc

    return UploadResult(
        202,
        {"attempt_id": str(attempt_id), "status": admission.status, "received_bytes": received},
    )
