"""Sesión opaca y token CSRF (A1.4, S1.md §1.1).

- La cookie `mw_session` lleva 32 bytes aleatorios; la BD guarda solo su SHA-256.
- El token CSRF se deriva de la cookie con HMAC(secret_key). Así `GET /me` puede devolverlo
  en claro en cada lectura sin guardarlo: la BD conserva solo `csrf_hash`. Cambia al crear o
  rotar la sesión, es el mismo para todas las pestañas y muere con la sesión.
- Toda comparación de secretos es en tiempo constante y sobre bytes: `compare_digest` con
  `str` lanza `TypeError` ante caracteres no ASCII, y un header manipulado debe acabar en
  403, no en 500.
- Rotar `MAULWURF_SECRET_KEY` deja las sesiones vivas sin CSRF válido (solo lectura hasta
  un nuevo login). Es fail-closed a propósito: no relajar la comparación para "arreglarlo".
"""
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import set_current_user
from app.core.errors import ApiError
from app.models import Session, User

TOKEN_BYTES = 32


@dataclass(frozen=True)
class IssuedSession:
    """Valores planos que solo viajan al navegador; nunca se persisten ni se loguean."""

    session: Session
    cookie_token: str
    csrf_token: str


def _bytes(value: str) -> bytes:
    return value.encode("utf-8", "surrogatepass")


def _sha256(value: str) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def csrf_token_for(cookie_token: str) -> str:
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, f"csrf:{cookie_token}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, user_id: uuid.UUID) -> IssuedSession:
    cookie_token = secrets.token_urlsafe(TOKEN_BYTES)
    csrf_token = csrf_token_for(cookie_token)
    # The RLS policy on `sessions` checks the row's user_id. Binding here keeps the
    # service correct for any caller (login, rotation) instead of relying on the route.
    await set_current_user(db, user_id)
    session = Session(
        user_id=user_id,
        token_hash=_sha256(cookie_token),
        csrf_hash=_sha256(csrf_token),
        expires_at=_now() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    await db.flush()
    return IssuedSession(session, cookie_token, csrf_token)


async def resolve_session(db: AsyncSession, cookie_token: str | None) -> Session | None:
    """Sesión vigente para la cookie; `None` si falta, es desconocida, expiró o fue revocada.

    Solo lectura: validar una petición no escribe en BD.

    Under RLS (A2.17) the request does not know its user yet. The bootstrap policy exposes
    only the row whose `token_hash` matches the presented opaque token; that hash is set
    transactionally before the lookup. Once the row is found, its user_id becomes the
    tenant of the remaining transactions in the request.
    """
    if not cookie_token:
        return None
    token_hash = _sha256(cookie_token)
    await db.execute(
        text("SELECT set_config('app.session_token_hash', :token_hash, true)"),
        {"token_hash": token_hash},
    )
    session: Session | None = await db.scalar(
        select(Session).where(
            Session.token_hash == token_hash,
            Session.revoked_at.is_(None),
            Session.expires_at > _now(),
        )
    )
    if session is None:
        return None
    await set_current_user(db, session.user_id)
    # The join with `users` used to reject an account in deletion in the same query. RLS
    # needs the user_id first, so the status check is a second, equally fail-closed read.
    status = await db.scalar(select(User.status).where(User.id == session.user_id))
    return session if status == "active" else None


async def revoke_session(db: AsyncSession, session: Session) -> bool:
    """Revocación inmediata; `False` si otra petición ya la había revocado.

    Idempotente para logout. La siguiente petición con esa cookie recibe 401.
    """
    # Rotation and the login callback may revoke a session captured before the tenant of
    # this request was bound; bind it explicitly so the RLS WITH CHECK also passes.
    await set_current_user(db, session.user_id)
    result = await db.execute(
        update(Session)
        .where(Session.id == session.id, Session.revoked_at.is_(None))
        .values(revoked_at=_now())
        .returning(Session.revoked_at)
    )
    revoked_at = result.scalar_one_or_none()
    session.revoked_at = revoked_at or session.revoked_at or _now()
    return revoked_at is not None


async def rotate_session(db: AsyncSession, session: Session) -> IssuedSession:
    """Nueva cookie y nuevo CSRF en la misma transacción; el par anterior deja de valer.

    Si dos peticiones rotan la misma sesión a la vez, solo la que revoca crea sucesor; la
    otra recibe 409 y el cliente repite `GET /me` con la cookie nueva.
    """
    if not await revoke_session(db, session):
        raise ApiError(409, "session_conflict", "La sesión ya fue rotada o revocada.")
    return await create_session(db, session.user_id)


def csrf_matches(session: Session, cookie_token: str, presented: str | None) -> bool:
    """El token presentado debe ser el derivado de ESTA cookie y coincidir con `csrf_hash`."""
    if not presented:
        return False
    expected = csrf_token_for(cookie_token)
    return hmac.compare_digest(_bytes(presented), _bytes(expected)) and hmac.compare_digest(
        _bytes(_sha256(presented)), _bytes(session.csrf_hash)
    )
