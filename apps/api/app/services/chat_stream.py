"""Live and replay SSE for one chat turn.

`RAGService.stream_events` decides which path to run. The generators, checkpoints and
terminal frames live here so the service module stays the turn orchestrator.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chat import Message
from app.schemas.citations import (
    Citation,
    CitedSource,
    cited_sources,
    dump_citations,
    parse_citations,
)
from app.schemas.tools import ToolCallOut, dump_tool_calls, parse_tool_calls
from app.services.chat_prompt import (
    CITATION_VALIDATION_TIMEOUT_SECONDS,
    MESSAGE_OVERHEAD_TOKENS,
    ChatStreamError,
    MessageSnapshot,
    PreparedStream,
    _LLMEvent,
)
from app.services.chat_tools import TOOL_PROTOCOL, execute_tool, extract_tool_call
from app.services.citations import (
    CitationBuffer,
    CitationRef,
    CitationValidator,
    citation_event,
    citation_record,
    resolve_citation,
    validate_citation_record,
    validate_citation_source,
)
from app.services.llm import (
    ChatMessage,
    LLMContextExceeded,
    LLMError,
    LLMNotConfigured,
    LLMProvider,
    LLMProviderError,
    LLMRateLimited,
)
from app.services.progress import encode_event, heartbeat
from app.services.rate_limiter import refresh_stream_slot
from app.services.retrieval import RetrievedChunk
from app.services.tools import MAX_TOOL_ROUNDS, TOOL_BUDGET_SECONDS, ToolCall

logger = logging.getLogger(__name__)

# A tool result is appended after the prompt budget was filled; bound it so one large payload
# cannot push the next provider call over the context window.
MAX_TOOL_RESULT_CHARS = 4000


def _merge_tool_protocol(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    if messages and messages[0].role == "system":
        merged = ChatMessage(
            role="system",
            content=f"{messages[0].content}\n\n{TOOL_PROTOCOL}",
        )
        return [merged, *messages[1:]]
    return [ChatMessage(role="system", content=TOOL_PROTOCOL), *messages]


def _fit_to_budget(
    messages: list[ChatMessage], budget: int, count_tokens: Callable[[str], int]
) -> list[ChatMessage]:
    """Drop the oldest history turns until the prompt (with tool protocol) fits `budget`.

    Layout is `[system, (user, assistant)*, final user]`; the system prompt and the final
    evidence message are never removed.
    """

    def total(items: Sequence[ChatMessage]) -> int:
        return sum(count_tokens(m.content) + MESSAGE_OVERHEAD_TOKENS for m in items)

    kept = list(messages)
    while len(kept) > 3 and total(kept) > budget:
        del kept[1:3]
    return kept


def _tool_result_message(payload: dict[str, object]) -> ChatMessage:
    body = json.dumps(payload, ensure_ascii=False)
    if len(body) > MAX_TOOL_RESULT_CHARS:
        body = body[:MAX_TOOL_RESULT_CHARS] + "...[truncated]"
    return ChatMessage(
        role="user",
        content=(
            "Tool result: "
            + body
            + "\n\nRespond now in the student's language using "
            "this data without outputting JSON."
        ),
    )


def _safe_prose_cut(buffer: str, *, flushed_any: bool) -> int:
    """Number of leading characters in `buffer` guaranteed not to be part of a tool call.

    Plain prose streams immediately on the first delta; only `{` or markdown code fences are
    held until they either form a valid tool call or close as ordinary prose.
    """
    if not flushed_any and not buffer.strip():
        return 0
    fence_idx = buffer.find("`")
    brace_idx = buffer.find("{")
    candidates = [idx for idx in (fence_idx, brace_idx) if idx != -1]
    if not candidates:
        return len(buffer)
    earliest = min(candidates)
    if earliest == brace_idx and buffer.count("{") <= buffer.count("}") and fence_idx == -1:
        return len(buffer)
    return earliest
# In-process wake-up. The database status is authoritative.
_CANCEL_EVENTS: dict[uuid.UUID, asyncio.Event] = {}
_STREAM_TIMEOUT_ERROR = ChatStreamError(
    code="stream_timeout",
    message="La respuesta tardó demasiado; vuelve a intentarlo.",
)


class ChatTurnStream:
    """Streaming half of a chat turn. The subclass owns `prepare` and the provider."""

    _session_factory: async_sessionmaker[AsyncSession]
    _llm: LLMProvider | None
    _output_reserve_tokens: int
    _heartbeat_seconds: float
    _checkpoint_seconds: float
    _cancel_poll_seconds: float
    _stream_max_seconds: float
    _context_margin_tokens: int
    _replay_poll_seconds: float
    _citation_validator: CitationValidator | None
    _count_tokens: Callable[[str], int]
    # Set by the chat route for the lifetime of one SSE response. Absent in tests
    # and replay-only callers: refresh is then a no-op and the slot TTL is the backstop.
    _stream_redis: Redis | None = None

    def bind_stream_slot(self, client: Redis) -> None:
        """Keep the per-user stream counter alive for this response's Redis client."""
        self._stream_redis = client

    async def _refresh_stream_slot(self, user_id: uuid.UUID) -> None:
        """Renew the concurrency key while the turn is open.

        A failed refresh must not fail the turn: the slot TTL still bounds a lost
        heartbeat, and dropping the answer would punish a Redis blip.
        """
        client = self._stream_redis
        if client is None:
            return
        try:
            await refresh_stream_slot(client, user_id)
        except RedisError:
            logger.warning("chat.stream_slot_refresh_failed")

    async def _validate_citation(
        self, user_id: uuid.UUID, source: RetrievedChunk
    ) -> RetrievedChunk | None:
        """Revalidate one source with a short session; any failure drops the citation."""
        try:
            async with asyncio.timeout(CITATION_VALIDATION_TIMEOUT_SECONDS):
                if self._citation_validator is not None:
                    return await self._citation_validator(user_id, source)
                async with self._session_factory() as db:
                    return await validate_citation_source(db, user_id=user_id, source=source)
        except TimeoutError:
            logger.warning("chat.citation.validation_timeout chunk_id=%s", source.chunk_id)
            return None
        except Exception as exc:  # CancelledError is BaseException and must propagate.
            # The SSE response may already be open. A citation is optional evidence, so a
            # failed revalidation must fail closed instead of leaking a stale source.
            logger.warning(
                "chat.citation.validation_failed error=%s chunk_id=%s",
                type(exc).__name__,
                source.chunk_id,
            )
            return None

    async def _replay_citation_ref(
        self, user_id: uuid.UUID, record: Citation
    ) -> CitationRef | None:
        """Revalidate a persisted citation before replaying it."""
        try:
            async with self._session_factory() as db:
                current = await validate_citation_record(
                    db, user_id=user_id, record=record
                )
        except Exception as exc:  # CancelledError is BaseException and must propagate.
            logger.warning(
                "chat.citation.replay_validation_failed error=%s", type(exc).__name__
            )
            return None
        if current is None:
            return None
        return CitationRef(index=record.index, chunk=current, offset=record.offset)

    async def _live_events(self, prepared: PreparedStream) -> AsyncGenerator[str, None]:
        if self._llm is None:
            yield self._error_event(
                ChatStreamError(
                    code="provider_unavailable",
                    message="El proveedor LLM no está configurado.",
                ),
                1,
            )
            return
        queue: asyncio.Queue[_LLMEvent] = asyncio.Queue(maxsize=32)
        producer = asyncio.create_task(
            self._produce(prepared, self._output_reserve_tokens, queue)
        )
        cancel_event = asyncio.Event()
        _CANCEL_EVENTS[prepared.assistant_message_id] = cancel_event
        buffer = CitationBuffer(len(prepared.sources))
        content = ""
        citations: list[Citation] = []
        tool_calls: list[ToolCallOut] = []
        # A model may repeat the same marker. Validation is a DB round-trip, so reuse the
        # first result for the lifetime of this stream.
        validated_sources: dict[uuid.UUID, RetrievedChunk | None] = {}

        async def validate_cached(
            user_id: uuid.UUID, source: RetrievedChunk
        ) -> RetrievedChunk | None:
            if source.chunk_id not in validated_sources:
                validated_sources[source.chunk_id] = await self._validate_citation(
                    user_id, source
                )
            return validated_sources[source.chunk_id]

        dropped_citations = 0
        terminal = False
        cancelled = False
        producer_error: LLMError | None = None
        timed_out = False
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + self._stream_max_seconds
        last_checkpoint = started_at
        last_checkpoint_content = ""
        last_cancel_poll = started_at
        last_heartbeat = started_at
        last_slot_refresh = started_at
        try:
            while True:
                now = loop.time()
                if now >= deadline:
                    timed_out = True
                    break
                if now - last_slot_refresh >= self._cancel_poll_seconds:
                    last_slot_refresh = now
                    await self._refresh_stream_slot(prepared.user_id)
                if cancel_event.is_set() or now - last_cancel_poll >= self._cancel_poll_seconds:
                    last_cancel_poll = now
                    status = await self._message_status(
                        prepared.user_id, prepared.assistant_message_id
                    )
                    if status != "streaming":
                        cancelled = True
                        break
                tick = max(
                    0.01,
                    min(
                        self._heartbeat_seconds,
                        self._checkpoint_seconds,
                        self._cancel_poll_seconds,
                    ),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=tick)
                except TimeoutError:
                    now = loop.time()
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    if now - last_cancel_poll >= self._cancel_poll_seconds:
                        last_cancel_poll = now
                        if (
                            await self._message_status(
                                prepared.user_id, prepared.assistant_message_id
                            )
                            != "streaming"
                        ):
                            cancelled = True
                            break
                    if content and now - last_checkpoint >= self._checkpoint_seconds:
                        last_checkpoint = now
                        if not await self._checkpoint(
                            prepared, content, citations, tool_calls
                        ):
                            cancelled = True
                            break
                        last_checkpoint_content = content
                    if now - last_heartbeat >= self._heartbeat_seconds:
                        last_heartbeat = now
                        yield heartbeat()
                    continue

                if event.kind == "delta":
                    safe, indices = buffer.feed(event.text)
                    if safe:
                        content += safe
                        yield self._delta_event(safe, len(content))
                    for index in indices:
                        if index > len(prepared.sources):
                            continue
                        source = prepared.sources[index - 1].chunk
                        ref = await resolve_citation(
                            index=index,
                            source=source,
                            offset=len(content),
                            user_id=prepared.user_id,
                            validate=validate_cached,
                        )
                        if ref is None:
                            dropped_citations += 1
                            continue
                        record = citation_record(ref)
                        citations.append(record)
                        yield encode_event(
                            name="citation",
                            id=len(content),
                            data=citation_event(ref),
                        )
                    now = loop.time()
                    if (
                        content
                        and content != last_checkpoint_content
                        and (now - last_checkpoint >= self._checkpoint_seconds)
                    ):
                        last_checkpoint = now
                        if not await self._checkpoint(
                            prepared, content, citations, tool_calls
                        ):
                            cancelled = True
                            break
                        last_checkpoint_content = content
                elif event.kind == "tool":
                    if event.tool is not None:
                        tool_calls.append(event.tool)
                        yield self._tool_event(event.tool, len(content) + 1)
                elif event.kind == "error":
                    producer_error = event.error
                    break
                elif event.kind == "end":
                    tail = buffer.flush()
                    if tail:
                        content += tail
                        yield self._delta_event(tail, len(content))
                    break

            await self._stop_producer(producer)
            if cancelled:
                actual = await self._finalize(
                    prepared, "cancelled", content, citations, tool_calls
                )
                terminal = True
                yield self._done_event(
                    prepared,
                    status=actual,
                    event_id=len(content) + 1,
                    sources=cited_sources(citations),
                    tool_calls=tool_calls,
                )
                return
            if producer_error is not None or timed_out:
                if timed_out or (
                    producer_error is not None
                    and producer_error.details.get("reason") == "stream_timeout"
                ):
                    error = _STREAM_TIMEOUT_ERROR
                else:
                    assert producer_error is not None  # noqa: S101
                    error = _map_llm_error(producer_error)
                actual = await self._finalize(
                    prepared, "failed", content, citations, tool_calls
                )
                terminal = True
                if actual == "cancelled":
                    yield self._done_event(
                        prepared,
                        status="cancelled",
                        event_id=len(content) + 1,
                        sources=cited_sources(citations),
                        tool_calls=tool_calls,
                    )
                else:
                    yield self._error_event(error, len(content) + 1)
                return
            actual = await self._finalize(
                prepared, "completed", content, citations, tool_calls
            )
            terminal = True
            yield self._done_event(
                prepared,
                status=actual,
                event_id=len(content) + 1,
                sources=cited_sources(citations),
                tool_calls=tool_calls,
            )
        finally:
            await self._stop_producer(producer)
            _CANCEL_EVENTS.pop(prepared.assistant_message_id, None)
            if dropped_citations:
                logger.info("chat.citation.dropped count=%d", dropped_citations)
            if not terminal:
                try:
                    # A client disconnect may cancel the generator task; the durable
                    # partial must survive that cancellation.
                    await asyncio.shield(
                        self._finalize(prepared, "cancelled", content, citations, tool_calls)
                    )
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.error("chat.stream.finalize_failed")

    async def _produce(
        self,
        prepared: PreparedStream,
        max_tokens: int,
        queue: asyncio.Queue[_LLMEvent],
    ) -> None:
        assert self._llm is not None  # noqa: S101 - guarded by `_live_events`
        messages = _merge_tool_protocol(prepared.messages)
        budget = self._llm.context_window - max_tokens - self._context_margin_tokens
        messages = _fit_to_budget(messages, budget, self._count_tokens)
        started = time.monotonic()
        deadline = started + self._stream_max_seconds
        try:
            async with asyncio.timeout(self._stream_max_seconds):
                await self._produce_rounds(
                    prepared, max_tokens, queue, messages, started, deadline
                )
        except TimeoutError:
            # The queue may already be full of deltas. Dropping the terminal marker is
            # safe: the consumer applies the same deadline and finalizes the partial text.
            with suppress(asyncio.QueueFull):
                queue.put_nowait(
                    _LLMEvent(
                        kind="error",
                        error=LLMProviderError(
                            "stream_timeout",
                            details={"reason": "stream_timeout"},
                        ),
                    )
                )
        except LLMError as exc:
            await queue.put(_LLMEvent(kind="error", error=exc))
        except Exception as exc:  # noqa: BLE001 - provider adapters must not crash the stream
            logger.error("chat.llm.unexpected error=%s", type(exc).__name__)
            await queue.put(
                _LLMEvent(
                    kind="error",
                    error=LLMProviderError(
                        "El proveedor LLM falló de forma inesperada.",
                        details={"error": type(exc).__name__},
                    ),
                )
            )

    async def _produce_rounds(
        self,
        prepared: PreparedStream,
        max_tokens: int,
        queue: asyncio.Queue[_LLMEvent],
        messages: list[ChatMessage],
        started: float,
        deadline: float,
    ) -> None:
        """Run tool rounds until the model finishes or the stream deadline passes."""
        assert self._llm is not None  # noqa: S101 - guarded by `_produce`
        budget_exhausted = False
        rounds = 0
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError
            if budget_exhausted:
                async for delta in self._llm.stream(messages, max_tokens=max_tokens):
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    await queue.put(_LLMEvent(kind="delta", text=delta))
                break
            buffer = ""
            flushed_any = False
            last_flushed = ""
            tool_executed = False
            async for delta in self._llm.stream(messages, max_tokens=max_tokens):
                if time.monotonic() >= deadline:
                    raise TimeoutError
                buffer += delta
                extracted = extract_tool_call(buffer)
                if extracted is not None:
                    prefix, call = extracted
                    tool_executed = True
                    await self._run_tool(
                        prepared, queue, messages, prefix, call, flushed_any, last_flushed
                    )
                    break
                cut = _safe_prose_cut(buffer, flushed_any=flushed_any)
                if cut > 0:
                    chunk = buffer[:cut]
                    await queue.put(_LLMEvent(kind="delta", text=chunk))
                    buffer = buffer[cut:]
                    flushed_any = True
                    last_flushed = chunk
            if not tool_executed and buffer:
                extracted = extract_tool_call(buffer)
                if extracted is not None:
                    prefix, call = extracted
                    tool_executed = True
                    await self._run_tool(
                        prepared, queue, messages, prefix, call, flushed_any, last_flushed
                    )
                else:
                    await queue.put(_LLMEvent(kind="delta", text=buffer))
            if tool_executed:
                rounds += 1
                if (
                    rounds >= MAX_TOOL_ROUNDS
                    or time.monotonic() - started > TOOL_BUDGET_SECONDS
                ):
                    budget_exhausted = True
                    messages.append(
                        ChatMessage(
                            role="system",
                            content=(
                                "Tool budget exhausted. Answer with the information "
                                "you already have."
                            ),
                        )
                    )
                continue
            break
        await queue.put(_LLMEvent(kind="end"))

    async def _run_tool(
        self,
        prepared: PreparedStream,
        queue: asyncio.Queue[_LLMEvent],
        messages: list[ChatMessage],
        prefix: str,
        call: ToolCall,
        flushed_any: bool,
        last_flushed: str,
    ) -> None:
        """Emit prose separator, run the tool and append the exchange to the prompt."""
        if flushed_any:
            if prefix.strip():
                await queue.put(_LLMEvent(kind="delta", text=prefix.rstrip() + "\n\n"))
            elif not last_flushed.endswith("\n\n"):
                sep = "\n" if last_flushed.endswith("\n") else "\n\n"
                await queue.put(_LLMEvent(kind="delta", text=sep))
        record = await execute_tool(
            self._session_factory,
            user_id=prepared.user_id,
            call=call,
        )
        await queue.put(_LLMEvent(kind="tool", tool=record))
        messages.append(
            ChatMessage(
                role="assistant",
                content=json.dumps(
                    {"tool": call.name, "arguments": dict(call.arguments)},
                    ensure_ascii=False,
                ),
            )
        )
        messages.append(_tool_result_message(record.payload))

    async def _stop_producer(self, producer: asyncio.Task[None]) -> None:
        if producer.done():
            with suppress(asyncio.CancelledError):
                await producer
            return
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer

    async def _replay_events(
        self, prepared: PreparedStream, last_event_id: int
    ) -> AsyncIterator[str]:
        state = prepared.replay
        if state is None:
            return
        offset = min(max(0, last_event_id), len(state.content))
        yield self._snapshot_event(state.message_id, offset, state.status)
        sent = offset
        emitted_citations = len(state.citations)
        for record in sorted(state.citations, key=lambda item: item.offset):
            citation_offset = min(record.offset, len(state.content))
            if citation_offset < offset:
                continue
            if citation_offset > sent:
                yield self._delta_event(state.content[sent:citation_offset], citation_offset)
                sent = citation_offset
            ref = await self._replay_citation_ref(prepared.user_id, record)
            if ref is None:
                continue
            yield encode_event(
                name="citation",
                id=citation_offset,
                data=citation_event(ref),
            )
        if len(state.content) > sent:
            yield self._delta_event(state.content[sent:], len(state.content))
            sent = len(state.content)
        if state.status != "streaming":
            yield self._terminal_from_status(
                prepared,
                state.status,
                sent,
                cited_sources(state.citations),
                state.tool_calls,
            )
            return

        loop = asyncio.get_running_loop()
        started = loop.time()
        # The cap runs from the turn's start, so a reconnect cannot fail a live turn early.
        created = state.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = max(0.0, (datetime.now(UTC) - created).total_seconds())
        deadline = started + self._stream_max_seconds - age
        last_heartbeat = started
        latest_content = state.content
        latest_citations = list(state.citations)
        latest_tool_calls = list(state.tool_calls)
        while True:
            if loop.time() >= deadline:
                actual = await self._finalize(
                    prepared, "failed", latest_content, latest_citations, latest_tool_calls
                )
                if actual == "cancelled":
                    yield self._done_event(
                        prepared,
                        status="cancelled",
                        event_id=sent + 1,
                        sources=cited_sources(latest_citations),
                        tool_calls=latest_tool_calls,
                    )
                else:
                    yield self._error_event(_STREAM_TIMEOUT_ERROR, sent + 1)
                return
            await asyncio.sleep(self._replay_poll_seconds)
            snapshot = await self._read_message(prepared.user_id, prepared.assistant_message_id)
            if snapshot is None:
                yield self._error_event(
                    ChatStreamError(code="internal_error", message="La respuesta ya no existe."),
                    sent + 1,
                )
                return
            new_citations = snapshot.citations[emitted_citations:]
            emitted_citations = len(snapshot.citations)
            for record in sorted(new_citations, key=lambda item: item.offset):
                citation_offset = min(record.offset, len(snapshot.content))
                if citation_offset > sent:
                    yield self._delta_event(snapshot.content[sent:citation_offset], citation_offset)
                    sent = citation_offset
                ref = await self._replay_citation_ref(prepared.user_id, record)
                if ref is None:
                    continue
                # A citation can be persisted after the content checkpoint that already
                # exposed its text. Reusing the last sent offset keeps event ids monotonic.
                yield encode_event(
                    name="citation",
                    id=max(citation_offset, sent),
                    data=citation_event(ref),
                )
            if len(snapshot.content) > sent:
                yield self._delta_event(snapshot.content[sent:], len(snapshot.content))
                sent = len(snapshot.content)
            latest_content = snapshot.content
            latest_citations = list(snapshot.citations)
            latest_tool_calls = list(snapshot.tool_calls)
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= self._heartbeat_seconds:
                last_heartbeat = now
                yield heartbeat()
            if snapshot.status != "streaming":
                yield self._terminal_from_status(
                    prepared,
                    snapshot.status,
                    sent,
                    cited_sources(snapshot.citations),
                    snapshot.tool_calls,
                )
                return
    async def _message_status(self, user_id: uuid.UUID, message_id: uuid.UUID) -> str | None:
        async with self._session_factory() as db:
            status = await db.scalar(
                select(Message.status).where(
                    Message.user_id == user_id,
                    Message.id == message_id,
                )
            )
        return status

    async def _read_message(
        self, user_id: uuid.UUID, message_id: uuid.UUID
    ) -> MessageSnapshot | None:
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(
                        Message.content,
                        Message.status,
                        Message.citas,
                        Message.tool_calls,
                    ).where(
                        Message.user_id == user_id,
                        Message.id == message_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return MessageSnapshot(
            content=row.content,
            status=row.status,
            citations=parse_citations(row.citas),
            tool_calls=tuple(parse_tool_calls(row.tool_calls)),
        )

    async def _checkpoint(
        self,
        prepared: PreparedStream,
        content: str,
        citations: Sequence[Citation],
        tool_calls: Sequence[ToolCallOut],
    ) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                update(Message)
                .where(
                    Message.user_id == prepared.user_id,
                    Message.id == prepared.assistant_message_id,
                    Message.status == "streaming",
                )
                .values(
                    content=content,
                    citas=dump_citations(citations),
                    tool_calls=dump_tool_calls(tool_calls),
                    output_tokens=self._count_tokens(content),
                )
            )
            await db.commit()
            return bool(cast(int, getattr(result, "rowcount", 0)))

    async def _finalize(
        self,
        prepared: PreparedStream,
        status: str,
        content: str,
        citations: Sequence[Citation],
        tool_calls: Sequence[ToolCallOut],
    ) -> str:
        values = {
            "content": content,
            "citas": dump_citations(citations),
            "tool_calls": dump_tool_calls(tool_calls),
            "output_tokens": self._count_tokens(content),
        }
        async with self._session_factory() as db:
            if status == "completed":
                await db.execute(
                    update(Message)
                    .where(
                        Message.user_id == prepared.user_id,
                        Message.id == prepared.assistant_message_id,
                        Message.status == "streaming",
                    )
                    .values(status="completed", **values)
                )
            elif status == "cancelled":
                await db.execute(
                    update(Message)
                    .where(
                        Message.user_id == prepared.user_id,
                        Message.id == prepared.assistant_message_id,
                        Message.status.in_(("streaming", "cancelled")),
                    )
                    .values(status="cancelled", **values)
                )
            else:
                await db.execute(
                    update(Message)
                    .where(
                        Message.user_id == prepared.user_id,
                        Message.id == prepared.assistant_message_id,
                        Message.status == "streaming",
                    )
                    .values(status="failed", **values)
                )
            await db.commit()
            actual = await db.scalar(
                select(Message.status).where(
                    Message.user_id == prepared.user_id,
                    Message.id == prepared.assistant_message_id,
                )
            )
            if actual == "cancelled" and status != "cancelled":
                # Preservation rule: cancellation wins the state race, but the text
                # produced before it must still be durable.
                await db.execute(
                    update(Message)
                    .where(
                        Message.user_id == prepared.user_id,
                        Message.id == prepared.assistant_message_id,
                        Message.status == "cancelled",
                    )
                    .values(**values)
                )
                await db.commit()
            return actual or status

    def _snapshot_event(self, message_id: uuid.UUID, event_id: int, status: str) -> str:
        return encode_event(
            name="snapshot",
            id=event_id,
            data={"message_id": str(message_id), "status": status},
        )

    def _delta_event(self, content: str, event_id: int) -> str:
        return encode_event(name="delta", id=event_id, data={"text": content})

    def _error_event(self, error: ChatStreamError, event_id: int) -> str:
        return encode_event(
            name="error",
            id=event_id,
            data={"code": error.code, "message": error.message},
        )

    def _tool_event(self, tool: ToolCallOut, event_id: int) -> str:
        return encode_event(
            name="tool",
            id=event_id,
            data=tool.model_dump(mode="json"),
        )

    def _done_event(
        self,
        prepared: PreparedStream,
        *,
        status: str,
        event_id: int,
        sources: Sequence[CitedSource] = (),
        tool_calls: Sequence[ToolCallOut] = (),
    ) -> str:
        return encode_event(
            name="done",
            id=event_id,
            data={
                "message_id": str(prepared.assistant_message_id),
                "conversation_id": str(prepared.conversation_id),
                "status": status,
                "sources": [item.model_dump(mode="json") for item in sources],
                "tools": [item.model_dump(mode="json") for item in tool_calls],
                "proposals": [
                    item.payload
                    for item in tool_calls
                    if item.name == "propose_task" and item.ok
                ],
            },
        )

    def _terminal_from_status(
        self,
        prepared: PreparedStream,
        status: str,
        event_id: int,
        sources: Sequence[CitedSource] = (),
        tool_calls: Sequence[ToolCallOut] = (),
    ) -> str:
        if status == "completed":
            return self._done_event(
                prepared,
                status="completed",
                event_id=event_id + 1,
                sources=sources,
                tool_calls=tool_calls,
            )
        if status == "cancelled":
            return self._done_event(
                prepared,
                status="cancelled",
                event_id=event_id + 1,
                sources=sources,
                tool_calls=tool_calls,
            )
        return self._error_event(
            ChatStreamError(
                code="internal_error",
                message="La respuesta terminó con un error.",
            ),
            event_id + 1,
        )


def _map_llm_error(exc: LLMError) -> ChatStreamError:
    if isinstance(exc, LLMRateLimited):
        return ChatStreamError(
            code="rate_limited",
            message="El proveedor LLM rechazó la petición por cuota.",
        )
    if isinstance(exc, LLMContextExceeded):
        return ChatStreamError(
            code="context_exceeded",
            message="La respuesta excede la ventana del modelo.",
        )
    if isinstance(exc, (LLMNotConfigured, LLMProviderError)):
        return ChatStreamError(
            code="provider_unavailable",
            message="El proveedor LLM no está disponible.",
        )
    return ChatStreamError(
        code="internal_error",
        message="No se pudo generar la respuesta.",
    )

