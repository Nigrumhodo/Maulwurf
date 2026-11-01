"""U-S2-AN-18: contract conformance of A1.8/A2.3/A2.4 (A2.19).

One contract test per item of `docs/plan/S2.md` §A2.19:

1. reservation deadlines come from the published capabilities TTL (`receive_deadline` and
   `asr_deadline` are no longer always NULL);
2. the language allowlist is compared case-insensitively, so a published mixed-case code
   can match the normalized request;
3. every unusable variant token (unknown, expired, consumed or mismatched) gets the same
   opaque answer, and the upload routes advertise only documented codes;
4. `GET /me` is profile + CSRF and `PATCH /me` only accepts the frozen `timezone` field;
5. `MAULWURF_UPLOAD_MAX_BYTES` and `INGEST_MAX_UPLOAD_BYTES` are one limit: startup fails
   when they disagree;
6. a capacity `503` carries `Retry-After` and never contradicts it with `retryable=false`.

Nothing here touches PostgreSQL, Redis or a provider.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.config import settings as live_settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, CSRF_HEADER, current_session
from app.core.errors import ApiError, install_error_handlers, variant_hash_mismatch
from app.main import app
from app.models import IngestionAttempt, User, VariantConfirmationToken
from app.models import Session as AuthSession
from app.routers import me
from app.routers.audios import AudioCreate
from app.schemas.ingestion import IngestionCapabilities
from app.services import audios as audios_service
from app.services import sessions
from app.services import uploads as uploads_service
from app.services.uploads import map_ingest_code

_SECRETS: dict[str, Any] = {
    "secret_key": "test-secret",
    "encryption_key": "test-encryption",
    "oauth_state_secret": "test-oauth-state",
}
_CLASS_DATE = date(2026, 11, 1)
_USER_ID = uuid.uuid4()
_SUBJECT_ID = uuid.uuid4()
COOKIE = "u-s2-an-18-cookie"
CSRF = sessions.csrf_token_for(COOKIE)
ORIGIN = live_settings.allowed_origin


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**_SECRETS, **overrides})


def _published(**overrides: Any) -> IngestionCapabilities:
    """Complete spike publication: `limits_validated=true` requires every effective limit."""
    values: dict[str, Any] = {
        "source_report": "docs/spike/F0.1-informe-riva.md",
        "source_version": "test-2026-11-01",
        "limits_validated": True,
        "max_upload_bytes": 960_044,
        "max_duration_seconds": 30,
        "accepted_input_formats": ("wav",),
        "languages": ("es",),
        "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
        "active_slots": 2,
    }
    values.update(overrides)
    return IngestionCapabilities.model_validate(values)


class _RecordingDb:
    """Minimal session for `create_reservation`: records rows and assigns their UUIDs."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        for row in self.rows:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    def of(self, kind: type) -> Any:
        return next(row for row in self.rows if isinstance(row, kind))


# --- 1. TTL: the published capabilities decide the reservation deadlines -----------------


def test_reservation_deadlines_follow_the_published_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audios_service,
        "settings",
        _settings(ingestion_capabilities=_published(), upload_ttl_minutes=15),
    )
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    deadlines = audios_service.attempt_deadlines(now)

    # Chained ephemeral budget: upload → receive → ASR, each phase bounded by its own TTL.
    assert deadlines.upload_expires_at == now + timedelta(minutes=10)
    assert deadlines.receive_deadline == now + timedelta(minutes=40)
    assert deadlines.asr_deadline == now + timedelta(minutes=100)


async def test_new_attempt_persists_the_published_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audios_service, "settings", _settings(ingestion_capabilities=_published())
    )
    db = _RecordingDb()

    reservation = await audios_service.create_reservation(
        db,  # type: ignore[arg-type]
        user_id=_USER_ID,
        subject_id=_SUBJECT_ID,
        title=None,
        teacher=None,
        class_date=_CLASS_DATE,
        class_timezone="America/Bogota",
        language_code="es",
        privacy_notice_version="2026-11-v1",
    )

    attempt = db.of(IngestionAttempt)
    assert attempt.upload_expires_at == reservation.upload_expires_at
    assert attempt.receive_deadline == reservation.upload_expires_at + timedelta(minutes=30)
    assert attempt.asr_deadline == attempt.receive_deadline + timedelta(minutes=60)


def test_without_a_publication_no_receive_or_asr_deadline_is_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # D4/D6 are blocked: the S1 provisional window has no approved receive/ASR TTL.
    monkeypatch.setattr(
        audios_service, "settings", _settings(upload_ttl_minutes=15)
    )
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    deadlines = audios_service.attempt_deadlines(now)

    assert deadlines.upload_expires_at == now + timedelta(minutes=15)
    assert deadlines.receive_deadline is None
    assert deadlines.asr_deadline is None


# --- 2. Language: normalization applies to both sides of the comparison ------------------


def test_language_allowlist_is_compared_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audios_service,
        "settings",
        _settings(ingestion_capabilities=_published(languages=("ES", "pt-BR"))),
    )

    allowed = audios_service.allowed_languages()

    assert allowed == ("es", "pt-br")
    body = AudioCreate(
        subject_id=_SUBJECT_ID,
        class_date=_CLASS_DATE,
        class_timezone="America/Bogota",
        language_code="ES",
        privacy_notice_version="2026-11-v1",
        cloud_processing_accepted=True,
        third_party_voice_acknowledged=True,
    )
    # The router normalizes the request (BCP-47 is case-insensitive), so a published
    # mixed-case code can never be unmatched again.
    assert body.language_code == "es"
    assert body.language_code in allowed


# --- 3. Variant token: one opaque rejection and only documented codes -------------------


class _Rows:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _StubDb:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    async def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        return _Rows(self.row)


def _token(**overrides: Any) -> VariantConfirmationToken:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": _USER_ID,
        "token_hash": "c" * 64,
        "canonical_audio_id": uuid.uuid4(),
        "content_sha256": "a" * 64,
        "subject_id": _SUBJECT_ID,
        "class_date": _CLASS_DATE,
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "metadata_digest": "b" * 64,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(overrides)
    return VariantConfirmationToken(**values)


async def _consume(db: _StubDb) -> ApiError:
    with pytest.raises(ApiError) as excinfo:
        await audios_service.consume_variant_token(
            db,  # type: ignore[arg-type]
            user_id=_USER_ID,
            presented="opaque-token",
            subject_id=_SUBJECT_ID,
            class_date=_CLASS_DATE,
            class_timezone="America/Bogota",
            language_code="es",
        )
    return excinfo.value


@pytest.mark.parametrize(
    "token",
    [
        None,
        _token(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        _token(consumed_at=datetime.now(UTC), reserved_audio_id=uuid.uuid4()),
        _token(subject_id=uuid.uuid4()),
    ],
    ids=["unknown", "expired", "consumed", "mismatched"],
)
async def test_every_unusable_variant_token_gets_the_same_opaque_422(
    token: VariantConfirmationToken | None,
) -> None:
    error = await _consume(_StubDb(token))

    # Unknown, foreign, expired, consumed and mismatched tokens share one rejection so the
    # answer never reveals existence or state (ADR-0006); no 409 is advertised for them.
    assert (error.status_code, error.code) == (422, "validation_failed")
    assert "variant_confirmation_token" in error.details["fields"]


async def test_losing_the_consumption_race_keeps_the_same_opaque_422() -> None:
    # The compare-and-set matches no row: another request consumed the token first. The
    # state is not disclosed; the answer is identical to every other unusable token. The
    # private helper is called directly because losing this race needs no database.
    with pytest.raises(ApiError) as excinfo:
        await audios_service._mark_token_consumed(  # noqa: SLF001
            _StubDb(None),  # type: ignore[arg-type]
            token=_token(),
            audio_id=uuid.uuid4(),
            at=datetime.now(UTC),
        )

    error = excinfo.value
    assert (error.status_code, error.code) == (422, "validation_failed")
    assert "variant_confirmation_token" in error.details["fields"]


def test_upload_routes_advertise_only_the_documented_token_answers() -> None:
    paths = app.openapi()["paths"]

    post = paths["/audios"]["post"]["responses"]
    assert "409" not in post  # a consumed token is validation_failed, not invalid_transition
    assert "validation_failed" in post["422"]["description"]
    put = paths["/audios/{audio_id}/content"]["put"]["responses"]
    assert "variant_hash_mismatch" in put["422"]["description"]
    error = variant_hash_mismatch()
    assert (error.status_code, error.code) == (422, "variant_hash_mismatch")


# --- 4. /me: profile + CSRF, integrations live in their own route -----------------------


class _UserDb:
    def __init__(self, user: User) -> None:
        self.user = user
        self.commits = 0

    async def get(self, model: type, key: Any) -> Any:
        assert model is User
        return self.user

    async def commit(self) -> None:
        self.commits += 1


def _auth_session(user: User) -> AuthSession:
    return AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=sessions._sha256(COOKIE),
        csrf_hash=sessions._sha256(CSRF),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _me_app(user: User) -> tuple[FastAPI, _UserDb]:
    db = _UserDb(user)
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(me.router)
    session_row = _auth_session(user)

    async def fake_session() -> AuthSession:
        return session_row

    async def fake_db() -> AsyncIterator[_UserDb]:
        yield db

    application.dependency_overrides[current_session] = fake_session
    application.dependency_overrides[get_session] = fake_db
    return application, db


def _user() -> User:
    return User(
        id=_USER_ID,
        google_sub="google-sub",
        email="student@uni.edu",
        display_name="Ada",
        timezone="UTC",
    )


async def _me_client(application: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=application),
        base_url=ORIGIN,
        cookies={COOKIE_NAME: COOKIE},
    )


async def test_get_me_returns_the_profile_and_the_csrf_bootstrap_only() -> None:
    application, _ = _me_app(_user())

    async with await _me_client(application) as client:
        response = await client.get("/me")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    # §3.3: integrations are served by GET /integrations/status, never embedded here.
    assert set(response.json()) == {"id", "email", "display_name", "timezone", "csrf_token"}
    assert response.json()["csrf_token"] == CSRF


async def test_patch_me_only_accepts_the_frozen_timezone_field() -> None:
    application, db = _me_app(_user())
    headers = {"Origin": ORIGIN, CSRF_HEADER: CSRF}

    async with await _me_client(application) as client:
        accepted = await client.patch(
            "/me", json={"timezone": "America/Bogota"}, headers=headers
        )
        rejected = await client.patch("/me", json={"display_name": "Grace"}, headers=headers)

    assert accepted.status_code == 200
    assert accepted.json()["timezone"] == "America/Bogota"
    assert db.commits == 1
    # `display_name` is read-only (it comes from the Google profile); A1.8 froze timezone.
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "validation_failed"
    assert "display_name" in rejected.json()["error"]["details"]["fields"]


# --- 5. One upload limit: the settings fail at startup when the two names disagree ------


def test_upload_limit_has_a_single_source() -> None:
    agreed = Settings(
        _env_file=None, **{**_SECRETS, "INGEST_MAX_UPLOAD_BYTES": 209_715_200}
    )
    assert agreed.ingest_max_upload_bytes == agreed.upload_max_bytes
    assert _settings().ingest_max_upload_bytes is None

    with pytest.raises(ValidationError, match="INGEST_MAX_UPLOAD_BYTES"):
        Settings(
            _env_file=None,
            **{**_SECRETS, "upload_max_bytes": 1_024, "INGEST_MAX_UPLOAD_BYTES": 2_048},
        )


def test_the_api_cut_uses_the_single_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = _settings(
        upload_max_bytes=123_456, **{"INGEST_MAX_UPLOAD_BYTES": 123_456}
    )
    monkeypatch.setattr(uploads_service, "settings", configured)

    assert uploads_service.effective_upload_limit() == 123_456


# --- 6. Capacity 503: Retry-After is the retry signal, with no contradiction ------------


@pytest.mark.parametrize(
    "code", ["capacity_unavailable", "probe_unavailable", "wrong_instance"]
)
def test_capacity_503_never_contradicts_its_retry_after(code: str) -> None:
    error = map_ingest_code(code)

    assert error.status_code == 503
    assert error.code == "capacity_unavailable"
    assert error.headers["Retry-After"]
    # A Retry-After says «come back later»; `retryable=false` said the opposite (§3.1).
    assert "retryable" not in error.details
