"""L2.5 QA: release of concurrent SSE stream slots on every exit path."""

import uuid
from collections.abc import AsyncGenerator

from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.routers import chat as chat_router


def setup_function() -> None:
    chat_router._active.clear()


def teardown_function() -> None:
    chat_router._active.clear()


async def test_chat_slot_release_on_early_abort() -> None:
    user_id = uuid.uuid4()
    chat_router._acquire_stream(user_id)
    slot = chat_router._Slot(user_id)

    async def events() -> AsyncGenerator[str, None]:
        yield "never iterated"

    stream = events()
    response = StreamingResponse(
        stream,
        media_type="text/event-stream",
        background=BackgroundTask(slot.release),
    )

    assert chat_router._active[user_id] == 1
    background = response.background
    assert background is not None
    await background()
    assert user_id not in chat_router._active
    assert slot.released

    await stream.aclose()


def test_chat_slot_release_is_idempotent() -> None:
    user_id = uuid.uuid4()
    chat_router._acquire_stream(user_id)
    chat_router._acquire_stream(user_id)
    slot = chat_router._Slot(user_id)

    slot.release()
    slot.release()

    assert chat_router._active[user_id] == 1
