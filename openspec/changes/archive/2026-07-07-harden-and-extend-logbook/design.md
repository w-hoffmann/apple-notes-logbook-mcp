## Context

The server (Python + uv, official MCP SDK low-level `Server`, `osascript` subprocess) is
functionally stable. This change hardens three residual gaps and adds two additive `read_log`
projections, without touching the deliberate invariants (append-only, no folder creation,
low-level Server, values via `on run argv`, stdout = JSON-RPC only, explicit iCloud account,
`-1728` per-note read fallback). Web research (2026-07-07) into macOS TCC / Apple Events
established the boundary conditions the decisions below rely on:

- A denied Automation grant is error **`-1743`** (`errAEEventNotPermitted`) and **always**
  raises → non-zero exit + stderr; it can never surface as a silent success.
- `osascript` exits 0 only on normal completion; a bare `try` that swallows an error, or
  `ignoring application responses`, are the *only* ways to get "exit 0, nothing happened".
  Neither is used in the write script — so a read-back confirmation is belt-and-suspenders.
- A pre-flight TCC probe (`AEDeterminePermissionToAutomateTarget`) cannot distinguish
  "denied" from "undecided" without prompting (`askUserIfNeeded=false` returns `-1744` for
  both), requires a PyObjC/C bridge, and has a documented hang bug → not worth building.
- Error numbers are locale-independent; classification must key on the number, with a text
  substring only as a drift fallback. Relevant: `-1743` denied, `-1728` no-such-object
  (folder/account missing), `-600` procNotFound, `-609` connectionInvalid, `-1712` timeout.
  `-10004` is a Standard-Suite privilege error and is **not** the TCC-denial signal.

## Goals / Non-Goals

**Goals:**
- Make a reported `append_log_entry` success provably correspond to a persisted note, and
  return that note's creation date.
- Make every Automation failure land on the most specific actionable message available,
  robust to stderr format/locale drift.
- Turn the folder-missing dead-end into a self-diagnosing message.
- Give `read_log` prefix and detail projection to bound the returned token size as the
  logbook grows.
- Remove the redundant double folder check (latency, TOCTOU) as a debt cleanup.

**Non-Goals:**
- No pre-flight/standalone permission probe (research: not reliable, not worth the bridge).
- No folder auto-creation; no update/delete/edit; no external list/search tool.
- No new dependency; no change to the transport, HTML handling, or date/timezone core.
- No server-side taxonomy for prefixes (the `TECH:` / `ZWISCHENSPEICHER:` convention stays
  in the project prompt).

## Decisions

### D-A — Create-then-confirm in one Apple Event; return the note's own creation date
`_CREATE_SCRIPT` binds the new note and reads its date back in the same `tell` block, guarding
the read exactly like `_READ_SCRIPT` (creation date → modification date fallback):

    set newNote to make new note … with properties {body:bodyHtml}
    set d to ""
    try
        set d to my isoDate(creation date of newNote)
    on error
        try
            set d to my isoDate(modification date of newNote)
        end try
    end try
    return d

`create_note(body) -> str` strips osascript's trailing newline, validates the
`YYYY-MM-DDThh:mm:ss` shape, and returns the **date part** (`YYYY-MM-DD`); `do_append` returns
`{ "created": true, "date": <that> }` and does no date parsing of its own (so no cross-layer
reach into a private helper, and no clock in `do_append`).
- **Why one event, not a follow-up read:** atomic, no TOCTOU, no second permission/latency
  surface. The returned value *is* the confirmation.
- **The date is always read from Notes — never the server clock.** `creation date` is a local,
  whole-second timestamp stamped at creation (independent of iCloud sync), i.e. the same value
  `read_log` will later emit for the note; a second server-clock reading could instead land on
  the other side of a midnight boundary. In the rare case `creation date` is unreadable
  (`-1728`), the fallback is the *same note's* `modification date` (≈ the same instant) — still
  from Notes, mirroring the read path. (Confirmed against the on-machine Notes.sdef /
  CocoaStandard.sdef and a production reference: `make` returns a live specifier whose
  properties are immediately readable; sync latency does not block the read.)
- **Confirmation semantics.** A non-empty, well-formed date proves the note exists. An
  empty/malformed read-back (both date reads failed — a broken note object, astronomically
  unlikely for a just-created note) is treated as failure (`isError`) even on exit 0, so an
  unpersisted entry is never reported as saved. Because the returned value is a from-Notes date
  and never the clock, `do_append` needs no injected clock.
- **Create-then-throw (the one irreducible ambiguity):** `make new note` can, in principle,
  create the note and then error before returning (e.g. an Apple-Event timeout `-1712` after
  the object was made), so the caller sees a failure though a note exists. This cannot be fully
  closed in-script. Posture: append is **at-least-once** — surface the error with a message
  noting the entry may or may not have been created, do **not** auto-retry (a blind retry is
  what makes a duplicate), and leave dedup out (over-engineering for an append-only logbook
  where a rare visible duplicate is benign and a lost entry is not).
  - **Where the wording comes from:** `_run`'s generic `OperationTimeoutError` (raised both on a
    subprocess kill and on a classified `-1712`) says nothing about a possibly-created entry —
    that phrasing is only correct for the *create* path, not for `read_notes`. `create_note`
    therefore catches `OperationTimeoutError` specifically and re-raises it as a new
    `AmbiguousCreateError(NotesError)`, whose message states the entry may or may not have been
    created and that the server will not auto-retry. `read_notes` is unaffected — a read timeout
    has no create-ambiguity to report.
- **Alternative rejected:** returning the note `id` — opaque, and the spec forbids echoing
  id/title; the date is the useful, self-documenting value the user asked for (B3) and doubles
  as the existence proof.

### D-B — Classification order in `_classify`, hardened for drift
Order: (1) folder-not-found marker → `FolderNotFoundError` (now with the folder list, D-D);
(2) numeric code → `-1743` denied, `-600`/`-609` unavailable, `-1712` timeout; (3) **new**
substring fallback — stderr contains "not authorized to send apple events"
(case-insensitive) ⇒ `PermissionDeniedError` even if `_extract_ae_number` found nothing;
(4) generic `NotesError` with the raw stderr.
- **Why number-first, substring-fallback:** numbers are locale-independent (this deployment
  runs a German locale); the substring only rescues an unexpected stderr shape.
- **`-609`** (connection invalid — Notes quit mid-op) joins `-600` under
  `NotesUnavailableError`; the user remedy ("make sure Notes can launch") is identical.

### D-C — Drop the redundant pre-`folder_exists()` checks; remove it from the protocol
`_CREATE_SCRIPT` and `_READ_SCRIPT` already guard `if not (exists folder …) then error
<marker>`, so `do_append`/`do_read` calling `provider.folder_exists()` first is a redundant
second Apple Event and a TOCTOU window. Remove both pre-checks; folder-missing surfaces via
the marker path (same `FolderNotFoundError`). `folder_exists` is then unused → remove it from
the `NotesProvider` protocol, `OsascriptNotesProvider`, and `FakeNotesProvider`.
- **Behavioral equivalence:** both paths already produce `FolderNotFoundError` naming the
  folder; the fake's `create_note`/`read_notes` already raise it when `folder_present` is
  false. Tests that call `folder_exists` directly are replaced by asserting the tool-level
  error.

### D-D — Self-diagnosing folder-missing message, same Apple Event
On the missing-folder branch, the scripts collect `name of every folder of (my
targetAccount())` and embed it in the raised error alongside the marker, FS-delimited:
`error _FOLDER_NOT_FOUND_MARKER & FS & folderList`. `_classify` parses the folder list out of
the marker payload and `FolderNotFoundError(folder, existing=[…])` renders: `Folder 'Logbuch'
not found in the iCloud account. Existing folders: 'Claude Logbuch', 'Notizen'. Check
LOGBOOK_FOLDER (or rename the folder) and try again.` (empty account → "no folders found").
- **Why in-script, error-path only:** zero extra round-trip on the happy path; the
  enumeration cost is paid only when already failing. If Automation is denied, the
  enumeration itself raises `-1743` first, so the permission error correctly wins.

### D-E — `read_log` projection in the pure core
`consolidate(...)` gains `prefix: str | None` and `include_detail: bool = True`. The prefix
matches the note's **first plain-text line** (the `html_to_text` output, *before* the
`YYYY-MM-DD — ` date prefix is prepended): keep an entry only if that line `startswith(prefix)`
(exact, case-sensitive) — composed with the existing `from`/`to` range filter. An omitted or
empty `prefix` means no filter. `count` is the number of entries that pass all filters. When
`include_detail` is false, `_render_entry` emits only the dated first line (no body lines).
- **Why in core, not AppleScript:** consistent with `from`/`to` (filter-after-read, D3); no
  `whose` clause; fully unit-testable. The bulk read still fetches all bodies (unchanged);
  projection bounds only what is *rendered/returned* — which is the token-cost lever.
- **Why case-sensitive exact prefix:** the convention is uppercase (`TECH:`); exact match is
  predictable and needs no taxonomy in the server.

### D-F — Additive schema changes
`append_log_entry.outputSchema`: add `date` (string), add to `required` (`created` stays
required). `read_log.inputSchema`: add `prefix` (string) and `include_detail` (boolean,
default true) to `properties`; `additionalProperties` stays `false`. All additive — existing
callers reading `created` / passing only `from`/`to` are unaffected.

## Risks / Trade-offs

- **Read-back `creation date` unreadable right after create (`-1728`)** → fall back to the
  *same note's* `modification date` (still a from-Notes value, ≈ the same instant); only if
  **both** are unreadable is the create reported as unconfirmed (`isError`). Extremely unlikely
  for a just-created note.
- **Create-then-throw ambiguity** (note made, then the command errors) → append is
  at-least-once: the error message flags that the entry may exist and the tool does not
  auto-retry. A rare visible duplicate is acceptable; a lost entry is not.
- **Folder enumeration on the error path adds one bit of work while already failing** →
  bounded by the same `with timeout`; only runs when the folder is missing; cheap (names
  only).
- **`prefix` is case-sensitive and literal** → intentional; documented in the tool
  description and spec so callers don't expect fuzzy/`contains` matching.
- **Removing `folder_exists` changes the provider protocol** → internal only (no wire
  contract); fake + tests updated in the same change; the two tools' folder-missing behavior
  is unchanged on the wire.
- **`date` becomes a required output field** → additive for clients (they still get
  `created`); only constrains the server. On success the read-back always yields a date
  (creation, or the modification-date fallback); the only no-date case fails as `isError`, so a
  success result always carries `date`.

## Migration Plan

Code-only; no data migration (append-only, nothing rewritten). Ship behind the normal CI gate
(`make check`). After merge, perform the manual macOS acceptance (see tasks): revoke
Automation via `tccutil reset AppleEvents` and confirm both tools return the `-1743` message;
rename the folder and confirm the listing message; confirm `append_log_entry` returns `date`
and the two `read_log` projections behave. Rollback: revert the change — no persisted state is
affected.

## Open Questions

- None blocking. Possible future follow-up (out of scope): a `limit`/tail option on
  `read_log` if headings-only output still grows large over many months.
