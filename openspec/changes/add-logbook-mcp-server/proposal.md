## Why

The Logbook is a behavioral log (decisions, patterns, anti-patterns) kept in an Apple Notes folder and reviewed during the Weekly Review. Today Claude drafts entries and the user pastes them into Notes by hand, and reading the log back for review is manual. This change automates the **write step after the user's approval** and the **read-out for review**, while preserving the deliberate human approval as a behavioral guardrail.

## What Changes

- Introduce a new local MCP server `apple-notes-logbook-mcp` (Python + uv + the official MCP SDK's low-level `Server`, stdio transport, launched by Claude Desktop, macOS-only).
- New tool **`append_log_entry`** — appends a new note to the `Logbook` folder (one note = one entry); "append" means adding a new note to the folder's log, **not** editing or appending text to an existing note.
- New tool **`read_log`** — reads all notes in the folder and consolidates them into a single chronological, dated text block, filterable by `from`/`to` (compared against each note's `creationDate`).
- **Append-only by design:** no update/delete/edit tools; no automatic folder creation (folder must pre-exist, else error); no external list/search (listing is internal to `read_log`).
- Notes access via a single technology: the **`osascript` subprocess** (`on run argv`, injection-safe, hard timeout) — ScriptingBridge is explicitly out of the default.
- Tool results use the current MCP contract: declared `outputSchema` + `structuredContent`, `isError` for recoverable failures (no `{ok:…}` envelope), and tool annotations.
- Engineering baseline ships with the server: pure-core / I/O split for testability, pytest, Ruff, pyright, Lefthook git hooks, GitHub Actions CI, uv-based distribution.

No existing behavior is removed or altered (greenfield), so there are no breaking changes.

## Capabilities

### New Capabilities
- `logbook`: the two MCP tools (`append_log_entry`, `read_log`), the one-note-per-entry storage model, the `Logbook`-folder-existence and error contract, input validation and AppleScript-injection safety, HTML↔plain-text handling, chronological consolidation with date-range filtering (user-local timezone), hard-timeout / surfaced-error (no hang) behavior, and Automation-permission failure handling.

### Modified Capabilities
<!-- None — greenfield project, no existing specs. -->

## Impact

- **New repository / package:** `apple-notes-logbook-mcp` (kebab-case everywhere). Currently only OpenSpec scaffolding exists; this adds the Python package, source, tests, tooling, and CI.
- **New dependencies:** official MCP Python SDK (`mcp`) via uv; dev tooling (Ruff, pyright, pytest, Lefthook). No runtime secrets.
- **Platform:** macOS-only (depends on Apple Notes + Apple Events). No iOS path.
- **External prerequisites:** the user creates the log folder in the iCloud account once (name defaults to `Logbook`, overridable via the `LOGBOOK_FOLDER` env var — this deployment uses `Logbuch`), and grants Automation permission (System Settings → Privacy & Security → Automation) on first run.
- **Host integration:** an entry in `claude_desktop_config.json` launching the server via `uvx`/`uv run`. The Automation grant is expected to attribute to Claude Desktop but must be verified empirically (see design).
