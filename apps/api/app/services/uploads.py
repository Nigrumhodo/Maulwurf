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

Concurrent-variant re-check (A2.5, S2.md §A2.5 review finding 2026-10-11): two uploads with the
same hash and different contexts can both miss the pre-commit lookups, and the exact-identity
unique index does not collide across contexts. After committing its identity, the admission
re-runs the variant lookup and the late arrival (`dedupe.is_late_arrival`) verifies the discard,
absorbs its provisional class and answers `409 duplicate_variant` with a token bound to its
discarded context. Token-confirmed variants skip the re-check: sharing a hash across contexts is
legal once the human confirmed it.
"""

import hashlib
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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


@dataclass(frozen=True)
class Received:
    """Hash and byte count that `ingest` computed over the bytes it actually stored."""

    sha256: str
    received_bytes: int


# ADR-0008 §3: the reasons `api` sends with `discard`. `ingest` maps each to a status.
DiscardReason = Literal[
    "duplicate_exact",
    "duplicate_variant",
    "variant_hash_mismatch",
    "client_disconnected",
    "reception_integrity_failed",
    "api_error",
]


class ReceptionSink(Protocol):
    """Destino efímero del pipe-through: la API escribe, nunca retiene.

    `finish()` returns the remote hash and count, which `receive_content` compares with its
    own. `discard()` must VERIFY the cleanup before any `200/409` answer (M2: «clean up
    first»); `abort()` only cancels the in-flight stream and expects no answer.
    """

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        fencing_token: int,
        declared_bytes: int | None,
    ) -> None: ...

    async def write(self, chunk: bytes) -> None: ...

    async def finish(self) -> Received: ...

    async def abort(self) -> None: ...

    async def discard(self, reason: DiscardReason) -> None: ...

    async def admit(self) -> Admission: ...


class UnavailableReceptionSink:
    """Sink used when `ingest` is not configured: every call answers `503` before a byte."""

    async def begin(
        self,
        *,
        user_id: uuid.UUID,
        audio_id: uuid.UUID,
        attempt_id: uuid.UUID,
        fencing_token: int,
        declared_bytes: int | None,
    ) -> None:
        raise SinkError("capacity_unavailable")

    async def write(self, chunk: bytes) -> None:
        raise SinkError("capacity_unavailable")

    async def finish(self) -> Received:
        raise SinkError("capacity_unavailable")

    async def abort(self) -> None:
        return None

    async def discard(self, reason: DiscardReason) -> None:
        raise SinkError("capacity_unavailable")

    async def admit(self) -> Admission:
        raise SinkError("capacity_unavailable")


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


def map_ingest_code(code: str) -> ApiError:
    """Public mapping of an ingest answer (ADR-0008 §8, S2.13).

    Format reasons collapse to ``415``. An internal credential failure is
    ``500`` and is never shown as ``401``. Unknown codes stay ``500``.
    """
    if code in {
        "unsupported_format", "url", "playlist", "malformed", "unverified_container",
        "not_pcm_s16", "multi_channel", "unexpected_container",
    }:
        return ApiError(415, "unsupported_format", "La ingesta rechazó el formato del audio.")
    if code == "payload_too_large":
        return ApiError(
            413,
            "payload_too_large",
            "El audio supera el límite de recepción.",
            {"max_bytes": effective_upload_limit()},
        )
    if code in {
        "capacity_unavailable", "probe_unavailable", "wrong_instance", "reception_not_held",
    }:
        # §3.1: a capacity 503 carries `Retry-After`; that IS the retry signal, so the body
        # must not contradict it with `retryable=false` (A2.19).
        return ApiError(
            503,
            "capacity_unavailable",
            "La recepción de audio no está disponible; inténtalo más tarde.",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    if code == "attempt_not_active":
        return ApiError(409, "attempt_not_active", "El intento de subida ya no admite audio.")
    if code == "variant_hash_mismatch":
        return variant_hash_mismatch()
    return ApiError(500, "internal_error", "No se pudo procesar la recepción.")


def _sink_api_error(exc: SinkError) -> ApiError:
    """Solo códigos del catálogo §3.2: un código desconocido del sink jamás se expone."""
    return map_ingest_code(exc.code)


async def _discard_lenient(sink: ReceptionSink, reason: DiscardReason) -> None:
    """Limpieza best-effort: la reserva sigue viva, así que los temporales quedan justificados."""
    try:
        await sink.discard(reason)
    except SinkError:
        return  # S2.7 reconcilia lo que no se pudo descartar; no hay respuesta dependiente.


async def _discard_verified(sink: ReceptionSink, reason: DiscardReason) -> None:
    """Sin respuesta `200/409` hasta que la limpieza termine (invariante de M2 y C10)."""
    try:
        await sink.discard(reason)
    except SinkError as exc:
        raise ApiError(
            500, "internal_error", "No se pudo limpiar el contenido temporal recibido."
        ) from exc


async def _abort_disconnected(sink: ReceptionSink, *, finished: bool) -> None:
    """Client disconnect (ADR-0008 §7): cancels the internal hop, with no response.

    Before `finish()`, cancelling is enough: `ingest` sees the body cut off and records
    `cancelled` on its own. If `receive` has already answered, `ingest` holds a retained
    temp file and must be asked for a verified `discard`.
    """
    await sink.abort()
    if finished:
        with suppress(SinkError):
            await sink.discard("client_disconnected")



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
        await _discard_verified(sink, "duplicate_exact")
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
    await _discard_verified(sink, "duplicate_variant")
    await dedupe.absorb_reservation(db, audio=audio, attempt=attempt)
    issued = await dedupe.issue_variant_token(
        db, canonical=variant, identity=identity, content_sha256=sha256_hex
    )
    # `ApiError` construido con valores planos YA leídos: nada del ORM tras el commit.
    error = duplicate_variant(canonical_id, issued.plaintext, issued.expires_at)
    await db.commit()
    raise error


async def _reject_if_concurrent_variant(
    db: AsyncSession,
    sink: ReceptionSink,
    *,
    user_id: uuid.UUID,
    audio_id: uuid.UUID,
    attempt_id: uuid.UUID,
    identity: dedupe.Identity,
    sha256_hex: str,
) -> None:
    """Post-admission re-check for the concurrent-variant window (A2.5, review finding 2026-10-11).

    After our identity commit, at least the later committer of a same-hash/different-context race
    sees the rival, so exactly one class survives per race: the late arrival verifies the discard,
    absorbs its provisional reservation and answers `409 duplicate_variant` with a token bound to
    its discarded context. The ORM rows are re-read because `admit_identity` committed (or rolled
    back) and expired them.
    """
    rival = await dedupe.lookup_variant(
        db,
        user_id=user_id,
        sha256_hex=sha256_hex,
        identity=identity,
        exclude_audio_id=audio_id,
    )
    if rival is None:
        return  # no rival: the common path pays exactly one extra SELECT.
    own = await get_owned(db, Audio, user_id, audio_id)
    attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id)
    if own is None or attempt is None:
        return
    if not dedupe.is_late_arrival(rival, own):
        # This class holds precedence: the rival's own re-check yields toward us.
        return
    rival_id = rival.id
    await _discard_verified(sink, "duplicate_variant")
    await dedupe.absorb_reservation(db, audio=own, attempt=attempt)
    issued = await dedupe.issue_variant_token(
        db, canonical=rival, identity=identity, content_sha256=sha256_hex
    )
    # Flat values read BEFORE the commit: nothing from the ORM after it.
    error = duplicate_variant(rival_id, issued.plaintext, issued.expires_at)
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
            fencing_token=attempt.fencing_token,
            declared_bytes=content_length,
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
                # Mid-stream cut (ADR-0008 §7): the internal hop is cancelled; `ingest` sees the
                # body cut off and records `cancelled` itself, so there is no discard here.
                await sink.abort()
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
        await sink.abort()
        raise _sink_api_error(exc) from exc
    except Exception:
        # Disconnect or broken read mid-upload (ADR-0008 §7): no response to send.
        await _abort_disconnected(sink, finished=False)
        raise

    if received == 0:
        # C10: un cuerpo vacío nunca se admite (jamás `202`); `415` del catálogo §3.2.
        await sink.abort()
        raise ApiError(415, "unsupported_format", "El contenido de audio está vacío.")

    try:
        remote = await sink.finish()
    except SinkError as exc:
        await sink.abort()
        raise _sink_api_error(exc) from exc

    sha256_hex = hasher.hexdigest()
    # ADR-0008 §6: `ingest` hashed the bytes it stored. A mismatch means the hop lost or
    # changed data, so the reception is discarded and the public answer is a 500.
    if remote.sha256 != sha256_hex or remote.received_bytes != received:
        await _discard_lenient(sink, "reception_integrity_failed")
        raise ApiError(500, "internal_error", "No se pudo procesar la recepción.")

    # Segundo `PUT` de una variante confirmada: el hash se verifica ANTES de admisión/ASR.
    confirmed = await dedupe.find_confirmed_variant(db, user_id=user_id, audio_id=audio_id)
    if confirmed is not None and not dedupe.verify_variant_hash(confirmed, sha256_hex):
        await _discard_verified(sink, "variant_hash_mismatch")
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
        await _discard_lenient(sink, "api_error")
        raise ApiError(
            500, "internal_error", "No se pudo registrar el contenido recibido."
        ) from exc

    if raced is not None:
        # Carrera ganada por otra subida exacta: convergencia hacia la canónica (M2).
        raced_id = raced.id
        await _discard_verified(sink, "duplicate_exact")
        payload = {"audio_id": str(raced_id), "outcome": "duplicate_exact"}
        fresh_audio = await get_owned(db, Audio, user_id, audio_id)
        fresh_attempt = await get_owned(db, IngestionAttempt, user_id, attempt_id)
        if fresh_audio is not None and fresh_attempt is not None:
            # Relectura obligatoria: el `rollback` de `admit_identity` expiró los objetos.
            await dedupe.absorb_reservation(db, audio=fresh_audio, attempt=fresh_attempt)
        await db.commit()
        return UploadResult(200, payload)

    if confirmed is None:
        # A2.5: only fresh admissions re-check; a token-confirmed variant may share the hash.
        await _reject_if_concurrent_variant(
            db,
            sink,
            user_id=user_id,
            audio_id=audio_id,
            attempt_id=attempt_id,
            identity=identity,
            sha256_hex=sha256_hex,
        )

    try:
        admission = await sink.admit()
    except SinkError as exc:
        # La identidad YA se confirmó: compensarla para no dejar identidad sin contenido
        # admitido, y descartar el temporal (§8.1). El `503` invita a reintentar dentro de
        # la ventana; `exclude_audio_id` hace idempotente ese reintento.
        await _restore_identity(
            db, user_id=user_id, audio_id=audio_id, attempt_id=attempt_id, previous=previous
        )
        await _discard_lenient(sink, "api_error")
        raise _sink_api_error(exc) from exc

    return UploadResult(
        202,
        {"attempt_id": str(attempt_id), "status": admission.status, "received_bytes": received},
    )
