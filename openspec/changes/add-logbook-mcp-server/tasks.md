## 1. Project scaffold & engineering baseline

- [x] 1.1 Initialize uv project with `pyproject.toml`: package `apple_notes_logbook_mcp` (src-layout), console entry point `apple-notes-logbook-mcp`, `requires-python = ">=3.12"`, dependency on the official `mcp` SDK (pinned)
- [x] 1.2 Add `.python-version`, `.gitignore` (`.venv/`, `__pycache__/`, build artifacts), `.editorconfig`, and `LICENSE` (MIT)
- [x] 1.3 Configure Ruff (lint + format) and pyright in `pyproject.toml`/config
- [x] 1.4 Add pytest and a `tests/` package; `uv run pytest` runs (even if empty)
- [x] 1.5 Add `lefthook.yml` (pre-commit: ruff format + lint on staged files; pre-push: pyright + pytest) and document `lefthook install` in a `make setup`/bootstrap step
- [x] 1.6 Add GitHub Actions CI (`.github/workflows/ci.yml`) on a macOS runner: ruff lint, pyright, pytest; add `.github/dependabot.yml` (`uv` + `github-actions`)

## 2. Walking skeleton (de-risk MCP transport + Apple Automation)

- [x] 2.1 Implement a minimal stdio MCP server (official SDK low-level `Server`) that starts and registers `append_log_entry` and `read_log` (stubs), with all logging routed to stderr only
- [x] 2.2 Implement the `NotesProvider` protocol and a thin osascript adapter that can resolve the `Logbook` folder in the iCloud account (read-only smoke operation)
- [x] 2.3 Document and verify the `claude_desktop_config.json` entry (`uvx`/`uv run`); confirm the server connects and `tools/list` shows exactly the two tools
- [x] 2.4 Trigger and verify the macOS Automation permission flow against a real `Logbook` folder; record the **observed responsible-process name** (the app the grant attaches to) and confirm the happy path works end to end with the stub provider — **observed: grant attaches to `uv`**, happy path verified in Claude Desktop

## 3. Pure core logic (no Apple Notes; fully unit-tested)

- [x] 3.1 Implement HTML escaping for write (`&` first, then `<`, `>`) and body assembly (heading as first `<div>`, single `<div><br></div>` separator, each detail line as its own `<div>`; collapse newlines in summary; body-only, never set `name`)
- [x] 3.2 Implement HTML→plain-text for read using `html.unescape()` (decode `&amp;`/`&lt;`/`&gt;`/`&nbsp;`→U+00A0/numeric refs), convert `</div>`/`<br>` to newlines, strip tags, ignore `<object>`/attachment markup, collapse blank-line runs to one (`<div><br></div>` → exactly one blank line), trim
- [x] 3.3 Implement date handling: parse/validate `YYYY-MM-DD`, derive date part of `creationDate` in user-local timezone (injectable for tests), inclusive `from`/`to` filtering with `to` defaulting to today, filtering done in the core (not AppleScript)
- [x] 3.4 Implement consolidation: sort entries ascending by `creationDate`, render `YYYY-MM-DD — {first line}` + detail lines, blank line between entries, return `count` + `entries_text`
- [x] 3.5 Define plain note DTOs and write unit tests for 3.1–3.4 including: non-ASCII; `<`, `>`, and `&`; numeric-entity and `&nbsp;`→U+00A0 decode; multi-line detail; empty body; empty folder; timezone-boundary instant (UTC vs local date); `creationDate`→`modificationDate` fallback; and the write→read round-trip (incl. `>`)

## 4. Notes I/O via osascript

- [x] 4.1 Implement folder-existence check returning a clear "Folder 'Logbook' not found" signal; select the iCloud account explicitly when multiple accounts exist
- [x] 4.2 Implement note creation via `osascript` with values passed through `on run argv` (no source interpolation); set body only (do not set `name`); confirm AppleScript-significant characters (`"`, `\`, newlines) are safe
- [x] 4.3 Implement bulk read: one script looping notes in the folder, emitting record/field-separated id, first line (note `name`), creation-date and modification-date as ISO/epoch (locale-independent), and body; per-note `try … on error use modification date` guard inside the loop; parse into DTOs in Python
- [x] 4.4 Wrap every Notes operation in a hard timeout (AppleScript `with timeout` < 120 s plus subprocess kill on grace) and map Apple Events errors (`-1743`, `-600`, `-1712`, `-1728`) to actionable messages / fallback
- [x] 4.5 Add an in-memory fake `NotesProvider` for tests of the server layer

## 5. Wire the tools (MCP contract)

- [x] 5.1 Implement `append_log_entry`: validate inputs (reject unknown fields via `additionalProperties: false`), check folder, assemble body, create note; return minimal success; recoverable failures as `isError: true`
- [x] 5.2 Implement `read_log`: validate `from`/`to` and reject unknown fields, check folder, bulk-read, filter, consolidate; return `count` + `entries_text`
- [x] 5.3 Declare `outputSchema` for **both** tools and return `structuredContent` plus a JSON text block on success; set tool annotations (read_log read-only/idempotent; append non-destructive/non-idempotent/open-world)
- [x] 5.4 Ensure all recoverable failures (missing folder, permission `-1743`, Notes unavailable `-600`, timeout `-1712`, invalid date) return `isError: true` with remediation (raise `ToolError` or return an error result) and never crash the handler
- [x] 5.5 Add server-layer tests using the fake provider covering success and each error path, and asserting the on-wire `isError` shape

## 6. Docs & end-to-end verification

- [x] 6.1 Write README: purpose, prerequisites (`Logbook` folder, `brew install uv`), install, `claude_desktop_config.json` setup, and a "Permissions / First Run" section (Automation grant, observed responsible process, `tccutil reset AppleEvents` recovery)
- [x] 6.2 Add `Tool-Knowledge.md` capturing confirmed quirks (Automation permission + responsible process, folder prerequisite, osascript vs alternatives, `creationDate` reliability/`-1728` fallback, `name` vs first-body-line, locale-independent date emit, actual binary/config path)
- [x] 6.3 Manual end-to-end check against the real `Logbook` folder: append (summary only; summary+detail; non-ASCII + `<`, `>`, `&`; `&nbsp;`/numeric entity), then `read_log` with and without `from`/`to`; verify chronological dated output, round-trip fidelity, and that an entry created near local midnight renders its local calendar date — **done via CLI against real `Logbuch`**; found & fixed a real bug (Notes' inter-tag `\n` → spurious blank lines in multi-line detail, now regression-tested). Note: `&nbsp;`/numeric-entity decode and the local-midnight boundary are covered by unit tests (cannot be provoked via append — Notes collapses spaces; `creationDate` cannot be back-dated)
- [x] 6.4 Verify acceptance: exactly two tools listed; missing-folder aborts both with no creation; empty folder returns `count` 0; append-only (existing notes untouched, `read_log` does not bump `modificationDate`); CI green — **all verified: two tools ✅, missing-folder aborts both with no creation ✅, append-only / `read_log` does not bump `modificationDate` ✅ (real Notes), empty-folder→`count` 0 (unit), CI green ✅ (macOS runner, run 27549122682).**
