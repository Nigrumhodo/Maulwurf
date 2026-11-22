"""W3 defensive controls: stream caps, budgets, secret filter, and worker guards."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import fakeredis.aioredis
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.calendar import CalendarSettingsUpdateIn
from app.schemas.tools import ProposalPayload, ToolErrorPayload
from app.services.chat_prompt import STREAM_MAX_SECONDS, PreparedStream
from app.services.embeddings import EmbeddingProviderError, HttpEmbeddingProvider
from app.services.gmail_templates import render_notification_email
from app.services.limits import enforce_lua_window, enforce_notification_send_limit
from app.services.llm import ChatMessage, HttpChatProvider, LLMProviderError
from app.services.notifications import NotificationDeliveryRejected
from app.services.provider_budget import (
    ProviderBudgetExceeded,
    enforce_provider_budget,
    reset_provider_budget_cache,
)
from app.services.provider_budget import collect_usage as real_collect_usage
from app.services.rag import RAGService
from app.services.rate_limiter import (
    STREAM_SLOT_TTL_SECONDS,
    acquire_stream,
    refresh_stream_slot,
    release_stream,
    stream_key,
)
from app.services.summary import SummaryRejected, validate_summary
from app.services.tools import ToolCall, ToolOrchestrator

_SECRETS = {
    "secret_key": "s3cret-value-for-test",
    "encryption_key": "enc-value-for-test",
    "oauth_state_secret": "state-value-for-test",
}
_SUMMARY = (
    "The class reviewed integration. The midterm covers four topics. "
    "Students should review before the exam. No other deadline was stated."
)


@pytest.fixture(autouse=True)
def _isolated_budget_cache() -> Any:
    reset_provider_budget_cache()
    yield
    reset_provider_budget_cache()


def test_stream_slot_ttl_outlives_the_live_cap() -> None:
    assert STREAM_SLOT_TTL_SECONDS > STREAM_MAX_SECONDS


async def test_refresh_stream_slot_extends_a_held_key_and_does_not_recreate_it() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()
    await acquire_stream(redis, user_id)
    await redis.expire(stream_key(user_id), 5)

    assert await refresh_stream_slot(redis, user_id) is True
    ttl = await redis.ttl(stream_key(user_id))
    assert isinstance(ttl, int)
    assert ttl > 5

    await release_stream(redis, user_id)
    assert await refresh_stream_slot(redis, user_id) is False
    assert await redis.exists(stream_key(user_id)) == 0


async def test_live_stream_ends_failed_when_the_duration_cap_is_hit() -> None:
    class SlowLLM:
        model = "test-model"
        context_window = 4096

        async def stream(
            self, messages: Sequence[ChatMessage], *, max_tokens: int
        ) -> AsyncIterator[str]:
            del messages, max_tokens
            await asyncio.sleep(2)
            yield "tarde"
            return

    service = RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=cast(Any, SlowLLM()),
        stream_max_seconds=0.15,
        heartbeat_seconds=0.05,
        checkpoint_seconds=0.05,
        cancel_poll_seconds=0.05,
    )
    finalized: list[str] = []

    async def streaming(user_id: uuid.UUID, message_id: uuid.UUID) -> str:
        del user_id, message_id
        return "streaming"

    async def checkpoint(*_args: object, **_kwargs: object) -> bool:
        return True

    async def finalize(
        prepared: PreparedStream,
        status: str,
        content: str,
        citations: Sequence[object],
        tool_calls: Sequence[object],
    ) -> str:
        del prepared, content, citations, tool_calls
        finalized.append(status)
        return status

    service._message_status = streaming  # type: ignore[method-assign]
    service._checkpoint = checkpoint  # type: ignore[method-assign]
    service._finalize = finalize  # type: ignore[method-assign]
    prepared = PreparedStream(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        messages=(ChatMessage(role="user", content="pregunta"),),
        sources=(),
    )

    frames = [frame async for frame in service._live_events(prepared)]

    errors = [
        json.loads(line.removeprefix("data: "))
        for frame in frames
        if frame.startswith("event: error")
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    assert errors == [
        {
            "code": "stream_timeout",
            "message": "La respuesta tardó demasiado; vuelve a intentarlo.",
        }
    ]
    assert finalized == ["failed"]


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "a" * 20,
        "sk-proj-" + "b" * 20,
        "sk-ant-" + "c" * 20,
        "ghp_" + "d" * 20,
        "gho_" + "e" * 20,
        "github_pat_" + "f" * 20,
        "AIza" + "g" * 35,
        "AKIA" + "H" * 16,
        "ya29.access-token",
        "xoxb-1234567890",
        "-----BEGIN RSA PRIVATE KEY-----",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature1",
        "Bearer supersecrettoken",
        "nvapi-secret",
    ],
)
def test_summary_rejects_cloud_credential_shapes(secret: str) -> None:
    with pytest.raises(SummaryRejected, match="sensitive data"):
        validate_summary(_SUMMARY, ["integration", secret])


def test_summary_still_accepts_a_plain_four_sentence_class() -> None:
    parsed = validate_summary(_SUMMARY, ["integration", "midterm"])

    assert parsed.text == _SUMMARY
    assert parsed.topics == ("integration", "midterm")


@pytest.mark.parametrize(
    ("base_url", "env", "allowed"),
    [
        ("http://llm.example/v1", "local", False),
        ("http://127.0.0.1:8000/v1", "local", True),
        ("http://e2e-provider:8080/v1", "local", True),
        ("http://provider-double:8080/v1", "ci", True),
        ("http://127.0.0.1:8000/v1", "prod", False),
        ("https://llm.example/v1", "prod", True),
    ],
)
def test_provider_base_urls_require_https_outside_local_stubs(
    base_url: str, env: str, allowed: bool
) -> None:
    def build_llm() -> HttpChatProvider:
        return HttpChatProvider(
            base_url=base_url,
            api_key="test-key",
            model="test",
            context_window=8192,
            env=env,
        )

    def build_embeddings() -> HttpEmbeddingProvider:
        return HttpEmbeddingProvider(
            base_url=base_url,
            api_key="test-key",
            model="test",
            dimension=8,
            batch_size=1,
            env=env,
        )

    if allowed:
        build_llm()
        build_embeddings()
        return
    with pytest.raises(LLMProviderError) as llm_error:
        build_llm()
    assert llm_error.value.details["reason"] == "insecure_base_url"
    with pytest.raises(EmbeddingProviderError) as embedding_error:
        build_embeddings()
    assert embedding_error.value.details["reason"] == "insecure_base_url"


def test_insecure_override_is_explicit() -> None:
    provider = HttpChatProvider(
        base_url="http://llm.example/v1",
        api_key="test-key",
        model="test",
        context_window=8192,
        env="prod",
        allow_insecure=True,
    )

    assert provider.model == "test"


async def test_propose_task_rejects_a_long_timezone_before_the_database() -> None:
    class _DB:
        async def scalars(self, statement: object) -> object:
            del statement
            raise AssertionError("timezone validation must not query")

    now = datetime.now(UTC)
    results = await ToolOrchestrator(cast(Any, _DB())).execute(
        [
            ToolCall(
                name="propose_task",
                arguments={
                    "titulo": "Entregar informe",
                    "tipo": "tarea",
                    "timezone": "A" * 65,
                    "mencion": {"kind": "absent"},
                },
            )
        ],
        user_id=uuid.uuid4(),
        now=now,
    )

    assert results[0].ok is False
    assert isinstance(results[0].payload, ToolErrorPayload)
    assert results[0].payload.code == "schema_invalid"


async def test_propose_task_reuses_a_pending_proposal_with_the_same_title() -> None:
    user_id = uuid.uuid4()
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        titulo="  Entregar   Informe ",
        source="chat",
        status="needs_review",
        calendar_sync_status="not_requested",
    )

    class _Rows:
        def all(self) -> list[object]:
            return [existing]

    class _DB:
        def __init__(self) -> None:
            self.added: list[object] = []

        async def scalars(self, statement: object) -> _Rows:
            del statement
            return _Rows()

        def add(self, task: object) -> None:
            self.added.append(task)

        async def flush(self) -> None:
            return None

    db = _DB()
    results = await ToolOrchestrator(cast(Any, db)).execute(
        [
            ToolCall(
                name="propose_task",
                arguments={
                    "titulo": "entregar informe",
                    "tipo": "tarea",
                    "timezone": "America/Bogota",
                    "mencion": {"kind": "absent"},
                },
            )
        ],
        user_id=user_id,
        now=datetime.now(UTC),
    )

    assert db.added == []
    assert results[0].ok is True
    assert isinstance(results[0].payload, ProposalPayload)
    assert results[0].payload.task_id == str(existing.id)
    assert results[0].payload.evidence == "general_suggestion"


def test_read_calendar_ids_reject_controls_spaces_and_oversized_lists() -> None:
    holiday = "es.colombia#holiday@group.v.calendar.google.com"
    accepted = CalendarSettingsUpdateIn(
        read_calendar_ids=["primary", "ana@example.test", holiday]
    )
    assert accepted.read_calendar_ids[0] == "primary"

    for bad in ("has space", "line\nbreak", "", "x" * 257, "id\x85"):
        with pytest.raises(ValidationError):
            CalendarSettingsUpdateIn(read_calendar_ids=[bad])
    with pytest.raises(ValidationError):
        CalendarSettingsUpdateIn(read_calendar_ids=["ok"] * 26)


@pytest.mark.parametrize("control", ["\t", "\x0b", "\x1b", "\x7f", "\x85"])
def test_gmail_headers_reject_c0_and_c1_controls(control: str) -> None:
    with pytest.raises(NotificationDeliveryRejected) as exc:
        render_notification_email(
            recipient="ana@example.test",
            notification_type="reminder_t24",
            task_title=f"Tarea{control}",
            task_tipo="entrega",
        )

    assert exc.value.error_code == "template_invalid"


def test_gmail_headers_keep_accented_text() -> None:
    email = render_notification_email(
        recipient="ana@example.test",
        notification_type="reminder_t24",
        task_title="cálculo",
        task_tipo="entrega",
    )

    assert "cálculo" in email.subject


async def test_provider_budget_is_idle_until_a_positive_cap_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def collect(db: object, *, cross_tenant: bool = False) -> dict[str, float]:
        del db, cross_tenant
        nonlocal calls
        calls += 1
        return {"llm": 0.0}

    monkeypatch.setattr("app.services.provider_budget.collect_usage", collect)
    settings = Settings(_env_file=None, **_SECRETS)

    await enforce_provider_budget(cast(Any, object()), settings, providers=("llm",))

    assert calls == 0
    assert real_collect_usage is not collect


async def test_provider_budget_rejects_spent_caps_and_caches_the_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def collect(db: object, *, cross_tenant: bool = False) -> dict[str, float]:
        del db, cross_tenant
        nonlocal calls
        calls += 1
        return {"llm": 9.0, "asr": 0.0, "google": 0.0}

    monkeypatch.setattr("app.services.provider_budget.collect_usage", collect)
    under = Settings(_env_file=None, budget_llm_tokens=10, **_SECRETS)
    await enforce_provider_budget(cast(Any, object()), under, providers=("llm",))
    await enforce_provider_budget(cast(Any, object()), under, providers=("llm",))
    assert calls == 1

    reset_provider_budget_cache()
    spent = Settings(_env_file=None, budget_llm_tokens=9, **_SECRETS)
    with pytest.raises(ProviderBudgetExceeded) as exc:
        await enforce_provider_budget(cast(Any, object()), spent, providers=("llm",))
    assert exc.value.provider == "llm"
    assert exc.value.code == "llm_rate_limited"


async def test_provider_budget_fails_closed_when_the_reading_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def collect(db: object, *, cross_tenant: bool = False) -> dict[str, float]:
        del db, cross_tenant
        raise SQLAlchemyError("unreadable")

    monkeypatch.setattr("app.services.provider_budget.collect_usage", collect)
    settings = Settings(_env_file=None, budget_llm_tokens=10, **_SECRETS)

    with pytest.raises(ConnectionError):
        await enforce_provider_budget(cast(Any, object()), settings, providers=("llm",))


async def test_chat_and_notification_windows_return_retry_after() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    user_id = uuid.uuid4()
    await enforce_lua_window(redis, scope="chat", identity=str(user_id), limit=1)
    with pytest.raises(ApiError) as chat_error:
        await enforce_lua_window(redis, scope="chat", identity=str(user_id), limit=1)
    assert chat_error.value.status_code == 429
    assert chat_error.value.code == "rate_limited"
    assert chat_error.value.headers["Retry-After"].isdigit()

    await enforce_notification_send_limit(redis, user_id, limit=1)
    with pytest.raises(ApiError) as notification_error:
        await enforce_notification_send_limit(redis, user_id, limit=1)
    assert notification_error.value.status_code == 429
    assert notification_error.value.headers["Retry-After"].isdigit()
