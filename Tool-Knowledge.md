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

### Apple Events error numbers ✅ (web research, 2026-07-07)

| Number | Meaning | Server mapping |
| --- | --- | --- |
| `-1743` | `errAEEventNotPermitted` — Automation denied/not yet granted | `PermissionDeniedError` |
| `-1728` | no such object — the specific property (e.g. `creation date`) couldn't be read | per-note/per-create fallback to `modification date` (not an abort) |
| `-600` | `procNotFound` — the target app isn't running / can't be reached | `NotesUnavailableError` |
| `-609` | connection to the application is invalid (Notes quit mid-operation) | `NotesUnavailableError` (same remedy as `-600`) |
| `-1712` | Apple Event timed out | `OperationTimeoutError` |

- **`-10004` is *not* the TCC-denial signal.** It is a Standard-Suite privilege
  error (a different failure class); do not treat it as equivalent to `-1743`
  when classifying stderr.
- **A denied Automation grant is never a silent success.** `-1743` always
  raises (non-zero exit + stderr); the only ways `osascript` can exit 0 with
  "nothing happened" are a bare `try` that swallows the error or `ignoring
  application responses` — neither is used anywhere in this server's scripts,
  so a denied grant cannot masquerade as `{ "created": true }`.
- **No pre-flight permission probe.** `AEDeterminePermissionToAutomateTarget`
  (`askUserIfNeeded=false`) cannot distinguish "denied" from "undecided" —
  both return `-1744` — and has a documented hang bug. It would also need a
  PyObjC/C bridge for no reliable gain. Decision: keep attempt-and-classify
  (run the operation, classify the failure) rather than probing first.
- **Classification is number-first, substring-fallback-second.** Error numbers
  are locale-independent (this deployment runs a German locale); a text
  substring (`"not authorized to send apple events"`, case-insensitive) is
  only consulted when no `(-NNNN)` could be parsed at all, to catch an
  unexpected stderr shape without becoming the primary signal.

## 2. The log folder is a hard prerequisite ✅

- The folder name is **configurable**: env `LOGBOOK_FOLDER`, default **`Logbook`**.
  **This deployment uses `Logbuch`** (set via `LOGBOOK_FOLDER` in the Claude
  Desktop config). The name is resolved once at process start.
- Both tools locate that folder and **never create it**. Missing folder →
  `isError: true` with `Folder '<name>' not found`, nothing created.
- The server selects the **iCloud account explicitly** (`account "iCloud"`,
  falling back to the sole account) so a same-named folder in another account
  (e.g. "On My Mac") is not touched.
- **Self-diagnosing folder-missing message ✅.** On the missing-folder branch,
  the same Apple Event (create or read) also enumerates `name of every folder`
  of the target account and raises the list alongside the marker, FS-joined.
  `_classify` strips osascript's `execution error: … (-NNNN).` wrapper before
  splitting on FS (the FS control character itself survives to stderr intact —
  confirmed on this machine; the gotcha is the trailing `(-NNNN)` glued onto
  the *last* folder name, which must be stripped first). Rendered message:
  `Folder 'Logbuch' not found in the iCloud account. Existing folders: 'Claude
  Logbuch', 'Notizen'. Check LOGBOOK_FOLDER (or rename the folder) and try
  again.` (or "No folders were found in the iCloud account." when empty). This
  costs nothing on the happy path — enumeration only runs once already
  failing — and if Automation is denied, the enumeration itself raises
  `-1743` first, so the permission error still wins.

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
- **Write path: create-then-confirm, same guard ✅.** `append_log_entry`'s
  AppleScript binds the just-created note and reads its date back **in the
  same Apple Event**, guarded exactly like the read loop: `try` creation date →
  `on error try` modification date → `""` if both fail. The returned `date` is
  therefore always a value read from Notes — **never the server's own clock**
  — so it is the same value `read_log` will later emit for that note, and it
  doubles as existence proof: an empty/malformed read-back is treated as
  failure (`isError`) even if the underlying process exited 0, closing the
  last silent-write path.
- **At-least-once, not exactly-once ✅ (accepted trade-off).** `make new note`
  can, in principle, create the note and then have the operation time out
  before the date read-back returns (AppleScript's own `with timeout`, or the
  subprocess-kill backstop) — this one ambiguity cannot be closed in-script.
  `create_note` re-raises exactly that case as `AmbiguousCreateError` (never
  the generic `OperationTimeoutError`), whose message notes the entry may or
  may not have been created and that the server does **not auto-retry** — a
  blind retry is what would create a duplicate. Every *other* create failure
  (folder-missing, permission denied, Notes unavailable) is established
  before any Apple Event that could have created the note, so it propagates
  unchanged, with no ambiguity wording. `read_notes` never creates anything,
  so its timeout stays the plain `OperationTimeoutError` too. Dedup is
  deliberately out of scope — a rare visible duplicate is benign for an
  append-only logbook; a lost entry is not.
- **Exact `-1743` stderr form (observed):** `execution error: Not authorised to
  send Apple events (-1743).` — the trailing `(-NNNN).` is what
  `_parse_folder_list`/`_TRAILING_AE_CODE_RE` strip when parsing the
  folder-missing payload; classification itself keys on the number via
  `_extract_ae_number`, not this exact string.

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
  later as a hard backstop (write ≈ 30 s AS / 35 s kill; read ≈ 110 s AS /
  115 s kill). A stuck Notes app or a pending permission dialog surfaces as a
  timeout error (`-1712` / subprocess kill), not an indefinite block.
- There is no separate `folder_exists` operation (removed as dead weight):
  the missing-folder check is inline in the create/read script itself, so
  folder-missing detection shares the same op's timeout rather than paying
  for a second Apple Event.

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
7. `tccutil reset AppleEvents`, then **deny** the prompt: both
   `append_log_entry` and `read_log` return the `-1743` actionable message
   (no silent success, no empty result) — re-approve afterward to continue.
8. Rename the `Logbuch` folder (or point `LOGBOOK_FOLDER` at a name that
   doesn't exist): both tools return `Folder '…' not found` listing the
   folders that *do* exist in the account.
9. `append_log_entry` returns `{ "created": true, "date": "YYYY-MM-DD" }`
   matching the new note's creation date in Notes.
10. `read_log` with `prefix="TECH:"` returns only matching entries (exact,
    case-sensitive); `read_log` with `include_detail=false` returns dated
    headings only, same `count` as with detail included.
