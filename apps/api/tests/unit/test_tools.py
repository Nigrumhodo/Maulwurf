"""L3.6 tool orchestrator: allowlist, tenant, and the provisional round/time cap."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.tools import ToolCall, ToolOrchestrator, admission_error

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_unknown_tools_and_model_tenants_are_rejected() -> None:
    assert (
        admission_error(
            ToolCall("send_email", {}),
            index=0,
            started=NOW,
            now=NOW,
        )
        == "tool_not_allowed"
    )
    assert (
        admission_error(
            ToolCall("get_pending_tasks", {"user_id": str(uuid4())}),
            index=0,
            started=NOW,
            now=NOW,
        )
        == "tenant_rejected"
    )


def test_the_round_and_time_caps_stop_the_batch() -> None:
    call = ToolCall("get_pending_tasks", {})
    assert admission_error(call, index=4, started=NOW, now=NOW) == "tool_rounds_exceeded"
    assert (
        admission_error(call, index=0, started=NOW, now=NOW + timedelta(seconds=16))
        == "tool_budget_exceeded"
    )
    assert admission_error(call, index=3, started=NOW, now=NOW + timedelta(seconds=15)) is None


async def test_the_budget_follows_the_clock_not_the_request_instant() -> None:
    moments = [NOW, NOW + timedelta(seconds=16)]
    tools = ToolOrchestrator(cast(AsyncSession, object()), clock=lambda: moments.pop(0))
    result = await tools.execute(
        (ToolCall("get_pending_tasks", {}),),
        user_id=uuid4(),
        now=NOW - timedelta(days=1),
    )
    assert result[0].ok is False
    assert result[0].payload["code"] == "tool_budget_exceeded"


def test_pending_tasks_handed_to_the_model_are_capped() -> None:
    from app.services.tools import PENDING_TASK_LIMIT, pending_tasks_statement

    statement = pending_tasks_statement(uuid4())
    assert statement._limit_clause.value == PENDING_TASK_LIMIT
