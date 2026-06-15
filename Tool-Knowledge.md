# Tool-Knowledge

Confirmed quirks and operational knowledge for driving Apple Notes from this
server. This is the "hard-won facts" file — update it whenever real-machine
behavior contradicts an assumption here.

> Legend: ✅ confirmed in code/tests · ⏳ **must be verified on a real Mac with a
> real `Logbook` folder** (see the tasks under change `add-logbook-mcp-server`).

## 1. Automation permission (TCC) & the responsible process

- Talking to Notes sends **Apple Events**, which are gated by macOS **Automation**
  privacy (TCC). The first real call triggers a "… wants to control Notes" prompt.
- The launch chain is **Claude Desktop → uv → python → `/usr/bin/osascript`**.
  macOS attaches the grant to the **responsible process** — not necessarily the
  top-level GUI app (see the observed result below).
- ✅ **Observed responsible process (2026-06-15, this machine): `uv`**
  (`/opt/homebrew/bin/uv`) — *not* Claude Desktop. The **System Settings → Privacy
  & Security → Automation** toggle for Notes appears under **uv**. This is the
  "not guaranteed" case D8 anticipated. The happy path works with the grant on
  `uv`, so a `responsibility_spawnattrs_setdisclaim` wrapper is **not needed** for
  now (revisit only if attribution proves fragile).
- **Implications of the grant living on `uv`:**
  - `uv` is a **shared** launcher (it also starts other MCP servers here, e.g.
    Things), so granting it Notes-control applies to any uv-spawned process.
  - The grant is keyed to uv's binary identity. If `uv` is **reinstalled/upgraded**
    (e.g. `brew upgrade uv`) and its signature changes, macOS may re-prompt —
    re-approve if so. If the prompt stops appearing but calls fail with `-1743`,
    run `tccutil reset AppleEvents` and trigger a tool again.
- Recovery: `tccutil reset AppleEvents` (all) resets the grant so the prompt
  reappears. Scope to a bundle id once known.

## 2. The log folder is a hard prerequisite ✅

- The folder name is **configurable**: env `LOGBOOK_FOLDER`, default **`Logbook`**.
  **This deployment uses `Logbuch`** (set via `LOGBOOK_FOLDER` in the Claude
  Desktop config). The name is resolved once at process start.
- Both tools locate that folder and **never create it**. Missing folder →
  `isError: true` with `Folder '<name>' not found`, nothing created.
- The server selects the **iCloud account explicitly** (`account "iCloud"`,
  falling back to the sole account) so a same-named folder in another account
  (e.g. "On My Mac") is not touched.

## 3. Why `osascript` (and not the alternatives) ✅ (decision)

- **`osascript` subprocess** (chosen): language-agnostic, clean kill-based
  timeouts, isolated stdout/stderr/exit code, values passed via `on run argv`
  (injection-safe).
- **`NSAppleScript` in-process**: rejected — Apple Events are main-thread-affined
  and not concurrency-friendly; no clean kill timeout.
- **ScriptingBridge**: rejected as default — same Apple Events/TCC cost, adds a
  generated header + Obj-C bridging + main-thread issues, flaky note creation.
- **`NoteStore.sqlite` direct**: never — gzip+protobuf, Full Disk Access, schema
  drift, fights CloudKit. Never for writes.
- **`shortcuts run` / JXA / GUI scripting**: rejected — extra artifacts, weaker
  error/timeout control, stagnant, or fragile.

## 4. Dates: `creationDate`, reliability, and the `-1728` fallback ✅

- Entry date = the **date part of `creationDate` in the user's local time zone**.
  No back-dating (`creationDate` is read-only).
- `creationDate` can occasionally fail to read (**Apple Events `-1728`**). The
  bulk-read AppleScript guards each note with `try … on error use modification
  date` **inside the loop**, so one bad note degrades only that entry — the whole
  read never fails for it. (`properties of every note` would fail the whole record
  at once, so it is deliberately not used.)

## 5. `name` vs. the first body line ✅

- A note is created with a **body only**; the read-only `name`/title is derived by
  Notes from the **first body line**. We never set `name` explicitly — doing so
  risks a title that diverges from the first line and breaks the first-line
  guarantee.
- On read, the entry's first line is taken from the (HTML→plain-text) body's first
  line. (Notes' `name` equals it and could be read directly, but deriving from the
  body keeps the round trip self-consistent.)

## 6. Locale-independent date emission ✅

- macOS serializes dates in the user's **regional format** (e.g. DE locale
  `DD.MM.YYYY`), which is brittle to parse. The bulk-read AppleScript instead
  computes and emits each date as a fixed **`YYYY-MM-DDThh:mm:ss`** string (local
  wall-clock), parsed locale-independently in Python as a naive (local) datetime.
- Bulk-read payload uses **ASCII RS (0x1e)** between records and **US (0x1f)**
  between fields — control chars that do not appear in note bodies.

## 7. HTML handling ✅

- **Write**: escape `&` first, then `<`, `>`. Heading is the first `<div>`; a
  single `<div><br></div>` separates heading from detail; each detail line is its
  own `<div>`.
- **Read**: `</div>`/`<br>` → newline; strip remaining tags (incl. `<object>` /
  attachment markup); decode entities with Python's `html.unescape()` so
  `&nbsp;` → **U+00A0** (a NO-BREAK SPACE, *not* an ASCII space) and numeric refs
  resolve; collapse blank-line runs to one; trim. Tags are stripped **before**
  entity decoding so escaped content (`&lt;div&gt;`) decodes to literal text.
- **Confirmed Notes storage quirks (E2E, 2026-06-15):**
  - Notes **pretty-prints a newline between block tags** (stores `</div>\n<div>`).
    The reader drops inter-tag whitespace (`>\s+<` → `><`) **before** converting
    block boundaries — otherwise every line of a multi-line `detail` gets a
    spurious blank line between it. (Found end-to-end; now regression-tested.)
  - Notes **collapses runs of ASCII spaces** in a line to a single space (standard
    HTML whitespace handling), so multiple spaces in `summary`/`detail` do not
    survive verbatim — within the documented whitespace-normalization tolerance.
  - Special and AppleScript-significant characters (`<`, `>`, `&`, `"`, `\`) and
    non-ASCII (`ä ö ü —`) round-trip correctly through real Notes (verified E2E).

## 8. Timeouts: never hang ✅

- Every Notes op runs under an AppleScript `with timeout` set **below**
  AppleScript's 120 s default, and the Python subprocess is killed a few seconds
  later as a hard backstop (write/folder ≈ 30 s AS / 35 s kill; read ≈ 110 s AS /
  115 s kill). A stuck Notes app or a pending permission dialog surfaces as a
  timeout error (`-1712` / subprocess kill), not an indefinite block.

## 9. Binary / config paths ✅

- `osascript`: **`/usr/bin/osascript`** (hard-coded; fixed Apple binary).
- Console entry point: **`apple-notes-logbook-mcp`** (defined in `pyproject.toml`,
  module `apple_notes_logbook_mcp.server:main`).
- Folder name override: env var **`LOGBOOK_FOLDER`** (default `Logbook`), set in
  the Claude Desktop config's `env` block (here: `Logbuch`).
- Claude Desktop config (macOS):
  `~/Library/Application Support/Claude/claude_desktop_config.json`.
- ⏳ **Confirm** the exact `command`/`args` that work in your Claude Desktop on
  first run (whether bare `uv` resolves on its `PATH`, or the absolute
  `/opt/homebrew/bin/uv` is needed) and record it here.

## First-run verification checklist ⏳

1. `tccutil reset AppleEvents`, then create the iCloud log folder (`Logbuch`) and
   set `LOGBOOK_FOLDER` accordingly in the Claude Desktop config.
2. Configure Claude Desktop; confirm it connects and `tools/list` shows exactly
   the two tools.
3. Trigger `append_log_entry`; approve the Automation prompt; **record the
   responsible process** (§1) and confirm the note appears.
4. `append_log_entry` variants: summary only; summary+detail; non-ASCII + `<`,
   `>`, `&`; `&nbsp;` / numeric entity.
5. `read_log` with and without `from`/`to`: chronological dated output, round-trip
   fidelity, and an entry created near local midnight renders its **local**
   calendar date.
6. Confirm append-only: existing notes untouched; `read_log` does not bump any
   `modificationDate`; empty folder → `count` 0.
