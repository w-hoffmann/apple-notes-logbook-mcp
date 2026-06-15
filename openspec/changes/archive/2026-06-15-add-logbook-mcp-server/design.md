## Context

The server automates the write-after-approval and read-out of the Apple Notes Logbook (see proposal). It is a small, local, macOS-only stdio MCP server launched by Claude Desktop. The original requirements draft assumed Swift + ScriptingBridge; this design revises the stack and access strategy based on review findings: the Notes write/read path is best done via the `osascript` subprocess (language-agnostic), which removes Swift's only hard advantage, and the maintaining team is Python/backend-oriented. Constraints: append-only, no folder creation, exactly two tools, robust against Apple Automation/TCC realities, and free of technical debt for a 1–10 person team.

## Goals / Non-Goals

**Goals:**
- Two reliable tools (`append_log_entry`, `read_log`) that satisfy the `logbook` spec.
- Single, low-debt Notes-access technology with hard timeouts and injection safety.
- Pure, fully unit-testable core logic separated from the Apple Events I/O boundary.
- Current MCP result/error contract (structured output, `isError`, annotations).
- Lean but complete engineering baseline (lint, format, types, tests, hooks, CI) and a simple distribution story for the team.

**Non-Goals:**
- No update/delete/edit, no folder creation, no external list/search.
- No mobile/iOS support; no back-dating of entries.
- No images, checkboxes, or attachments in entries (plain text only).
- No notarized/Developer-ID distribution pipeline (not needed for build/run-from-source).

## Decisions

### D1 — Language & SDK: Python 3.12+ with uv and the official MCP SDK (low-level `Server`)
Use the official MCP Python SDK (`mcp`). Wire the two tools with the SDK's low-level `Server` (`mcp.server.lowlevel.Server`) rather than the high-level FastMCP API.
- **Why the low-level `Server`, not FastMCP:** the `logbook` spec mandates an exact on-wire contract that FastMCP cannot express (both points verified empirically against `mcp` 1.27.2):
  - A parameter named literally **`from`** — a Python keyword, so it cannot be a FastMCP function-signature parameter. A Pydantic `alias="from"` produces the correct schema but breaks FastMCP's call dispatch (`unexpected keyword argument 'from'`), and a single model parameter nests the fields under `params` instead of exposing them at the top level.
  - **`additionalProperties: false`** / rejection of unknown parameters — FastMCP neither emits it nor rejects extra keys (they are silently ignored), so the spec's "reject unknown parameters" requirement would be unmet.
  The low-level `Server` is the *same* official SDK (FastMCP is a layer on top of it) and lets us declare each tool's `inputSchema`, `outputSchema`, and annotations exactly, while its `call_tool` machinery still provides the contract for free: it validates arguments against the declared `inputSchema` (so `additionalProperties: false` is enforced and unknown params are rejected), turns a returned dict into `structuredContent` plus a JSON text block, validates against `outputSchema`, and converts any raised exception into an `isError` result. For a two-tool surface the explicit schemas are short and double as documentation; FastMCP's schema-from-type-hints convenience does not outweigh losing exact wire control here.
- **Why over Swift:** the team is Python-oriented; the Notes path is osascript (see D2), so Swift's native ScriptingBridge edge is moot; the Python SDK is the most mature. Swift's remaining edge (self-contained binary) does not outweigh dev speed/SDK maturity for two tools.
- **Why over TypeScript/Node:** no advantage over Python here unless the team were already a Node shop.

### D2 — Notes access: single-technology `osascript` subprocess
Both create and read go through `/usr/bin/osascript` invoked as a subprocess, with user values passed via an `on run argv` handler (never interpolated into the script source). Each invocation runs under a hard timeout: the AppleScript wraps its logic in `with timeout of N seconds` (N set well below AppleScript's 120 s default) and the Python side kills the subprocess at N + grace. This catches a hang deterministically and surfaces it (errAETimeout `-1712`) rather than blocking the MCP call.
- **Why over `NSAppleScript` in-process:** Apple Events are main-thread-affined and not concurrency-friendly; a subprocess gives clean kill-based timeouts, isolation, and stdout/stderr/exit-code capture.
- **Why over ScriptingBridge (the draft's primary):** SB sends the same Apple Events (same TCC cost) but adds a generated header, Obj-C bridging, and main-thread issues, and is flaky for note creation — a second code path for no benefit. SB stays out of the default; it may return later only as a measured bulk-read fast path for very large folders.
- **Rejected:** direct `NoteStore.sqlite` (gzip+protobuf, Full Disk Access, schema drift, fights CloudKit — never for writes); `shortcuts run` (requires maintaining a separate Shortcut artifact out-of-band, carries the same Apple Events/TCC cost with weaker error/timeout control, and can foreground the GUI); JXA (stagnant, no upside); GUI/AXUIElement scripting and private frameworks (fragile/unsupported).

### D3 — Read strategy: one bulk script, parsed in Python
`read_log` issues a single AppleScript that iterates the notes in the folder and emits a record/field-separated payload (id, first line, creation-date, modification-date, body) in one Apple Event, instead of N per-note round-trips.
- **Per-note resilience:** the script loops with a `try … on error use modification date` guard *inside* the loop so one unreadable `creationDate` (`-1728`) degrades only that entry — a plain `properties of every note` would fail the whole record together, so it is not used.
- **Locale-independent dates:** the script emits each date as an ISO `YYYY-MM-DD` string (computed in AppleScript) or epoch seconds, never a human-formatted AppleScript date string. macOS serializes dates in the user's regional format (e.g. DE locale `DD.MM.YYYY`), which would be brittle to parse in Python; emitting ISO/epoch removes that failure mode.
- **First line source:** the entry's first line is the body's first line. Notes' read-only `name` property equals that first line and may be read directly as the heading; the rest of the body text below it is the detail.
- **Filtering in Python:** `from`/`to` filtering happens in the server after the bulk read, not via AppleScript `whose` clauses (which are slow/unreliable in Notes).
- Python parses the payload into plain DTOs, then the pure core consolidates. `body` is the dominant cost of the bulk read; the `from`/`to` range bounds the rendered output even though all bodies are fetched.

### D4 — Result & error contract
Declare an `outputSchema` per tool and return `structuredContent` on success (plus a JSON-serialized text block for compatibility); schema validation applies to success results only. Recoverable failures surface as a result with `isError: true` and an actionable text message. Mechanically, raising an exception inside the low-level `call_tool` handler (which the SDK converts into an `isError` result with the message visible to the model) is the preferred path; returning an explicit error result is also acceptable. The requirement is on the on-wire shape, not the mechanism. JSON-RPC protocol errors are reserved for malformed requests at the transport layer; note that the low-level SDK converts both a handler-raised exception (e.g. an unknown tool name) and an `inputSchema` validation failure into an `isError` result rather than a method-level JSON-RPC error, so unknown tools and unknown parameters surface as `isError` (acceptable and informative). Input schemas declare `additionalProperties: false`, which the low-level `Server` enforces by validating arguments against the `inputSchema` before dispatch (so unknown parameters are rejected as invalid input); FastMCP would neither emit that constraint nor reject unknown keys. Annotations: `read_log` read-only/idempotent; `append_log_entry` non-destructive/non-idempotent/open-world. All logs go to **stderr** — stdout is exclusively JSON-RPC.

### D5 — Architecture: pure core / I/O boundary split
- `core` — pure functions and value types: HTML→plain-text, HTML escaping, body assembly, date parsing/timezone, date-range filtering, sorting, consolidation rendering. No Apple Events, no MCP. 100% unit-testable.
- `notes` — a `NotesProvider` protocol plus the concrete osascript adapter that maps live Notes data into core DTOs and performs creation. Tests inject an in-memory fake.
- `server` — thin low-level `Server` wiring: tool definitions, input validation, calling core + provider, shaping results.
This is the humble-object/ports-and-adapters pattern; one seam at the Notes boundary, no DI framework.

### D6 — HTML handling
On write: escape `&` first, then `<`, `>`; render `summary` as the first `<div>` line and each `detail` line as its own `<div>`, with a single `<div><br></div>` blank line between heading and detail. The note is created with a **body only** — the `name`/title is read-only and derived by Notes from the first body line, so it is never set explicitly (setting it risks a title that diverges from the body's first line and breaks the first-line guarantee). On read, use Python's stdlib `html.unescape()` for entity decoding (it correctly maps `&nbsp;` → U+00A0 NO-BREAK SPACE and numeric refs to their codepoints — do **not** map `&nbsp;` to an ASCII space), convert `</div>`/`<br>` to newlines, strip remaining tags, ignore `<object>`/attachment markup, then collapse blank-line runs to one and trim. Canonical separator mapping: an empty paragraph `<div><br></div>` decodes to exactly one blank line; any boundary sequence that would yield more than one consecutive blank line is collapsed to one. The escape/decode round-trip is covered by tests (it is fidelity up to whitespace/blank-line normalization, not byte-exact).

### D7 — Dates & timezone
Entry date is the date part of `creationDate` evaluated in the **user's local time zone**; `to` defaults to today (local). The AppleScript emits ISO/epoch values (see D3) so parsing is locale-independent. No back-dating (`creationDate` is read-only). Date inputs are validated as `YYYY-MM-DD`. The local zone is injectable in the core so timezone-boundary behavior is deterministically testable.

### D8 — Distribution & permissions
Run via `uvx`/`uv run` from source in `claude_desktop_config.json`; teammates install with `brew install uv`. No code signing/notarization is required because the binary is built/run from source (no quarantine). The target folder name is deployment configuration: the `LOGBOOK_FOLDER` environment variable (default `Logbook`), set in the config's `env` block — keeping a deployment's German `Logbuch` (or any name) out of the code and spec defaults.
- **TCC attribution (verify, don't assume):** the launch chain is Claude Desktop → uvx → uv → python → `/usr/bin/osascript`. The Automation grant is *expected* to attribute to Claude Desktop (the nearest signed GUI ancestor), but this is **not guaranteed** and MUST be verified empirically on a clean machine (`tccutil reset AppleEvents`) before relying on it. If attribution instead lands on `osascript`/`python`, the README must document granting Automation to that responsible process, and a wrapper using `responsibility_spawnattrs_setdisclaim` may be needed. **Observed (2026-06-15, first real run):** attribution lands on **`uv`** (`/opt/homebrew/bin/uv`), not Claude Desktop; the happy path works with the grant on `uv`, so the disclaim wrapper is not currently needed. README and Tool-Knowledge document granting Notes to `uv` and the re-prompt-on-uv-upgrade caveat.
- The server detects Apple Events `-1743` and surfaces remediation; first-run Automation-permission setup and `tccutil reset AppleEvents` recovery are documented in the README and Tool-Knowledge.

### D9 — Engineering baseline
Ruff (lint + format), pyright (type-check), pytest (tests over the pure core + fake provider), Lefthook (pre-commit: ruff format+lint on staged files; pre-push: pyright + pytest), GitHub Actions CI on a macOS runner (ruff lint, pyright, pytest) as the enforced gate, Dependabot (`uv` + `github-actions`), and standard repo hygiene (README, LICENSE, `.gitignore`, `.editorconfig`, `.python-version`). Branch ruleset on `main` requires the CI check. Tooling is configured-as-code (not specced); test cases realize the spec scenarios.

### D10 — Package layout
`src/`-layout package `apple_notes_logbook_mcp` with modules `server.py`, `core.py`, `notes.py`, and AppleScript snippets passed via argv. `pyproject.toml` defines the `apple-notes-logbook-mcp` console entry point. `requires-python = ">=3.12"`.

## Risks / Trade-offs

- **TCC grant fragility / responsible-process attribution** → Verify empirically (D8); detect `-1743` and surface remediation; document first-run grant, the observed responsible-process name, and `tccutil reset AppleEvents` recovery in Tool-Knowledge.
- **`osascript` per-note / bulk latency** → One bulk read script (D3); scope strictly to the folder; set the AppleScript `with timeout` below 120 s and kill the subprocess on grace; large folders are the most likely `-1712` source, so the timeout must be deterministic.
- **HTML edge cases (nested divs, `<object>` attachments, exotic entities, `&nbsp;`→U+00A0)** → `html.unescape()` + tag strip + ignore attachment markup; plain-text-only constraint is by design; covered by fixture tests.
- **Locale-dependent date serialization** → AppleScript emits ISO/epoch, parsed locale-independently (D3/D7); a non-US-locale fixture test guards this.
- **`creationDate` read can throw (`-1728`)** → per-note `try` with `modificationDate` fallback inside the bulk loop (D3); degrades one entry, never the whole read.
- **Pre-1.0 MCP Python SDK churn / error-result quirks** → pin a known-good version; the two-tool surface is small; a test asserts the on-wire `isError` shape regardless of mechanism.
- **Notes launches/steals focus on first call** → acceptable for a desktop session workflow; documented.

## Migration Plan

Greenfield — no data migration. Deployment:
1. Prerequisite: user creates the log folder in the iCloud account once (name defaults to `Logbook`, overridable via the `LOGBOOK_FOLDER` env var — this deployment uses `Logbuch`).
2. `brew install uv`; clone the repo; `uv sync`.
3. Add an `mcpServers` entry to `claude_desktop_config.json` launching via `uvx`/`uv run`, with an `env` block setting `LOGBOOK_FOLDER` if the folder is not named `Logbook`.
4. First call triggers the macOS Automation prompt; grant Notes control; confirm the observed responsible process.
Rollback: remove the `mcpServers` entry. Nothing is destructive (append-only); no created notes are altered or removed.

## Open Questions

- **`entries_text` size:** should `read_log` gain an optional `limit`/truncation beyond `from`/`to`? Deferred; revisit if review outputs get large.
- **Body read source:** parse HTML `body` (chosen) vs. read the note `plaintext` property where available — revisit if HTML parsing proves lossy in practice.
- **Type checker:** pyright (chosen for speed/defaults) vs. mypy — low-stakes, can switch.
