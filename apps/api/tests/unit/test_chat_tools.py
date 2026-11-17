"""D3.8: the chat tool protocol parser is strict and fails closed."""

from app.services.chat_tools import TOOL_PROTOCOL, parse_tool_call


def test_parse_tool_call_accepts_one_json_object() -> None:
    call = parse_tool_call('{"tool": "get_pending_tasks", "arguments": {}}')

    assert call is not None
    assert call.name == "get_pending_tasks"
    assert call.arguments == {}


def test_parse_tool_call_keeps_the_proposal_arguments() -> None:
    call = parse_tool_call(
        '{"tool": "propose_task", "arguments": {"titulo": "Repasar", "sources": []}}'
    )

    assert call is not None
    assert call.name == "propose_task"
    assert call.arguments["titulo"] == "Repasar"


def test_parse_tool_call_rejects_prose_and_malformed_objects() -> None:
    assert parse_tool_call("Según tu agenda, tienes un examen.") is None
    assert parse_tool_call('{"arguments": {}}') is None
    assert parse_tool_call('{"tool": 3, "arguments": {}}') is None
    assert parse_tool_call('{"tool": "x", "arguments": []}') is None
    assert parse_tool_call("{not json}") is None


def test_protocol_lists_only_the_read_only_allowlist() -> None:
    assert "get_calendar_events" in TOOL_PROTOCOL
    assert "get_pending_tasks" in TOOL_PROTOCOL
    assert "propose_task" in TOOL_PROTOCOL
    assert "never writes to Calendar" in TOOL_PROTOCOL
