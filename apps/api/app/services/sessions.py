"""Sesión opaca y token CSRF (A1.4, S1.md §1.1).

- La cookie `mw_session` lleva 32 bytes aleatorios; la BD guarda solo su SHA-256.
- El token CSRF se deriva de la cookie con HMAC(secret_key). Así `GET /me` puede devolverlo
  en claro en cada lectura sin guardarlo: la BD conserva solo `csrf_hash`. Cambia al crear o
  rotar la sesión, es el mismo para todas las pestañas y muere con la sesión.
- Toda comparación de secretos es en tiempo constante.
"""
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Session

TOKEN_BYTES = 32


@dataclass(frozen=True)
class IssuedSession:
    """Valores planos que solo viajan al navegador; nunca se persisten ni se loguean."""

    session: Session
    cookie_token: str
    csrf_token: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token_for(cookie_token: str) -> str:
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, f"csrf:{cookie_token}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def create_session(db: AsyncSession, user_id: uuid.UUID) -> IssuedSession:
    cookie_token = secrets.token_urlsafe(TOKEN_BYTES)
    csrf_token = csrf_token_for(cookie_token)
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
    """
    if not cookie_token:
        return None
    session = await db.scalar(
        select(Session).where(
            Session.token_hash == _sha256(cookie_token),
            Session.revoked_at.is_(None),
            Session.expires_at > _now(),
        )
    )
    return session


async def revoke_session(db: AsyncSession, session: Session) -> None:
    """Revocación inmediata: la siguiente petición con esa cookie recibe 401."""
    await db.execute(
        update(Session)
        .where(Session.id == session.id, Session.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    session.revoked_at = session.revoked_at or _now()


async def rotate_session(db: AsyncSession, session: Session) -> IssuedSession:
    """Nueva cookie y nuevo CSRF en la misma transacción; el par anterior deja de valer."""
    await revoke_session(db, session)
    return await create_session(db, session.user_id)


def csrf_matches(session: Session, cookie_token: str, presented: str | None) -> bool:
    """El token presentado debe ser el derivado de ESTA cookie y coincidir con `csrf_hash`."""
    if not presented:
        return False
    expected = csrf_token_for(cookie_token)
    return hmac.compare_digest(presented, expected) and hmac.compare_digest(
        _sha256(presented), session.csrf_hash
    )
