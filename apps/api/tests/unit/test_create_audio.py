"""U-S2-AN-01: validación previa a bytes de `POST /audios` (A2.3, sin Postgres).

Un stub de sesión registra cada `add`/`flush`/`commit`, identifica las consultas que sí se
emiten y falla con cualquier otra. Así se prueba la promesa del ticket —**un rechazo llega
antes de reservar capacidad**— y que el consumo del token comparte transacción con la
reserva, sin necesitar una base de datos. Sin marker `integration`: corre en `python-unit`.
"""

import hashlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.config import settings as live_settings
from app.core.db import get_session
from app.core.deps import COOKIE_NAME, CSRF_HEADER
from app.main import app
from app.models import Session as AuthSession
from app.models import Subject, VariantConfirmationToken
from app.routers import audios as audios_router
from app.services import audios as audios_service
from app.services import sessions

_SECRETS: dict[str, Any] = {
    "secret_key": "test-secret",
    "encryption_key": "test-encryption",
    "oauth_state_secret": "test-oauth-state",
}
# El stub responde sin mirar el `WHERE`: el ID es solo la forma que exige el contrato.
_SUBJECT_ID = uuid.uuid4()
COOKIE = "u-s2-an-01-cookie"
CSRF = sessions.csrf_token_for(COOKIE)
PRESENTED_TOKEN = "u-s2-an-01-token"
TOKEN_HASH = hashlib.sha256(PRESENTED_TOKEN.encode("utf-8", "surrogatepass")).hexdigest()

client = TestClient(app)

# Consultas que solo corren si la petición llegó a mirar cupo o a consumir el token.
_CAPACITY_QUERIES = frozenset({"sql:count_quota", "sql:count_slot", "sql:consume_token"})


class _Rows:
    """Resultado mínimo de `execute()`: solo lo que la aplicación llega a leer."""

    def __init__(self, *values: Any) -> None:
        self._values = values

    def scalar_one_or_none(self) -> Any:
        if len(self._values) > 1:
            raise AssertionError(f"esperaba una fila como mucho y llegaron {len(self._values)}")
        return self._values[0] if self._values else None


class StubSession:
    """`AsyncSession` de mentira: registra escrituras y falla con consultas no preparadas."""

    def __init__(
        self,
        *,
        session_row: AuthSession,
        subject: Subject | None,
        token: VariantConfirmationToken | None = None,
        active: int = 0,
        token_still_free: bool = True,
    ) -> None:
        self.session_row = session_row
        self.subject = subject
        self.token = token
        self.active = active  # intentos activos que «ve» el contador, cuota o slot
        self.token_still_free = token_still_free
        self.added = 0
        self.flushes = 0
        self.commits = 0
        self.token_updates = 0
        self.events: list[str] = []
        self._pending: list[Any] = []

    @property
    def writes(self) -> int:
        return self.added + self.flushes + self.commits

    def add(self, obj: Any) -> None:
        self.added += 1
        self.events.append(f"add:{type(obj).__name__}")
        self._pending.append(obj)

    async def flush(self) -> None:
        self.flushes += 1
        self.events.append("flush")
        # `uuid_pk` es `server_default=gen_random_uuid()`: sin BD, el stub reparte el PK.
        for obj in self._pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        self._pending.clear()

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Rows:
        sql = str(statement)
        if sql.startswith("UPDATE"):
            self.token_updates += 1
            self.events.append("sql:consume_token")
            assert self.token is not None
            return _Rows(self.token.id if self.token_still_free else None)
        if "FROM sessions" in sql:
            self.events.append("sql:resolve_session")
            return _Rows(self.session_row)
        if "FROM subjects" in sql:
            self.events.append("sql:subject")
            return _Rows(self.subject)
        if "FROM variant_confirmation_tokens" in sql:
            self.events.append("sql:lock_token")
            return _Rows(self.token)
        if "FROM ingestion_attempts" in sql:
            # Cuota (por usuario) y slot (global) comparten tabla: se separan por el filtro.
            self.events.append("sql:count_quota" if "user_id" in sql else "sql:count_slot")
            return _Rows(self.active)
        raise AssertionError(f"consulta no preparada en el stub: {sql}")

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return (await self.execute(statement, *args, **kwargs)).scalar_one_or_none()


def _published(**overrides: Any) -> dict[str, Any]:
    """Publicación completa del spike (A2.2): `limits_validated=true` exige todos los datos."""
    capabilities: dict[str, Any] = {
        "source_report": "docs/spike/F0.1-informe-riva.md",
        "source_version": "test-2026-10-07",
        "limits_validated": True,
        "max_upload_bytes": 960044,
        "max_duration_seconds": 30,
        "accepted_input_formats": ["wav"],
        "languages": ["es"],
        "language_policy": "explicit_select_required_no_multi",
        "ttl": {"upload_start_minutes": 10, "receive_minutes": 30, "asr_minutes": 60},
        "asr": {
            "provider": "nvidia-riva",
            "model": "whisper-large-v3",
            "timestamp_precision": "none",
        },
        "active_slots": 2,
        "required_stages": ["index"],
    }
    capabilities.update(overrides)
    return capabilities


def _use_settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """Aplica `Settings` nuevo: el router y el servicio cada uno lee su propio `settings`."""
    configured = Settings(_env_file=None, **{**_SECRETS, **overrides})
    monkeypatch.setattr(audios_router, "settings", configured)
    monkeypatch.setattr(audios_service, "settings", configured)


def _session_row(user_id: uuid.UUID) -> AuthSession:
    return AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=sessions._sha256(COOKIE),
        csrf_hash=sessions._sha256(CSRF),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _token(user_id: uuid.UUID, **overrides: Any) -> VariantConfirmationToken:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "token_hash": TOKEN_HASH,
        "canonical_audio_id": uuid.uuid4(),
        "content_sha256": "a" * 64,
        "subject_id": _SUBJECT_ID,
        "class_date": date(2026, 9, 21),
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "metadata_digest": "b" * 64,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(overrides)
    return VariantConfirmationToken(**values)


def _stub(**overrides: Any) -> StubSession:
    user_id = overrides.pop("user_id", uuid.uuid4())
    defaults: dict[str, Any] = {
        "session_row": _session_row(user_id),
        "subject": Subject(id=uuid.uuid4(), user_id=user_id, name="Cálculo I"),
    }
    return StubSession(**{**defaults, **overrides})


def _stub_with_token(**token_overrides: Any) -> StubSession:
    user_id = uuid.uuid4()
    return _stub(user_id=user_id, token=_token(user_id, **token_overrides))


def _install(stub: StubSession) -> TestClient:
    async def override() -> AsyncIterator[StubSession]:
        yield stub

    app.dependency_overrides[get_session] = override
    return client


def _body(**overrides: Any) -> dict[str, Any]:
    return {
        "subject_id": str(_SUBJECT_ID),
        "class_date": "2026-09-21",
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "title": "Derivadas",
        "privacy_notice_version": live_settings.privacy_notice_version,
        "cloud_processing_accepted": True,
        "third_party_voice_acknowledged": True,
        **overrides,
    }


def _post(
    client: TestClient,
    body: dict[str, Any] | None = None,
    *,
    cookie: bool = True,
    csrf: bool = True,
) -> Any:
    headers = {"Origin": live_settings.allowed_origin}
    if csrf:
        headers[CSRF_HEADER] = CSRF
    return client.post(
        "/audios",
        json=_body() if body is None else body,
        headers=headers,
        cookies={COOKIE_NAME: COOKIE} if cookie else {},
    )


def _assert_no_writes(stub: StubSession) -> None:
    """Un rechazo no creó nada: 0 `add`, 0 `flush`, 0 `commit` (no reservó capacidad)."""
    assert stub.writes == 0


def _assert_rejected_early(stub: StubSession) -> None:
    """Consentimiento e idioma se deciden sin llegar siquiera a la materia ni al cupo."""
    _assert_no_writes(stub)
    assert {"sql:subject", *_CAPACITY_QUERIES}.isdisjoint(stub.events)


def _assert_token_rejected(stub: StubSession) -> None:
    """El token se pudo mirar, pero no se reservó ni se consumió nada."""
    _assert_no_writes(stub)
    assert _CAPACITY_QUERIES.isdisjoint(stub.events)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


# --- A. Consentimiento versionado ------------------------------------------------------


def test_cloud_flag_false_is_rejected_without_writing() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(cloud_processing_accepted=False))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_required"
    _assert_rejected_early(stub)


def test_third_party_voice_flag_false_is_rejected_without_writing() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(third_party_voice_acknowledged=False))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_required"
    _assert_rejected_early(stub)


def test_missing_privacy_notice_version_is_a_validation_error() -> None:
    stub = _stub()
    body = _body()
    del body["privacy_notice_version"]

    response = _post(_install(stub), body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "privacy_notice_version" in error["details"]["fields"]
    _assert_rejected_early(stub)


def test_outdated_privacy_notice_version_reports_the_current_one() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(privacy_notice_version="2026-08-v1"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "consent_required"
    # Solo la versión vigente: la obsoleta enviada por el cliente no se refleja.
    assert error["details"] == {"privacy_notice_version": live_settings.privacy_notice_version}
    assert "2026-08-v1" not in response.text
    _assert_rejected_early(stub)


def test_language_is_checked_once_consent_has_passed() -> None:
    # Orden §4.2: el consentimiento OK deja paso al rechazo de idioma, no al revés.
    stub = _stub()

    response = _post(_install(stub), _body(language_code="de"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "language_not_allowed"
    _assert_rejected_early(stub)


# --- B. Idioma ------------------------------------------------------------------------


def test_multi_is_never_accepted() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(language_code="multi"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "language_not_allowed"
    assert "multi" not in error["details"]["allowed"]
    _assert_rejected_early(stub)


def test_missing_language_is_a_validation_error() -> None:
    stub = _stub()
    body = _body()
    del body["language_code"]

    response = _post(_install(stub), body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "language_code" in error["details"]["fields"]
    _assert_rejected_early(stub)


def test_language_outside_the_allowlist_reports_the_allowed_list() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(language_code="pt"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "language_not_allowed"
    assert "pt" not in error["details"]["allowed"]
    assert "es" in error["details"]["allowed"]
    _assert_rejected_early(stub)


def test_language_case_is_normalized_before_the_allowlist() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(language_code="ES"))

    assert response.status_code == 201
    assert stub.added == 2 and stub.commits == 1


def test_published_capabilities_override_the_provisional_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # D3: lo que publica el spike (y mostrará el formulario) manda sobre `ingest_languages`.
    _use_settings(monkeypatch, ingestion_capabilities=_published(languages=["de"]))
    stub = _stub()
    client = _install(stub)

    provisional = _post(client, _body(language_code="es"))
    published = _post(client, _body(language_code="de"))

    assert provisional.status_code == 422
    assert provisional.json()["error"]["details"]["allowed"] == ["de"]
    assert published.status_code == 201
    # Con `active_slots` publicado, el contador de huecos sí se consulta (D3 + D4).
    assert "sql:count_slot" in stub.events


# --- C. Zona horaria y materia ---------------------------------------------------------


def test_unknown_timezone_is_rejected_without_writing() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(class_timezone="America/NoExiste"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    _assert_rejected_early(stub)


def test_valid_iana_timezone_reserves() -> None:
    stub = _stub()

    response = _post(_install(stub), _body(class_timezone="America/Bogota"))

    assert response.status_code == 201
    assert stub.added == 2 and stub.commits == 1


def test_another_users_subject_is_404() -> None:
    stub = _stub(subject=None)  # el guard de tenant devuelve «no existe» para un ID ajeno

    response = _post(_install(stub), _body(subject_id=str(uuid.uuid4())))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    _assert_no_writes(stub)
    assert "sql:subject" in stub.events


# --- D. Token de variante --------------------------------------------------------------


def test_unknown_or_foreign_token_is_rejected_without_writing() -> None:
    stub = _stub()  # sin token en BD: inexistente y ajeno son el mismo caso

    response = _post(_install(stub), _body(variant_confirmation_token=PRESENTED_TOKEN))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "variant_confirmation_token" in error["details"]["fields"]
    _assert_token_rejected(stub)


def test_expired_token_is_rejected_without_writing() -> None:
    stub = _stub_with_token(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    response = _post(_install(stub), _body(variant_confirmation_token=PRESENTED_TOKEN))

    assert response.status_code == 422
    assert "variant_confirmation_token" in response.json()["error"]["details"]["fields"]
    _assert_token_rejected(stub)


def test_already_consumed_token_is_rejected_without_writing() -> None:
    stub = _stub_with_token(
        consumed_at=datetime.now(UTC), reserved_audio_id=uuid.uuid4()  # CHECK: van juntos
    )

    response = _post(_install(stub), _body(variant_confirmation_token=PRESENTED_TOKEN))

    assert response.status_code == 422
    assert "variant_confirmation_token" in response.json()["error"]["details"]["fields"]
    _assert_token_rejected(stub)


def test_token_metadata_mismatch_is_rejected_on_the_token_field() -> None:
    stub = _stub_with_token(subject_id=uuid.uuid4())  # otra materia que la del body

    response = _post(_install(stub), _body(variant_confirmation_token=PRESENTED_TOKEN))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "variant_confirmation_token" in error["details"]["fields"]
    _assert_token_rejected(stub)


def test_valid_token_is_consumed_in_the_reservation_transaction() -> None:
    stub = _stub_with_token()

    response = _post(_install(stub), _body(variant_confirmation_token=PRESENTED_TOKEN))

    assert response.status_code == 201
    assert stub.token_updates == 1
    assert stub.commits == 1
    consumed = stub.events.index("sql:consume_token")
    # El consumo y ambos `add` ocurren dentro de la misma transacción que confirma.
    assert stub.events.index("add:Audio") < consumed
    assert stub.events.index("add:IngestionAttempt") < consumed
    assert consumed < stub.events.index("commit")


# --- E. Reserva y respuestas -----------------------------------------------------------


def test_valid_request_returns_the_relative_upload_url() -> None:
    stub = _stub()

    response = _post(_install(stub))

    assert response.status_code == 201
    data = response.json()
    assert data["outcome"] == "new"
    assert data["upload_url"] == (
        f"/audios/{data['audio_id']}/content?attempt_id={data['attempt_id']}"
    )
    assert response.headers["location"] == data["upload_url"]
    assert datetime.fromisoformat(data["upload_expires_at"]) > datetime.now(UTC)
    assert (stub.added, stub.flushes, stub.commits) == (2, 2, 1)
    # Sin publicación del spike no hay cupo que contar (D4 sigue `blocked`): no se inventa.
    assert "sql:count_slot" not in stub.events


def test_full_slot_is_503_with_retry_after_and_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_settings(monkeypatch, ingestion_capabilities=_published(active_slots=1))
    stub = _stub(active=1)

    response = _post(_install(stub))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "capacity_unavailable"
    assert response.headers["retry-after"] == str(audios_service.RETRY_AFTER_SECONDS)
    _assert_no_writes(stub)
    assert "sql:count_slot" in stub.events


def test_exhausted_quota_is_429_with_retry_after_and_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mecanismo condicional (D4): el valor lo fija el test, no el código de producción.
    monkeypatch.setattr(audios_service, "per_user_active_limit", 1)
    stub = _stub(active=1)

    response = _post(_install(stub))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.headers["retry-after"] == str(audios_service.RETRY_AFTER_SECONDS)
    _assert_no_writes(stub)
    assert "sql:count_quota" in stub.events


def test_identical_consecutive_posts_both_reserve() -> None:
    # Prueba literal de «sin dedupe aquí»: el hash aún no existe, así que no hay duplicado.
    stub = _stub()
    client = _install(stub)

    first = _post(client)
    second = _post(client)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["audio_id"] != second.json()["audio_id"]
    assert (stub.added, stub.commits) == (4, 2)


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, 201),
        ({"body": {"cloud_processing_accepted": False}}, 422),
        ({"body": {"language_code": "de"}}, 422),
        ({"body": {"class_timezone": "Mars/Olympus"}}, 422),
        ({"stub": {"subject": None}}, 404),
        ({"stub": {"active": 1}, "published": _published(active_slots=1)}, 503),
        ({"stub": {"active": 1}, "quota": 1}, 429),
        ({"cookie": False}, 401),
        ({"csrf": False}, 403),
    ],
)
def test_only_documented_statuses_are_observable(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], expected: int
) -> None:
    if "published" in options:
        _use_settings(monkeypatch, ingestion_capabilities=options["published"])
    if "quota" in options:
        monkeypatch.setattr(audios_service, "per_user_active_limit", options["quota"])
    stub = _stub(**options.get("stub", {}))
    client = _install(stub)

    response = _post(
        client,
        _body(**options.get("body", {})),
        cookie=options.get("cookie", True),
        csrf=options.get("csrf", True),
    )

    assert response.status_code == expected
    # Invariante A2.3 (S2.md §A2.3 + DISENO §3.1–3.2): nada fuera del catálogo documentado.
    assert response.status_code in {201, 422, 429, 503, 401, 403, 404}
