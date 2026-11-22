"""W1 defensive remediations: deletion, RLS startup, key rotation, OAuth cookies, OpenAPI, logs."""
import logging
import time
import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core import crypto, db
from app.core.config import Settings, redact_dsn, settings
from app.core.deps import (
    OAUTH_CALENDAR_COOKIE,
    OAUTH_COOKIE_NAME,
    OAUTH_LOGIN_COOKIE,
)
from app.core.errors import _handle_validation
from app.core.logging import REDACTED, RedactionFilter, scrub_text
from app.core.security import sign_state
from app.main import app
from app.models import GoogleCredential, User
from app.routers.auth import oauth_callback_payload
from app.services import deletion, oauth_google
from app.services.oauth_google import GoogleIdClaims, GoogleTokenResponse, _store_tokens

_SECRETS = {
    "secret_key": "s2-test-secret-key-0123456789abcdef",
    "encryption_key": "s2-test-encryption-key-0123456789ab",
    "oauth_state_secret": "s2-test-oauth-state-secret-0123456789",
}
_REFRESH = "1//refresh-token-retained"
_FIELD: crypto.CryptoField = "google_credentials.refresh_token"


def test_notification_is_purged_before_task() -> None:
    names = [model.__name__ for model in deletion._PURGE_USER_MODELS]
    assert "Notification" in names
    assert names.index("Notification") < names.index("Task")


class _RoleConnection:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row
        self.statements: list[str] = []

    async def execute(self, statement: object) -> "_RoleResult":
        self.statements.append(str(statement))
        return _RoleResult(self.row)

    async def __aenter__(self) -> "_RoleConnection":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _RoleResult:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row

    def one(self) -> tuple[object, ...]:
        return self.row


class _RoleEngine:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.connection = _RoleConnection(row)

    def connect(self) -> _RoleConnection:
        return self.connection


async def _check(row: tuple[object, ...]) -> _RoleEngine:
    engine = _RoleEngine(row)
    await db.verify_db_role_least_privilege(cast(AsyncEngine, engine))
    return engine


async def test_superuser_login_role_fails_closed_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    with pytest.raises(RuntimeError, match="rolsuper=True"):
        await _check(("maulwurf", True, False))


async def test_bypassrls_login_role_fails_closed_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "staging")
    with pytest.raises(RuntimeError, match="rolbypassrls=True"):
        await _check(("maulwurf_owner", False, True))


async def test_least_privilege_login_role_is_accepted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    with caplog.at_level(logging.WARNING, logger="app.core.db"):
        engine = await _check(("maulwurf_app_login", False, False))
    assert engine.connection.statements[0] == "RESET ROLE"
    assert "rolsuper" in engine.connection.statements[1]
    assert "rolbypassrls" in engine.connection.statements[1]
    assert caplog.records == []


async def test_local_superuser_is_allowed_with_a_structured_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "env", "local")
    with caplog.at_level(logging.WARNING, logger="app.core.db"):
        await _check(("maulwurf", True, False))
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert "rolsuper=True" in record.getMessage()
    fields = getattr(record, "fields", {})
    assert fields["env"] == "local"
    assert fields["rolsuper"] is True
    assert fields["rolbypassrls"] is False


def test_privilege_check_runs_only_outside_local_and_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert db.runtime_privilege_check_enabled() is True
    monkeypatch.setattr(settings, "env", "local")
    assert db.runtime_privilege_check_enabled() is False
    monkeypatch.setattr(settings, "env", "ci")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "unit")
    assert db.runtime_privilege_check_enabled() is False


async def test_lifespan_checks_the_role_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    async def _fake(bound: object) -> None:
        seen.append(bound)

    monkeypatch.setattr("app.main.verify_db_role_least_privilege", _fake)
    monkeypatch.setattr("app.main.close_llm_http_client", AsyncMock())
    monkeypatch.setattr("app.main.close_ingest_client", AsyncMock())
    # AsyncEngine.dispose is read-only on the instance (slotted engine).
    monkeypatch.setattr(type(db.engine), "dispose", AsyncMock())

    monkeypatch.setattr(settings, "env", "local")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    async with app.router.lifespan_context(app):
        pass
    assert seen == []

    monkeypatch.setattr(settings, "env", "staging")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "unit")
    async with app.router.lifespan_context(app):
        pass
    assert seen == []

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    async with app.router.lifespan_context(app):
        pass
    assert seen == [db.engine]


def _use_key(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secret: str,
    version: int,
    previous: str | None = None,
    previous_version: int | None = None,
) -> None:
    monkeypatch.setattr(settings, "encryption_key", SecretStr(secret))
    monkeypatch.setattr(settings, "encryption_key_version", version)
    monkeypatch.setattr(
        settings,
        "previous_encryption_key",
        None if previous is None else SecretStr(previous),
    )
    monkeypatch.setattr(settings, "previous_encryption_key_version", previous_version)


def test_previous_key_decrypts_only_its_own_version(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    _use_key(monkeypatch, secret="previous-key-value", version=1)
    blob, version = crypto.encrypt(_REFRESH, user_id=user_id, field=_FIELD)
    assert version == 1

    _use_key(
        monkeypatch,
        secret="current-key-value",
        version=2,
        previous="previous-key-value",
        previous_version=1,
    )
    assert crypto.decrypt(blob, key_version=1, user_id=user_id, field=_FIELD) == _REFRESH
    with pytest.raises(crypto.DecryptionError, match="key_version"):
        crypto.decrypt(blob, key_version=3, user_id=user_id, field=_FIELD)


def _credential(user_id: uuid.UUID, blob: bytes, version: int) -> GoogleCredential:
    return GoogleCredential(
        user_id=user_id,
        access_token_enc=b"old-access",
        refresh_token_enc=blob,
        key_version=version,
        scopes=["openid"],
        status="connected",
    )


def test_store_tokens_reencrypts_a_retained_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    _use_key(monkeypatch, secret="previous-key-value", version=1)
    blob, version = crypto.encrypt(_REFRESH, user_id=user_id, field=_FIELD)
    _use_key(
        monkeypatch,
        secret="current-key-value",
        version=2,
        previous="previous-key-value",
        previous_version=1,
    )
    user = User(id=user_id, google_sub="sub", email="a@example.test", status="active")
    credential = _credential(user_id, blob, version)

    class _Db:
        def add(self, _obj: object) -> None:
            return None

    stored = _store_tokens(
        cast(AsyncSession, _Db()),
        user,
        GoogleTokenResponse(access_token="new-access", refresh_token=None),
        ["openid"],
        credential,
    )

    assert stored.key_version == 2
    assert stored.refresh_token_enc != blob
    assert (
        crypto.decrypt(
            bytes(stored.refresh_token_enc or b""),
            key_version=2,
            user_id=user_id,
            field=_FIELD,
        )
        == _REFRESH
    )


async def test_relogin_reencrypts_a_retained_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    _use_key(monkeypatch, secret="previous-key-value", version=1)
    blob, version = crypto.encrypt(_REFRESH, user_id=user_id, field=_FIELD)
    _use_key(
        monkeypatch,
        secret="current-key-value",
        version=2,
        previous="previous-key-value",
        previous_version=1,
    )
    user = User(id=user_id, google_sub="sub-rotate", email="a@example.test", status="active")
    credential = _credential(user_id, blob, version)

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(oauth_google, "bind_google_sub", _noop)
    monkeypatch.setattr(oauth_google, "set_current_user", _noop)

    class _Db:
        async def scalar(self, _stmt: object) -> User:
            return user

        async def get(self, _model: object, _key: object) -> GoogleCredential:
            return credential

        def add(self, _obj: object) -> None:
            return None

        async def flush(self) -> None:
            return None

    await oauth_google.upsert_google_identity(
        cast(AsyncSession, _Db()),
        GoogleIdClaims(sub="sub-rotate", email="a@example.test", email_verified=True),
        GoogleTokenResponse(access_token="new-access", refresh_token=None),
    )

    assert credential.key_version == 2
    assert (
        crypto.decrypt(
            bytes(credential.refresh_token_enc or b""),
            key_version=credential.key_version,
            user_id=user_id,
            field=_FIELD,
        )
        == _REFRESH
    )


def _request(cookies: dict[str, str]) -> Request:
    header = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/google/callback",
            "headers": [(b"cookie", header.encode())],
            "query_string": b"",
        }
    )


def _signed(state: str, **extra: object) -> str:
    return sign_state(
        {"state": state, "nonce": "n", "code_verifier": "v", "exp": int(time.time()) + 600, **extra}
    )


def test_callback_selects_the_flow_cookie_that_matches_state() -> None:
    login = _signed("login-state")
    calendar = _signed("calendar-state", mode="connect", user_id="user-1")
    request = _request({OAUTH_LOGIN_COOKIE: login, OAUTH_CALENDAR_COOKIE: calendar})

    login_payload = oauth_callback_payload(request, "login-state")
    calendar_payload = oauth_callback_payload(request, "calendar-state")

    assert login_payload is not None and login_payload.get("mode") is None
    assert calendar_payload is not None and calendar_payload.get("mode") == "connect"
    assert oauth_callback_payload(request, "other-state") is None


def test_callback_still_accepts_the_legacy_cookie() -> None:
    legacy = _signed("legacy-state", mode="gmail_connect", user_id="user-1")
    payload = oauth_callback_payload(_request({OAUTH_COOKIE_NAME: legacy}), "legacy-state")
    assert payload is not None
    assert payload.get("mode") == "gmail_connect"


def test_previous_encryption_key_is_a_pair() -> None:
    with pytest.raises(ValidationError, match="van juntos"):
        Settings(_env_file=None, **_SECRETS, previous_encryption_key="old-key-value")
    with pytest.raises(ValidationError, match="distinto"):
        Settings(
            _env_file=None,
            **_SECRETS,
            previous_encryption_key="old-key-value-0123456789abcdef",
            previous_encryption_key_version=1,
        )


def test_driver_urls_hide_passwords_except_in_the_operational_string() -> None:
    configured = Settings(
        _env_file=None,
        **_SECRETS,
        database_url="postgresql+asyncpg://mw:Sup3rSecretPw@db.internal:5432/maulwurf",
        redis_url="redis://:Sup3rSecretPw@cache.internal:6379/0",
    )
    rendered = (
        f"{configured!r} {configured} {configured.database_url!r} {configured.redis_url!r} "
        f"{configured.model_dump()} {configured.model_dump_json()}"
    )
    assert "Sup3rSecretPw" not in rendered
    assert "Sup3rSecretPw" in str(configured.database_url)
    assert "Sup3rSecretPw" in str(configured.redis_url)
    assert redact_dsn(str(configured.redis_url)) == "redis://***@cache.internal:6379/0"
    assert "Sup3rSecretPw" not in scrub_text(
        f"connect {configured.database_url} cache {configured.redis_url}"
    )
    assert f"{REDACTED}@" in scrub_text(str(configured.database_url))


def test_openapi_declares_session_security_and_oauth_redirects() -> None:
    app.openapi_schema = None
    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["sessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "mw_session",
        "description": schemes["sessionCookie"]["description"],
    }
    assert schemes["csrfHeader"]["in"] == "header"
    assert schemes["csrfHeader"]["name"] == "X-CSRF-Token"

    public = {
        "/healthz",
        "/readyz",
        "/auth/google/start",
        "/auth/google/callback",
        "/internal/metrics",
    }
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if path in public:
                assert "security" not in operation
                continue
            assert operation["security"]
            assert "401" in operation["responses"]
            if method in {"post", "put", "patch", "delete"}:
                assert "403" in operation["responses"]
                assert operation["security"] == [{"sessionCookie": [], "csrfHeader": []}]
            else:
                assert operation["security"] == [{"sessionCookie": []}]

    assert "302" in schema["paths"]["/auth/google/start"]["get"]["responses"]
    assert "302" in schema["paths"]["/auth/google/callback"]["get"]["responses"]
    for path in (
        "/integrations/google/connect",
        "/integrations/google/reconnect",
        "/integrations/google/gmail/connect",
    ):
        assert "302" in schema["paths"][path]["get"]["responses"]


async def test_uuid_parsing_does_not_echo_any_input_character() -> None:
    marker = "Q"
    exc = RequestValidationError(
        [
            {
                "loc": ("query", "audio_id"),
                "msg": f"Input should be a valid UUID, invalid character: found `{marker}` at 1",
                "type": "uuid_parsing",
                "input": f"{marker}-SECRETVALUE",
            }
        ]
    )
    response = await _handle_validation(Request({"type": "http"}), exc)
    body = bytes(response.body).decode()
    assert marker not in body
    assert "SECRETVALUE" not in body
    assert "identificador inválido" in body


def test_uvicorn_access_record_redacts_oauth_query_secrets() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:9",
            "GET",
            "/auth/google/callback?code=SECRET&state=STATE&id_token=IDTOK&access_token=ATOK"
            "&refresh_token=RTOK",
            "1.1",
            302,
        ),
        exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    assert isinstance(record.args, tuple) and len(record.args) == 5
    rendered = record.getMessage()
    for secret in ("SECRET", "STATE", "IDTOK", "ATOK", "RTOK"):
        assert secret not in rendered
        assert secret not in str(record.args[2])
    assert "code=[REDACTED]" in rendered
    assert "state=[REDACTED]" in rendered


def test_application_record_redacts_oauth_query_in_msg_and_args() -> None:
    record = logging.LogRecord(
        name="app.routers.auth",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="callback %s",
        args=("/auth/google/callback?code=SECRET&state=STATE",),
        exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    assert record.args == ()
    assert "SECRET" not in str(record.msg)
    assert "code=[REDACTED]" in str(record.msg)
    assert "state=[REDACTED]" in str(record.msg)
