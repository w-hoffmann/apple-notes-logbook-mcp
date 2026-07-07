# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, **macOS-only** MCP server (Python + uv, official MCP SDK) that drives an Apple
Notes folder as an append-only behavioral logbook. It exposes exactly **two tools** over
stdio — `append_log_entry` (create one note per entry) and `read_log` (consolidate the
folder into one chronological, date-filterable text block). By design there is no
update/delete/edit tool, no folder creation, and no external list/search.

## Commands

```bash
make setup       # uv sync + install Lefthook git hooks (one-time bootstrap)
make check       # ruff lint + ruff format --check + pyright + pytest — the full gate, mirrors CI
make test        # pytest only
make lint        # ruff check .
make format      # ruff format .
make typecheck   # pyright
make run         # run the stdio server locally (uv run apple-notes-logbook-mcp)
```

Run a single test with pytest directly through uv:

```bash
uv run pytest tests/test_core.py::test_consolidate_orders_ascending_with_dated_headings
uv run pytest -k "timezone"
```

Toolchain: Python **3.12+**, `uv` for deps/run, Ruff (lint+format), pyright (`standard`
mode), pytest. CI (`.github/workflows/ci.yml`) runs the same gate on a **macOS** runner.
Lefthook runs ruff on pre-commit and pyright+pytest on pre-push.

## Architecture

Three layers, deliberately separated so the logic is testable without a live Notes app:

- **`core.py` — pure, deterministic, no I/O.** HTML escaping + note-body assembly (write
  path), HTML→plain-text conversion (read path), strict ISO date parsing, and
  filter/sort/render consolidation. The user's time zone and "today" are **injected**, so
  timezone-boundary behavior is unit-testable. Knows nothing about Apple Events or MCP.
- **`notes.py` — the only place that touches Apple Events.** `NotesProvider` (a `Protocol`)
  abstracts `folder_exists` / `create_note` / `read_notes`. `OsascriptNotesProvider` is the
  real adapter; `FakeNotesProvider` is an in-memory double used by core/server tests (a
  write-then-read round trip works end to end without Notes). Recoverable failures are
  raised as `NotesError` subclasses carrying user-actionable messages.
- **`server.py` — thin MCP wiring.** Tool definitions (exact `inputSchema`/`outputSchema`/
  annotations), and `do_append`/`do_read` which take an injected provider + clock and
  **raise** on recoverable errors. `create_server(provider, clock=...)` is the seam tests
  use.

Data flow: `Claude Desktop --stdio--> server --> OsascriptNotesProvider --subprocess-->
/usr/bin/osascript --Apple Events--> Notes.app`.

## Invariants that are easy to break

- **Low-level `Server`, not FastMCP.** FastMCP derives the input schema from the function
  signature, which cannot express two hard spec requirements: a parameter literally named
  `from` (a Python keyword) and top-level `additionalProperties: false`. Don't "simplify"
  to FastMCP.
- **Error contract.** Recoverable failures (missing folder, missing Automation permission,
  Notes unavailable, timeout, invalid date) are **raised**; the SDK's `call_tool` converts
  any raised exception into an `isError` result. Success returns a dict matching the tool's
  `outputSchema` (the SDK emits `structuredContent` + a JSON text block). Never invent an
  `{ "ok": false }` envelope.
- **stdout is reserved for JSON-RPC.** All logging/diagnostics go to **stderr** only
  (`main()` sets `logging.basicConfig(stream=sys.stderr)`). Printing to stdout corrupts the
  protocol.
- **Never interpolate user input into AppleScript source.** All user values (folder name,
  body, dates) pass via `on run argv`; the script source is static. This is the injection
  boundary — keep it.
- **Append-only / folder pre-exists.** Notes are created body-only (Notes derives the
  title/`name` from the first line — never set `name`). The target folder is never created;
  if missing, both tools error. The **iCloud** account is selected explicitly so a
  same-named folder in another account isn't touched.
- **HTML handling order matters.** Write: escape `&` first, then `<`/`>`. Read: drop
  inter-tag whitespace (`>\s+<`) → convert `</div>`/`<br>` to newlines → strip remaining
  tags → **then** `html.unescape` (so escaped `&lt;div&gt;` decodes to literal text, and
  `&nbsp;` becomes U+00A0, not an ASCII space). See `Tool-Knowledge.md` §7 for the
  real-machine quirks these steps defend against.
- **`creationDate` fallback is inside the AppleScript read loop.** A per-note
  `try … on error use modification date` keeps one unreadable date (Apple Events `-1728`)
  from failing the whole read. Dates are emitted as locale-independent
  `YYYY-MM-DDThh:mm:ss` (parsed naive/local); records are RS/US (`0x1e`/`0x1f`) delimited.
- **Timeouts everywhere.** Each op has an AppleScript `with timeout` below the 120 s
  default plus a subprocess-kill backstop, so a stuck Notes app or pending permission
  dialog surfaces as an error instead of hanging.

## Configuration

- **`LOGBOOK_FOLDER`** env var sets the target folder name (default `Logbook`), resolved
  once at import. This deployment uses `Logbuch`, set in the Claude Desktop config `env`
  block.
- First real Notes call triggers a macOS **Automation** (TCC) prompt. On this machine the
  grant attaches to **`uv`** (the responsible process), not Claude Desktop — see
  `Tool-Knowledge.md` §1.

## Specs & workflow (OpenSpec)

Behavior is specified under `openspec/`. The normative source of truth for this capability
is **`openspec/specs/logbook/spec.md`** (requirements + scenarios). Changes are developed as
proposals under `openspec/changes/<name>/` and moved to `openspec/changes/archive/` when
done (`openspec list` shows active changes; `/opsx:*` skills drive the workflow). **When you
change observable behavior, update the spec** — code and spec are expected to stay in sync.

`Tool-Knowledge.md` is the "hard-won facts" file for real-machine Apple Notes behavior;
update it whenever observed behavior contradicts an assumption there.

## Commit conventions

- **No Claude/Anthropic references.** Commit messages never mention Claude or Anthropic and
  never include a `Co-Authored-By: Claude` (or similar) trailer, regardless of what tooling
  produced the change.
- **Body as bullet points, not prose.** For any commit touching more than one thing, write the
  body as a short bullet list — one change per line — not a paragraph; it stays scannable in
  `git log`. Reserve prose for genuinely single-point commits.
- **Subject line:** short, imperative, describes the net effect (e.g. "Harden append/read
  confirmation and add read_log projection filters"), not a change-proposal name or task ID.
- **New commits over amends.** Prefer a new commit over `git commit --amend`; only amend when
  explicitly asked to.
