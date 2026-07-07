# apple-notes-logbook-mcp

[![MCP](https://img.shields.io/badge/MCP-1f6feb)](https://github.com/topics/mcp)
[![Model Context Protocol](https://img.shields.io/badge/Model_Context_Protocol-1f6feb)](https://github.com/topics/model-context-protocol)
[![macOS](https://img.shields.io/badge/macOS-000000?logo=apple&logoColor=white)](https://github.com/topics/macos)
[![Apple Notes](https://img.shields.io/badge/Apple_Notes-FFCC00?logo=apple&logoColor=black)](https://github.com/topics/apple-notes)
[![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)](https://github.com/topics/python)
[![osascript](https://img.shields.io/badge/osascript-555555)](https://github.com/topics/osascript)
[![logbook](https://img.shields.io/badge/logbook-6E5494)](https://github.com/topics/logbook)

A small, local, **macOS-only** [MCP](https://modelcontextprotocol.io) server that
lets an MCP client (e.g. Claude Desktop) **append entries to** and **read back**
an Apple Notes folder named `Logbook`.

The Logbook is a behavioral log (decisions, patterns, anti-patterns) reviewed
weekly. This server automates the write-after-approval and the read-out for
review, while keeping the deliberate human approval in the loop.

It exposes exactly **two tools**:

| Tool | What it does |
| --- | --- |
| `append_log_entry` | Creates **one new note** in the `Logbook` folder. `summary` is the note's first line; optional `detail` is the body below it. Append-only — never edits or deletes existing notes. Reads the new note back in the same operation and returns `{ "created": true, "date": "YYYY-MM-DD" }` — the note's own creation date, confirming it actually persisted. |
| `read_log` | Reads every note in the folder and returns them consolidated into one chronological, dated text block. Optional `from`/`to` (ISO `YYYY-MM-DD`) filter inclusively by each note's creation date in your local time zone (`to` defaults to today). Optional `prefix` returns only entries whose first line starts with that exact, case-sensitive string (e.g. `TECH:`) — the server has no built-in taxonomy. Optional `include_detail` (default `true`); set `false` to return dated headings only, omitting body lines. |

By design there is **no** update/delete/edit tool, **no** automatic folder
creation, and **no** external list/search.

## How it works

```
Claude Desktop ──stdio──▶ apple-notes-logbook-mcp ──subprocess──▶ /usr/bin/osascript ──Apple Events──▶ Notes.app
```

Notes access goes through `osascript` invoked as a subprocess, with all
user-provided values passed as out-of-band arguments (`on run argv`) — never
interpolated into the script source — under a hard timeout. See
[`Tool-Knowledge.md`](./Tool-Knowledge.md) for the confirmed platform quirks and
[`openspec/changes/archive/2026-06-15-add-logbook-mcp-server/design.md`](./openspec/changes/archive/2026-06-15-add-logbook-mcp-server/design.md)
for the full rationale.

## Prerequisites

1. **macOS** with the **Notes** app and an **iCloud** Notes account.
2. **A folder for the log in the iCloud account.** The default name is
   **`Logbook`**; set the `LOGBOOK_FOLDER` environment variable to use another
   name (this setup uses **`Logbuch`**). The server **does not create the folder**
   — create it once in Notes (iCloud → New Folder). If it is missing, both tools
   return an error and create nothing.
3. **[uv](https://docs.astral.sh/uv/)**: `brew install uv`.

## Install

```bash
git clone <this-repo>
cd apple-notes-logbook-mcp
make setup          # uv sync + install git hooks (or just `uv sync`)
```

## Configure Claude Desktop

Add an entry to `claude_desktop_config.json` (on macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`). Use the
**absolute path** to this checkout:

```json
{
  "mcpServers": {
    "apple-notes-logbook": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/apple-notes-logbook-mcp", "apple-notes-logbook-mcp"],
      "env": { "LOGBOOK_FOLDER": "Logbuch" }
    }
  }
}
```

`uv` must be on Claude Desktop's `PATH`. If it is not, use the absolute path to
`uv` (find it with `which uv`, e.g. `/opt/homebrew/bin/uv`) as `command`.

The `env` block sets the target folder name. Omit it to use the default
`Logbook`; set `LOGBOOK_FOLDER` to match your actual folder (here: `Logbuch`).

Restart Claude Desktop. The server should connect and `tools/list` should show
exactly `append_log_entry` and `read_log`.

## Permissions / First run

The **first** time a tool actually talks to Notes, macOS shows an **Automation**
permission prompt ("… wants to control Notes"). Click **OK**. The grant is
recorded under **System Settings → Privacy & Security → Automation**.

> **Which app does the grant attach to?** The launch chain is
> Claude Desktop → uv → python → `/usr/bin/osascript`, and macOS attributes the
> grant to the *responsible process*. **Observed on this setup: the grant attaches
> to `uv`** (`/opt/homebrew/bin/uv`), not Claude Desktop — so the Automation toggle
> for Notes appears under **uv**; enable it there. Because `uv` is a shared
> launcher, this also covers other uv-started MCP servers. If you upgrade `uv`
> (`brew upgrade uv`) the prompt may reappear — just re-approve. See
> [`Tool-Knowledge.md`](./Tool-Knowledge.md) §1 for details.

If you denied the prompt, or the toggle is missing, reset and retry:

```bash
tccutil reset AppleEvents            # reset all Apple Events grants
# or, scoped to the responsible app once you know its bundle id:
# tccutil reset AppleEvents <bundle-id>
```

Then trigger a tool again to re-prompt. Errors the server surfaces with
remediation:

| Symptom | Apple Events error | What to do |
| --- | --- | --- |
| "Not authorised to control Apple Notes" | `-1743` (or unparseable stderr containing "not authorized to send apple events") | Grant Automation → Notes for the controlling app; or `tccutil reset AppleEvents`. |
| "Apple Notes could not be reached" | `-600` (not running) or `-609` (connection invalid) | Make sure Notes is installed and can launch. |
| "The Notes operation timed out" | `-1712` / subprocess kill | Approve any pending permission dialog; very large folders can also time out. |
| "Folder 'Logbook' not found" | — | The message also lists the folders that *do* exist in the iCloud account (or says none were found), so a rename/typo is diagnosable without opening Notes. Fix `LOGBOOK_FOLDER` or rename the folder to match. |

`append_log_entry` is **at-least-once**, not exactly-once: if the create operation fails
after the note may already have been made (e.g. a timeout), the error message says so and
the server does **not** automatically retry (to avoid a duplicate) — check the folder
before retrying by hand.

## Development

```bash
make check          # ruff lint + pyright + pytest (mirrors CI)
make test           # pytest only
make lint           # ruff check
make format         # ruff format
make typecheck      # pyright
```

The code is split into a pure, fully unit-tested **core** (HTML escaping/parsing,
dates, consolidation), a **notes** I/O boundary (the `osascript` adapter plus an
in-memory fake), and a thin **server** layer. CI runs the same gate on a macOS
runner.

## Limitations

- macOS only; no iOS.
- Plain text only — no images, checkboxes, or attachments in entries.
- No back-dating: a note's date is its Notes-assigned `creationDate`.
- Round-trip fidelity is up to whitespace / blank-line normalization.

## License

MIT — see [LICENSE](./LICENSE).
