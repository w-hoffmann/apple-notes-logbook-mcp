## Why

The server is functionally stable (read + write verified on a real Mac, 2026-06-15 and
2026-07-03), but three gaps remain. (1) A successful `append_log_entry` is trusted on the
subprocess exit code alone — it never confirms the note actually persisted, so a theoretical
silent no-op would report `{ "created": true }` for an entry that does not exist (the most
dangerous failure for an append-only logbook). (2) When the target folder is missing (e.g.
after a rename without updating `LOGBOOK_FOLDER`), the error names the folder but gives the
user no way to see what *is* there, turning a one-line fix into guesswork. (3) As the logbook
grows, `read_log` always returns every entry's full body, inflating the token cost of the
Weekly Review even when only a prefix-filtered or headings-only view is needed.

Web research into macOS TCC / Apple Events (2026-07-07) confirms the write path is otherwise
sound: a denied Automation grant (`-1743`) can never surface as a silent success, and a
pre-flight permission probe is not worth building (the API cannot distinguish "denied" from
"undecided" and has a documented hang bug). So the hardening is small and targeted, not a
rewrite.

## What Changes

- **Write confirmation + creation date (B3).** `append_log_entry` reads the just-created note
  back in the *same* Apple Event and returns the note's **own** creation date (read from Notes,
  never the server clock): `{ "created": true, "date": "YYYY-MM-DD" }`. A missing read-back is
  treated as failure even on exit code 0, closing the last silent-write path; because create is
  not idempotent, an error after the note may already exist is surfaced as at-least-once (no
  auto-retry). Additive to the output schema; `created` stays required.
- **Sharper Automation-error classification (A1).** Add a locale/format-drift fallback (stderr
  containing "not authorized to send apple events" ⇒ permission-denied even if the numeric
  code cannot be parsed) and map `-609` (connection invalid) alongside `-600` (Notes
  unavailable). No pre-flight TCC probe is added — attempt-and-classify remains the pattern.
- **Self-diagnosing folder-missing error (A2).** When the folder is absent, the same Apple
  Event enumerates the iCloud account's existing folders and the error lists them: `Folder
  'Logbuch' not found in the iCloud account. Existing folders: 'Claude Logbuch', 'Notizen'.
  …`. No auto-creation (append-only folder discipline preserved).
- **`read_log` projection (B1 + B2).** Two additive optional parameters: `prefix` (return only
  entries whose rendered first line starts with the exact, case-sensitive string, e.g.
  `TECH:`) and `include_detail` (default `true`; `false` returns dated headings only). `count`
  reflects the prefix-filtered set. The server stays taxonomy-agnostic — prefix conventions
  live in the project prompt, not the code.
- **Internal cleanup (non-behavioral).** Remove the redundant pre-`folder_exists()` checks in
  `do_append`/`do_read`: the in-script folder checks are authoritative and atomic, so dropping
  them halves the Apple Events per call and removes a TOCTOU race. `folder_exists` leaves the
  provider protocol (dead code afterward).
- **Documentation.** Fold the verified TCC/Apple-Events facts into `Tool-Knowledge.md` and
  extend its first-run checklist with the new manual E2E acceptance steps.

No breaking changes: every output/input change is additive; `{ "created": true }` and the
existing `from`/`to` contract remain compatible.

## Capabilities

### New Capabilities

_None._ All changes modify the existing `logbook` capability.

### Modified Capabilities

- `logbook`: `append_log_entry` gains a returned `date` (and a create-then-confirm guarantee);
  `read_log` gains `prefix` and `include_detail` filters; the Automation-failure requirement
  gains the substring fallback and `-609`; the folder-missing requirement's message lists the
  account's existing folders.

## Impact

- **Spec:** `openspec/specs/logbook/spec.md` — delta on four requirements (see `specs/`).
- **Code:** `src/apple_notes_logbook_mcp/notes.py` (create/read AppleScript returns date +
  folder list; `_classify` fallback + `-609`; `create_note -> str`; drop `folder_exists` from
  the protocol), `server.py` (`do_append` returns `date`, drop pre-checks), `core.py` (prefix +
  detail-projection in `consolidate`), and the two tools' `outputSchema`/`inputSchema`.
- **Tests:** `tests/test_notes.py`, `tests/test_server.py`, `tests/test_core.py` — new cases
  for read-back date, `_classify` fallback + `-609`, folder-list message, prefix filter,
  headings-only mode; regressions kept green.
- **Docs:** `Tool-Knowledge.md` and `README.md` (new `read_log` params, append's `date`, the
  folder-listing message, the `-609` mapping).
- **Contract:** MCP tool output/input schemas change additively; no client breakage.
- **Manual verification (macOS only):** revoke Automation, rename folder — the unit suite
  cannot cover these.
