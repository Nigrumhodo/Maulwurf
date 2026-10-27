"""U-S2-AN-02: dedupe identity and the single-use variant token, without Postgres (A2.5).

`app/services/dedupe.py` owns every duplicate decision of the `PUT` (ADR-0006 keeps that SQL out
of the router). This suite proves the ticket's promises with stub sessions that record writes,
capture compiled SQL and fail on unexpected calls, following the style of `test_create_audio.py`:

- the identity is hash + language + subject + date + zone; title/teacher never participate;
- the lookups are tenant-filtered, exclude the own reservation, and the variant lookup picks a
  stable canonical among several candidates;
- the token is opaque, single-use, TTL-bounded and bound to tenant/hash/metadata; only its
  SHA-256 is ever persisted;
- identity writes converge on `IntegrityError` (the `audios_dedupe_identity` unique index);
- `is_late_arrival` resolves the concurrent-variant race (S2.md §A2.5 review finding 2026-10-11)
  deterministically toward one canonical.

HTTP-level behavior (409 payload, cleanup ordering, second POST/PUT) lives in
`tests/integration/test_put_content_dedupe.py` (I-S2-AN-05). No `integration` marker: runs in
`python-unit`.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ApiError
from app.models import Audio, IngestionAttempt, VariantConfirmationToken
from app.services import audios as audios_service
from app.services import dedupe

CONTENT = b"RIFF" + b"\x00" * 60
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class _Rows:
    """Resultado mínimo de `execute()`: solo lo que la aplicación llega a leer."""

    def __init__(self, *values: Any) -> None:
        self._values = values

    def scalar_one_or_none(self) -> Any:
        if len(self._values) > 1:
            raise AssertionError(f"esperaba una fila como mucho y llegaron {len(self._values)}")
        return self._values[0] if self._values else None


class StubDb:
    """`AsyncSession` de mentira: registra escrituras y SQL compilado, y falla si algo sorprende."""

    def __init__(self, *, rows: Any = None, commit_error: Exception | None = None) -> None:
        self.rows = rows
        self.commit_error = commit_error
        self.statements: list[str] = []
        self.events: list[str] = []
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self._pending: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.events.append(f"add:{type(obj).__name__}")
        self._pending.append(obj)

    async def flush(self) -> None:
        self.flushes += 1
        self.events.append("flush")
        # `uuid_pk` is `server_default=gen_random_uuid()`: without a DB the stub assigns the PK.
        for obj in self._pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        self._pending.clear()

    async def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.rollbacks += 1

    async def delete(self, obj: Any) -> None:
        self.events.append(f"delete:{type(obj).__name__}")

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Rows:
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        self.statements.append(sql)
        self.events.append("sql:delete" if sql.startswith("DELETE") else "sql:select")
        return _Rows(self.rows)


def _db(stub: StubDb) -> AsyncSession:
    return cast(AsyncSession, stub)


def _audio(**fields: Any) -> Audio:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "subject_id": uuid.uuid4(),
        "language_code": "es",
        "class_date": date(2026, 9, 21),
        "class_timezone": "America/Bogota",
        "title": "Derivadas",
        "teacher": "García",
        "created_at": datetime.now(UTC),
        "sha256": None,
        "original_bytes": None,
    }
    values.update(fields)
    return cast(Audio, SimpleNamespace(**values))


def _attempt(**fields: Any) -> IngestionAttempt:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": "awaiting_upload",
        "received_bytes": 0,
        "expected_bytes": None,
    }
    values.update(fields)
    return cast(IngestionAttempt, SimpleNamespace(**values))


def _identity(**overrides: Any) -> dedupe.Identity:
    values: dict[str, Any] = {
        "language_code": "es",
        "subject_id": uuid.uuid4(),
        "class_date": date(2026, 9, 21),
        "class_timezone": "America/Bogota",
    }
    values.update(overrides)
    return dedupe.Identity(**values)


def _token_row(**overrides: Any) -> VariantConfirmationToken:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "token_hash": "h" * 64,
        "canonical_audio_id": uuid.uuid4(),
        "content_sha256": DIGEST,
        "subject_id": uuid.uuid4(),
        "class_date": date(2026, 9, 21),
        "class_timezone": "America/Bogota",
        "language_code": "es",
        "metadata_digest": "b" * 64,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    values.update(overrides)
    return VariantConfirmationToken(**values)


def _where_of(stub: StubDb, index: int = 0) -> str:
    statement = stub.statements[index]
    return statement.split("WHERE", 1)[1] if "WHERE" in statement else ""


def _uuid_literal(value: uuid.UUID) -> str:
    # On the default (non-Postgres) compilation dialect a UUID literal renders hex-only.
    return value.hex


# --- A. Identity (M2 formula) --------------------------------------------------------------


def test_identity_of_excludes_title_and_teacher() -> None:
    base = _audio()
    renamed = _audio(
        user_id=base.user_id,
        language_code=base.language_code,
        subject_id=base.subject_id,
        class_date=base.class_date,
        class_timezone=base.class_timezone,
        title="Parciales",
        teacher="Otro profesor",
    )

    identity = dedupe.identity_of(base)

    assert identity == dedupe.identity_of(renamed)
    assert set(dedupe.Identity.__dataclass_fields__) == {
        "language_code",
        "subject_id",
        "class_date",
        "class_timezone",
    }
    assert (
        identity.language_code,
        identity.subject_id,
        identity.class_date,
        identity.class_timezone,
    ) == ("es", base.subject_id, base.class_date, "America/Bogota")


async def test_exact_lookup_is_tenant_scoped_and_excludes_the_own_reservation() -> None:
    stub = StubDb()
    audio = _audio()

    result = await dedupe.lookup_exact_identity(
        _db(stub),
        user_id=audio.user_id,
        sha256_hex=DIGEST,
        identity=dedupe.identity_of(audio),
        exclude_audio_id=audio.id,
    )

    assert result is None
    sql = stub.statements[0]
    assert "FROM audios" in sql
    where = _where_of(stub)
    for fragment in (
        f"audios.user_id = '{_uuid_literal(audio.user_id)}'",
        f"audios.sha256 = '{DIGEST}'",
        "audios.language_code = 'es'",
        f"audios.subject_id = '{_uuid_literal(audio.subject_id)}'",
        "audios.class_date = '2026-09-21'",
        "audios.class_timezone = 'America/Bogota'",
        "audios.deleted_at IS NULL",
        f"audios.id != '{_uuid_literal(audio.id)}'",
    ):
        assert fragment in where, fragment
    # Identity never includes the human labels (they live in the SELECT list only).
    assert "title" not in where and "teacher" not in where


async def test_variant_lookup_is_stable_ordered_and_tenant_scoped() -> None:
    stub = StubDb()
    audio = _audio()
    identity = dedupe.identity_of(audio)

    result = await dedupe.lookup_variant(
        _db(stub),
        user_id=audio.user_id,
        sha256_hex=DIGEST,
        identity=identity,
        exclude_audio_id=audio.id,
    )

    assert result is None
    sql = stub.statements[0]
    where = _where_of(stub)
    assert f"audios.user_id = '{_uuid_literal(audio.user_id)}'" in where
    assert f"audios.sha256 = '{DIGEST}'" in where
    assert "audios.deleted_at IS NULL" in where
    assert f"audios.id != '{_uuid_literal(audio.id)}'" in where
    for column in ("language_code", "subject_id", "class_date", "class_timezone"):
        assert f"audios.{column} != " in where, column
    assert where.count(" OR ") == 3  # one disjunction joining the four context comparisons
    # Stable canonical among several candidates: oldest first, at most one row.
    assert "ORDER BY audios.created_at" in sql
    assert "LIMIT 1" in sql
    assert "title" not in where and "teacher" not in where


# --- B. Metadata digest -------------------------------------------------------------------


def test_metadata_digest_is_keyed_and_context_sensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = dedupe.identity_of(_audio())

    digest = dedupe._metadata_digest(identity)

    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    assert dedupe._metadata_digest(identity) == digest  # deterministic
    for field, value in (
        ("language_code", "en"),
        ("subject_id", uuid.uuid4()),
        ("class_date", date(2026, 9, 22)),
        ("class_timezone", "Europe/Madrid"),
    ):
        assert dedupe._metadata_digest(_identity(**{field: value})) != digest, field
    plaintext = "|".join(
        (
            str(identity.subject_id),
            identity.class_date.isoformat(),
            identity.class_timezone,
            identity.language_code,
        )
    )
    assert digest != plaintext and plaintext not in digest
    # Keyed by the backend secret: another key produces another digest.
    monkeypatch.setattr(settings, "secret_key", SecretStr("otro-secreto-del-backend"))
    assert dedupe._metadata_digest(identity) != digest


# --- C. Token: opaque, single-use, TTL, bound ----------------------------------------------


async def test_issue_variant_token_persists_only_the_hash() -> None:
    stub = StubDb()
    canonical = _audio(sha256=DIGEST)
    identity = dedupe.identity_of(canonical)
    before = datetime.now(UTC)

    issued = await dedupe.issue_variant_token(
        _db(stub), canonical=canonical, identity=identity, content_sha256=DIGEST
    )

    assert stub.events == ["add:VariantConfirmationToken", "flush"]
    row: VariantConfirmationToken = stub.added[0]
    assert issued.plaintext != row.token_hash
    expected_hash = hashlib.sha256(issued.plaintext.encode("utf-8", "surrogatepass")).hexdigest()
    assert row.token_hash == expected_hash
    assert len(issued.plaintext) >= 43 and issued.plaintext.isascii()
    assert row.user_id == canonical.user_id
    assert row.canonical_audio_id == canonical.id
    assert row.content_sha256 == DIGEST
    assert (
        row.subject_id,
        row.class_date,
        row.class_timezone,
        row.language_code,
    ) == (identity.subject_id, identity.class_date, identity.class_timezone, "es")
    assert row.consumed_at is None and row.reserved_audio_id is None
    ttl = timedelta(minutes=settings.variant_token_ttl_minutes)
    assert before + ttl <= issued.expires_at <= datetime.now(UTC) + ttl
    assert issued.expires_at > datetime.now(UTC)  # short but alive at issuance


def test_verify_variant_hash_requires_exact_equality() -> None:
    token = _token_row()
    flipped = ("0" if DIGEST[0] != "0" else "1") + DIGEST[1:]

    assert dedupe.verify_variant_hash(token, DIGEST) is True
    assert dedupe.verify_variant_hash(token, flipped) is False
    assert dedupe.verify_variant_hash(token, DIGEST[:-1]) is False


async def test_token_consumption_locks_the_row_for_update() -> None:
    token = _token_row()
    stub = StubDb(rows=token)

    returned = await audios_service.consume_variant_token(
        _db(stub),
        user_id=token.user_id,
        presented="token-plano-de-prueba",
        subject_id=token.subject_id,
        class_date=token.class_date,
        class_timezone=token.class_timezone,
        language_code=token.language_code,
    )

    assert returned is token
    assert "FOR UPDATE" in stub.statements[0]  # serializes the double-spend


@pytest.mark.parametrize(
    ("label", "overrides", "mismatch"),
    [
        ("expired", {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, None),
        ("consumed", {"consumed_at": datetime.now(UTC)}, None),
        ("foreign_subject", {}, "subject_id"),
        ("foreign_date", {}, "class_date"),
        ("foreign_zone", {}, "class_timezone"),
        ("foreign_language", {}, "language_code"),
    ],
)
async def test_stale_or_mismatched_token_is_rejected_without_writes(
    label: str, overrides: dict[str, Any], mismatch: str | None
) -> None:
    token = _token_row(**overrides)
    stub = StubDb(rows=token)

    with pytest.raises(ApiError) as excinfo:
        await audios_service.consume_variant_token(
            _db(stub),
            user_id=token.user_id,
            presented="token-plano-de-prueba",
            subject_id=uuid.uuid4() if mismatch == "subject_id" else token.subject_id,
            class_date=date(2026, 1, 1) if mismatch == "class_date" else token.class_date,
            class_timezone=(
                "Europe/Madrid" if mismatch == "class_timezone" else token.class_timezone
            ),
            language_code="en" if mismatch == "language_code" else token.language_code,
        )

    error = excinfo.value
    assert (error.status_code, error.code) == (422, "validation_failed"), label
    assert error.details == {
        "fields": {"variant_confirmation_token": "Token inválido, caducado o ya utilizado."}
    }, label
    assert stub.events == ["sql:select"]  # the rejection wrote nothing


# --- D. Reservation absorption and identity convergence ------------------------------------


async def test_absorb_reservation_deletes_in_fk_safe_order() -> None:
    stub = StubDb()
    audio = _audio()
    attempt = _attempt()

    await dedupe.absorb_reservation(_db(stub), audio=audio, attempt=attempt)

    # Composite FKs without relationships: spent tokens, then attempt, then audio.
    assert stub.events == [
        "sql:delete",
        f"delete:{type(attempt).__name__}",
        "flush",
        f"delete:{type(audio).__name__}",
    ]
    assert stub.statements[0].startswith("DELETE FROM variant_confirmation_tokens")


async def test_admit_identity_writes_and_reports_no_race() -> None:
    stub = StubDb()
    audio = _audio()
    attempt = _attempt()

    raced = await dedupe.admit_identity(
        _db(stub),
        audio=audio,
        attempt=attempt,
        sha256_hex=DIGEST,
        received_bytes=64,
        expected_bytes=64,
    )

    assert raced is None
    assert audio.sha256 == DIGEST and audio.original_bytes == 64
    assert (attempt.received_bytes, attempt.expected_bytes) == (64, 64)
    assert stub.events == ["commit"]


async def test_admit_identity_race_rolls_back_and_returns_the_canonical() -> None:
    canonical = _audio(sha256=DIGEST)
    stub = StubDb(rows=canonical, commit_error=IntegrityError("stmt", {}, Exception("dup")))
    audio = _audio()
    attempt = _attempt()

    raced = await dedupe.admit_identity(
        _db(stub),
        audio=audio,
        attempt=attempt,
        sha256_hex=DIGEST,
        received_bytes=64,
        expected_bytes=64,
    )

    assert raced is canonical
    assert stub.events == ["commit", "rollback", "sql:select"]
    assert f"audios.id != '{_uuid_literal(audio.id)}'" in _where_of(stub)


# --- E. Concurrent-variant precedence (review finding 2026-10-11) --------------------------


def test_late_arrival_prefers_the_older_reservation_and_ties_by_id() -> None:
    early = datetime.now(UTC) - timedelta(minutes=2)
    older = _audio(created_at=early)
    newer = _audio(created_at=early + timedelta(minutes=1))
    assert dedupe.is_late_arrival(rival=older, own=newer) is True
    assert dedupe.is_late_arrival(rival=newer, own=older) is False
    # Same instant (a shared transaction timestamp): the smaller id wins, in both directions.
    first, second = uuid.uuid4(), uuid.uuid4()
    low = _audio(id=min(first, second), created_at=early)
    high = _audio(id=max(first, second), created_at=early)
    assert dedupe.is_late_arrival(rival=low, own=high) is True
    assert dedupe.is_late_arrival(rival=high, own=low) is False


def test_late_arrival_handles_naive_and_aware_datetimes() -> None:
    aware = datetime.now(UTC)
    naive = datetime(2026, 9, 21, 12, 0, 0)
    audio_aware = _audio(created_at=aware)
    audio_naive = _audio(created_at=naive)
    # Must compare safely without TypeError (can't compare offset-naive and offset-aware datetimes)
    assert isinstance(dedupe.is_late_arrival(rival=audio_aware, own=audio_naive), bool)
    assert isinstance(dedupe.is_late_arrival(rival=audio_naive, own=audio_aware), bool)

