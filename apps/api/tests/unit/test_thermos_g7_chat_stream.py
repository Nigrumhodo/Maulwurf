"""G7 review fixes: live stream wall-clock cap, replay cap origin and tool prompt bounds."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from app.services.chat_prompt import PreparedStream, ReplayState
from app.services.chat_stream import (
    MAX_TOOL_RESULT_CHARS,
    _fit_to_budget,
    _tool_result_message,
)
from app.services.llm import ChatMessage
from app.services.rag import RAGService


def _service(llm: Any, *, stream_max_seconds: float) -> RAGService:
    return RAGService(
        session_factory=cast(Any, object()),
        embedding_provider=cast(Any, object()),
        llm_provider=llm,
        checkpoint_seconds=60.0,
        cancel_poll_seconds=60.0,
        heartbeat_seconds=60.0,
        stream_max_seconds=stream_max_seconds,
        replay_poll_seconds=0.01,
    )


def _prepared(replay: ReplayState | None = None) -> PreparedStream:
    return PreparedStream(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4(),
        messages=(ChatMessage(role="user", content="hola"),),
        sources=(),
        replay=replay,
    )


def _patch(service: RAGService, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    finalized: list[str] = []

    async def streaming(*args: object) -> str:
        return "streaming"

    async def finalize(
        prepared: PreparedStream, status: str, *args: object
    ) -> str:
        finalized.append(status)
        return status

    monkeypatch.setattr(service, "_message_status", streaming)
    monkeypatch.setattr(service, "_finalize", finalize)
    return finalized


async def test_live_stream_is_capped_and_finalized_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingLLM:
        model = "test-model"
        context_window = 4096

        async def stream(
            self, messages: Sequence[ChatMessage], *, max_tokens: int
        ) -> AsyncIterator[str]:
            await asyncio.sleep(30)
            yield "never"

    service = _service(HangingLLM(), stream_max_seconds=0.05)
    finalized = _patch(service, monkeypatch)

    frames = [frame async for frame in service._live_events(_prepared())]

    assert finalized == ["failed"]
    assert "event: error" in frames[-1]
    assert "stream_timeout" in frames[-1]


async def test_replay_cap_counts_from_message_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(None, stream_max_seconds=300.0)
    finalized = _patch(service, monkeypatch)
    replay = ReplayState(
        message_id=uuid.uuid4(),
        content="",
        status="streaming",
        citations=(),
        created_at=datetime.now(UTC) - timedelta(seconds=301),
    )

    frames = [frame async for frame in service._replay_events(_prepared(replay), 0)]

    assert finalized == ["failed"]
    assert "stream_timeout" in frames[-1]


def test_tool_result_is_truncated_to_a_bounded_size() -> None:
    message = _tool_result_message({"blob": "x" * (MAX_TOOL_RESULT_CHARS * 3)})

    assert len(message.content) < MAX_TOOL_RESULT_CHARS + 300
    assert "[truncated]" in message.content


def test_fit_to_budget_drops_oldest_history_but_keeps_system_and_question() -> None:
    messages = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="old question " * 50),
        ChatMessage(role="assistant", content="old answer " * 50),
        ChatMessage(role="user", content="new question " * 50),
        ChatMessage(role="assistant", content="new answer"),
        ChatMessage(role="user", content="final"),
    ]

    fitted = _fit_to_budget(messages, 200, lambda text: len(text.split()))

    assert fitted[0].content == "sys"
    assert fitted[-1].content == "final"
    assert len(fitted) < len(messages)
