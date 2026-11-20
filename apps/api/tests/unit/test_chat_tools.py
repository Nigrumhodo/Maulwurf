"""D3.8: the chat tool protocol parser is strict and fails closed."""

from app.services.chat_stream import _safe_prose_cut
from app.services.chat_tools import TOOL_PROTOCOL, extract_tool_call, parse_tool_call


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


def test_extract_tool_call_handles_preamble_and_markdown_fences() -> None:
    preamble = (
        "Voy a revisar tus tareas pendientes.\n\n"
        '{"tool": "get_pending_tasks", "arguments": {}}'
    )
    extracted = extract_tool_call(preamble)
    assert extracted is not None
    prefix, call = extracted
    assert "Voy a revisar" in prefix
    assert call.name == "get_pending_tasks"
    assert call.arguments == {}

    fenced = 'Voy a revisar.\n\n```json\n{"tool": "get_pending_tasks", "arguments": {}}\n```'
    fenced_extracted = extract_tool_call(fenced)
    assert fenced_extracted is not None
    assert fenced_extracted[1].name == "get_pending_tasks"

    assert extract_tool_call("El conjunto {1, 2, 3} no es una tool.") is None


def test_safe_prose_cut_streams_short_prose_immediately_and_holds_json_or_fences() -> None:
    assert _safe_prose_cut("parcial", flushed_any=False) == len("parcial")
    assert _safe_prose_cut("   ", flushed_any=False) == 0
    assert _safe_prose_cut('{"tool": ', flushed_any=False) == 0
    assert _safe_prose_cut("```json\n", flushed_any=False) == 0
    assert _safe_prose_cut('Voy a revisar.\n\n{"tool": ', flushed_any=False) == len(
        "Voy a revisar.\n\n"
    )
    assert _safe_prose_cut("El conjunto {1, 2, 3} es finito", flushed_any=False) == len(
        "El conjunto {1, 2, 3} es finito"
    )
