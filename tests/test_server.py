"""Server-layer tests using the in-memory fake provider (task 5.5).

These drive the registered low-level handlers directly and assert the on-wire
``CallToolResult`` shape (``isError``, ``structuredContent``, content blocks).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import mcp.types as types
import pytest

from apple_notes_logbook_mcp.core import RawNote
from apple_notes_logbook_mcp.notes import (
    FOLDER_NAME,
    FakeNotesProvider,
    NotesUnavailableError,
    OperationTimeoutError,
    PermissionDeniedError,
)
from apple_notes_logbook_mcp.server import create_server

FIXED_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return FIXED_NOW


def _text(res: types.CallToolResult) -> str:
    """Return the first content block's text, asserting it is a text block."""
    block = res.content[0]
    assert isinstance(block, types.TextContent)
    return block.text


def _server(provider: FakeNotesProvider):
    return create_server(provider, clock=_fixed_clock)


async def _dispatch(handler, req) -> types.ServerResult:
    return await handler(req)


def _call(provider: FakeNotesProvider, name: str, arguments: dict) -> types.CallToolResult:
    server = _server(provider)
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = asyncio.run(_dispatch(handler, req)).root
    assert isinstance(result, types.CallToolResult)
    return result


def _list_tools(provider: FakeNotesProvider) -> types.ListToolsResult:
    server = _server(provider)
    handler = server.request_handlers[types.ListToolsRequest]
    req = types.ListToolsRequest(method="tools/list")
    result = asyncio.run(_dispatch(handler, req)).root
    assert isinstance(result, types.ListToolsResult)
    return result


# --- tool discovery & annotations -----------------------------------------


def test_lists_exactly_two_tools():
    tools = _list_tools(FakeNotesProvider()).tools
    assert sorted(t.name for t in tools) == ["append_log_entry", "read_log"]


def test_tool_annotations_reflect_behavior():
    tools = {t.name: t for t in _list_tools(FakeNotesProvider()).tools}
    read = tools["read_log"].annotations
    append = tools["append_log_entry"].annotations
    assert read is not None and read.readOnlyHint is True and read.idempotentHint is True
    assert append is not None
    assert append.readOnlyHint is False
    assert append.destructiveHint is False
    assert append.idempotentHint is False
    assert append.openWorldHint is True


def test_both_tools_declare_output_schema():
    tools = {t.name: t for t in _list_tools(FakeNotesProvider()).tools}
    assert tools["read_log"].outputSchema is not None
    assert tools["append_log_entry"].outputSchema is not None
    assert tools["read_log"].inputSchema["additionalProperties"] is False
    assert "from" in tools["read_log"].inputSchema["properties"]


# --- append success --------------------------------------------------------


def test_append_success_minimal_structured_content():
    provider = FakeNotesProvider()
    res = _call(provider, "append_log_entry", {"summary": "Hello"})
    assert res.isError is False
    assert res.structuredContent == {"created": True}
    # JSON text block mirrors the structured content; no id/title echoed.
    assert json.loads(_text(res)) == {"created": True}
    assert provider.created_bodies == ["<div>Hello</div>"]


def test_append_with_detail_assembles_body():
    provider = FakeNotesProvider()
    _call(provider, "append_log_entry", {"summary": "Head", "detail": "a\nb"})
    assert provider.created_bodies == ["<div>Head</div><div><br></div><div>a</div><div>b</div>"]


# --- read success ----------------------------------------------------------


def test_read_success_structured_content():
    provider = FakeNotesProvider()
    res = _call(provider, "read_log", {})
    assert res.isError is False
    assert res.structuredContent == {"count": 0, "entries_text": ""}


def test_round_trip_append_then_read():
    provider = FakeNotesProvider(clock=_fixed_clock)
    _call(provider, "append_log_entry", {"summary": "A > B & C", "detail": "why\nmore"})
    res = _call(provider, "read_log", {})
    assert res.isError is False
    assert res.structuredContent is not None
    assert res.structuredContent["count"] == 1
    assert res.structuredContent["entries_text"] == "2026-06-15 — A > B & C\n\nwhy\nmore"


# --- error paths (isError on-wire shape) -----------------------------------


def _assert_error(res: types.CallToolResult, *, contains: str):
    assert res.isError is True
    assert res.structuredContent is None
    assert len(res.content) == 1
    assert contains in _text(res)


def test_append_missing_folder_aborts_and_creates_nothing():
    provider = FakeNotesProvider(folder_present=False)
    res = _call(provider, "append_log_entry", {"summary": "x"})
    _assert_error(res, contains=f"Folder '{FOLDER_NAME}' not found")
    assert provider.created_bodies == []


def test_read_missing_folder_aborts():
    provider = FakeNotesProvider(folder_present=False)
    res = _call(provider, "read_log", {})
    _assert_error(res, contains=f"Folder '{FOLDER_NAME}' not found")


def test_permission_denied_is_actionable():
    provider = FakeNotesProvider(raise_on=PermissionDeniedError())
    res = _call(provider, "append_log_entry", {"summary": "x"})
    _assert_error(res, contains="-1743")
    assert "Automation" in _text(res)


def test_notes_unavailable_is_actionable():
    provider = FakeNotesProvider(raise_on=NotesUnavailableError())
    res = _call(provider, "read_log", {})
    _assert_error(res, contains="-600")


def test_timeout_is_actionable():
    provider = FakeNotesProvider(raise_on=OperationTimeoutError())
    res = _call(provider, "read_log", {})
    _assert_error(res, contains="timed out")


def test_invalid_date_rejected_before_any_read():
    # Provider would raise on any access; a malformed date must short-circuit
    # first, proving no read is attempted.
    provider = FakeNotesProvider(raise_on=OperationTimeoutError())
    res = _call(provider, "read_log", {"from": "14-03-2026"})
    _assert_error(res, contains="YYYY-MM-DD")


@pytest.mark.parametrize("tool", ["append_log_entry", "read_log"])
def test_unknown_parameter_rejected(tool):
    provider = FakeNotesProvider()
    args = {"summary": "x", "bogus": 1} if tool == "append_log_entry" else {"bogus": 1}
    res = _call(provider, tool, args)
    _assert_error(res, contains="validation")
    assert provider.created_bodies == []


def test_empty_string_date_is_rejected_before_any_read():
    provider = FakeNotesProvider(raise_on=OperationTimeoutError())
    res = _call(provider, "read_log", {"from": ""})
    _assert_error(res, contains="YYYY-MM-DD")


# --- hardening: scenarios correct-by-construction now asserted -------------


def _seeded_note(note_id: str) -> RawNote:
    return RawNote(
        note_id, "<div>existing</div>", creation_date=FIXED_NOW, modification_date=FIXED_NOW
    )


def test_append_does_not_modify_existing_notes():
    # Scenario: "Existing notes are never modified".
    provider = FakeNotesProvider(notes=[_seeded_note("old://1")], clock=_fixed_clock)
    original = provider.notes[0]
    _call(provider, "append_log_entry", {"summary": "new entry"})
    assert provider.notes[0] == original  # frozen dataclass, untouched
    assert len(provider.notes) == 2  # exactly one new note added


def test_read_does_not_mutate_notes():
    # Scenario: "Reading does not modify notes" (content + modificationDate).
    provider = FakeNotesProvider(notes=[_seeded_note("n1")], clock=_fixed_clock)
    snapshot = [(n.id, n.body_html, n.modification_date) for n in provider.notes]
    _call(provider, "read_log", {})
    assert [(n.id, n.body_html, n.modification_date) for n in provider.notes] == snapshot


def test_tool_descriptions_present():
    tools = {t.name: t for t in _list_tools(FakeNotesProvider()).tools}
    for name in ("append_log_entry", "read_log"):
        desc = tools[name].description
        assert desc is not None and desc.strip()


def test_stdout_stays_clean_during_tool_calls(capsys):
    # Scenario: "Diagnostics never corrupt the protocol stream" — handlers write
    # nothing to stdout (stdout is reserved for JSON-RPC).
    provider = FakeNotesProvider(clock=_fixed_clock)
    _call(provider, "append_log_entry", {"summary": "x"})
    _call(provider, "read_log", {})
    assert capsys.readouterr().out == ""


def test_round_trip_special_chars_and_filter_through_tools():
    # End-to-end through both tools with '<', non-ASCII, and a from/to range.
    provider = FakeNotesProvider(clock=_fixed_clock)
    _call(provider, "append_log_entry", {"summary": "x < y & z >", "detail": "ä\nö"})
    res = _call(provider, "read_log", {"from": "2026-06-15", "to": "2026-06-15"})
    assert res.structuredContent is not None
    assert res.structuredContent["count"] == 1
    assert res.structuredContent["entries_text"] == "2026-06-15 — x < y & z >\n\nä\nö"
