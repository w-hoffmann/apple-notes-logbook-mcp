"""MCP server wiring: tool definitions, validation, and result shaping.

Uses the official MCP SDK's low-level :class:`~mcp.server.lowlevel.Server`
rather than FastMCP. FastMCP derives the input schema from the function
signature, which cannot express two hard requirements of the ``logbook`` spec:
a parameter literally named ``from`` (a Python keyword) and a top-level
``additionalProperties: false``. The low-level ``Server`` is the same official
SDK and lets us declare both tool schemas, ``outputSchema``, and annotations
exactly, while its ``call_tool`` machinery gives us the required on-wire
contract for free: input-schema validation (unknown params rejected),
``structuredContent`` plus a JSON text block on success, ``outputSchema``
validation, and ``isError`` results for any raised exception (design D4).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import core
from .notes import FOLDER_NAME, NotesProvider, OsascriptNotesProvider

logger = logging.getLogger("apple_notes_logbook_mcp")

SERVER_NAME = "apple-notes-logbook-mcp"

Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    """Current instant as a timezone-aware datetime in the user's local zone."""
    return datetime.now().astimezone()


# ---------------------------------------------------------------------------
# Tool definitions (exact on-wire schemas + annotations)
# ---------------------------------------------------------------------------

APPEND_TOOL = types.Tool(
    name="append_log_entry",
    title="Append Logbook entry",
    description=(
        f"Append one new note to the Apple Notes '{FOLDER_NAME}' folder (one note = "
        "one entry). 'summary' becomes the note's first line (the heading); optional "
        "'detail' becomes the body below it. Does not edit existing notes."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "The entry heading; becomes the note's first line.",
            },
            "detail": {
                "type": "string",
                "description": "Optional body text below the heading; may be multi-line.",
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
    outputSchema={
        "type": "object",
        "properties": {"created": {"type": "boolean"}},
        "required": ["created"],
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)

READ_TOOL = types.Tool(
    name="read_log",
    title="Read Logbook",
    description=(
        f"Read every note in the '{FOLDER_NAME}' folder and return them consolidated "
        "into one chronological, dated text block. Optional 'from'/'to' (ISO "
        "YYYY-MM-DD) filter inclusively by each note's creation date in your local "
        "time zone; 'to' defaults to today. Read-only."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "from": {
                "type": "string",
                "description": "Inclusive lower bound, ISO YYYY-MM-DD. No lower bound if omitted.",
            },
            "to": {
                "type": "string",
                "description": "Inclusive upper bound, ISO YYYY-MM-DD. Defaults to today.",
            },
        },
        "additionalProperties": False,
    },
    outputSchema={
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "entries_text": {"type": "string"},
        },
        "required": ["count", "entries_text"],
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)


# ---------------------------------------------------------------------------
# Tool logic (pure-ish: provider + clock injected; raises on recoverable error)
# ---------------------------------------------------------------------------


def do_append(provider: NotesProvider, arguments: dict[str, Any]) -> dict[str, Any]:
    summary = arguments["summary"]
    detail = arguments.get("detail")
    if not provider.folder_exists():
        raise _folder_missing()
    body = core.assemble_body(summary, detail)
    provider.create_note(body)
    return {"created": True}


def do_read(provider: NotesProvider, arguments: dict[str, Any], *, clock: Clock) -> dict[str, Any]:
    # Validate dates first so a malformed value is rejected before any read.
    frm = core.parse_iso_date(arguments["from"]) if arguments.get("from") else None
    to = core.parse_iso_date(arguments["to"]) if arguments.get("to") else None

    if not provider.folder_exists():
        raise _folder_missing()

    now = clock()
    tz = now.tzinfo
    assert tz is not None  # _default_clock / injected clocks are tz-aware
    notes = provider.read_notes()
    result = core.consolidate(notes, tz=tz, today=now.date(), frm=frm, to=to)
    return {"count": result.count, "entries_text": result.entries_text}


def _folder_missing() -> Exception:
    from .notes import FolderNotFoundError

    return FolderNotFoundError(FOLDER_NAME)


# ---------------------------------------------------------------------------
# Server assembly
# ---------------------------------------------------------------------------


def create_server(provider: NotesProvider, *, clock: Clock | None = None) -> Server:
    """Build a configured low-level MCP ``Server`` bound to ``provider``."""
    the_clock = clock or _default_clock
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [APPEND_TOOL, READ_TOOL]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # Raised exceptions are converted to isError results by the SDK; the
        # handler never crashes the server (spec: failures surfaced, not silent).
        if name == "append_log_entry":
            return do_append(provider, arguments)
        if name == "read_log":
            return do_read(provider, arguments, clock=the_clock)
        raise ValueError(f"Unknown tool: {name}")

    return server


async def _run() -> None:
    provider = OsascriptNotesProvider()
    server = create_server(provider)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Console entry point: run the stdio server with logging on stderr only."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is reserved for JSON-RPC.
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting %s (stdio)", SERVER_NAME)
    anyio.run(_run)


if __name__ == "__main__":
    main()
