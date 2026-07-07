## 1. Write path: create-then-confirm, date read from Notes (D-A)

- [x] 1.1 In `notes.py`, change `_CREATE_SCRIPT` to bind the new note and read its date back in the same `tell` block, guarded like `_READ_SCRIPT`: `try` creation date → `on error try` modification date, `return my isoDate(...)` (an ISO `YYYY-MM-DDThh:mm:ss`), or `""` if both reads fail (replaces the static `"OK"`).
- [x] 1.2 Change `create_note` to `-> str`: strip osascript's trailing newline, validate the `YYYY-MM-DDThh:mm:ss` shape, and return the **date part** `YYYY-MM-DD`; an empty/malformed read-back raises `NotesError` (unconfirmed create — the note may exist, so do not auto-retry). Update the `NotesProvider` protocol signature accordingly.
- [x] 1.3 Update `FakeNotesProvider.create_note` to return the stamped note's local `YYYY-MM-DD` (from its `clock`), matching the new date-only contract.
- [x] 1.4 In `server.py` `do_append`, return `{"created": True, "date": provider.create_note(body)}` — the provider hands back `YYYY-MM-DD`, so no clock and no date parsing in the server. Do **not** thread a clock into `do_append`.
- [x] 1.5 Extend `APPEND_TOOL.outputSchema`: add `date` (string) to `properties` and to `required`; keep `created` required and `additionalProperties: false`. Update the tool description to mention the returned creation date.
- [x] 1.6 Add `AmbiguousCreateError(NotesError)` in `notes.py` with a message stating the entry may or may not have been created and that the server does not auto-retry. In `create_note`, catch `OperationTimeoutError` (raised by `_run` on either a subprocess kill or a classified `-1712`) and re-raise it as `AmbiguousCreateError`, so the timeout-after-create path gets this wording instead of the generic timeout message. `read_notes` is untouched — a read timeout has no create-ambiguity to report.

## 2. Automation-error classification hardening (D-B)

- [x] 2.1 Add `AE_NOTES_CONNECTION_INVALID = -609` and map it to `NotesUnavailableError` in `_classify` alongside `-600`.
- [x] 2.2 Add the drift fallback in `_classify`: if no AE number was extracted but stderr contains (case-insensitive) `not authorized to send apple events`, return `PermissionDeniedError`.
- [x] 2.3 Confirm ordering in `_classify`: folder-not-found marker → numeric code → substring fallback → generic `NotesError`.

## 3. Cleanup: drop redundant folder pre-checks (D-C)

- [x] 3.1 Remove `if not provider.folder_exists(): raise _folder_missing()` from `do_append` and `do_read`; rely on the in-script marker for folder-missing.
- [x] 3.2 Remove `folder_exists` from the `NotesProvider` protocol, `OsascriptNotesProvider`, and `FakeNotesProvider`; delete the now-unused `_FOLDER_EXISTS_SCRIPT` and the `_folder_missing()` helper if no longer referenced.

## 4. Self-diagnosing folder-missing message (D-D)

- [x] 4.1 In `_CREATE_SCRIPT` and `_READ_SCRIPT`, on the missing-folder branch, build a folder-name list from `name of every folder of (my targetAccount())` and raise `error _FOLDER_NOT_FOUND_MARKER & FS & <FS-joined names>`.
- [x] 4.2 Update `FolderNotFoundError` to accept an optional `existing: list[str]` and render `Folder '<name>' not found in the iCloud account. Existing folders: '…', '…'. Check LOGBOOK_FOLDER (or rename the folder) and try again.` (state "no folders found" when empty).
- [x] 4.3 Update `_classify` to parse the folder list out of the marker payload: strip osascript's wrapper (the `execution error:` prefix and the trailing ` (-NNNN).`) before splitting on FS, so the last folder name is clean; pass the names to `FolderNotFoundError`. (FS control chars survive to stderr — verified on this machine — so the FS delimiter is safe; the trailing `(-NNNN)` on the last name is the real gotcha.)

## 5. read_log projection in the pure core (D-E)

- [x] 5.1 Add `prefix: str | None = None` and `include_detail: bool = True` to `core.consolidate`; keep an entry only if its rendered first line `startswith(prefix)` (exact, case-sensitive), composed with the `from`/`to` filter; `count` reflects the filtered set.
- [x] 5.2 Update `_render_entry` (or `consolidate`) so `include_detail=False` emits only the dated first line.
- [x] 5.3 In `server.py` `do_read`, read `prefix`/`include_detail` from arguments (defaults) and pass them to `consolidate`.
- [x] 5.4 Extend `READ_TOOL.inputSchema`: add `prefix` (string) and `include_detail` (boolean, default true) to `properties`; keep `additionalProperties: false`. Document both in the tool description (prefix is exact/case-sensitive; server is taxonomy-agnostic).

## 6. Tests

- [x] 6.1 `test_notes.py`: `create_note` strips the trailing newline and returns `YYYY-MM-DD`; empty/malformed read-back raises `NotesError`; a `-1728` on creation date falls back to modification date; `_classify` `-609` → `NotesUnavailableError`; `_classify` substring fallback → `PermissionDeniedError`; folder-missing marker with FS-joined names → `FolderNotFoundError` message lists them with **no** trailing `(-NNNN)`. Re-point `test_nonzero_returncode_is_classified` (it currently calls the removed `folder_exists`, test_notes.py:166) at `create_note`/`read_notes` so the rc≠0 → `_classify` path stays covered. Add: `create_note` re-raises a subprocess-kill timeout, and a classified `-1712`, as `AmbiguousCreateError` (not the generic `OperationTimeoutError`); `read_notes` still raises the generic `OperationTimeoutError` on the same conditions (no create-ambiguity wording).
- [x] 6.2 `test_server.py`: append success `structuredContent` includes `date`; a provider whose `create_note` signals an unconfirmed create ⇒ `isError` (spec scenario "Unconfirmed creation"); folder-missing tool error lists existing folders; `{"created": true}` still present. Add a test whose note creation date differs from a plain clock reading so a silent always-fallback regression would be caught. Add a test where `create_note` raises `AmbiguousCreateError` (spec scenario "Ambiguous failure after a note may already exist") ⇒ `isError` with a message indicating the entry may or may not have been created.
- [x] 6.3 `test_core.py`: `prefix` filters by the first plain-text line (exact/case-sensitive, before the date prefix), composes with `from`/`to`, `count` reflects prefix; `include_detail=False` yields headings only with unchanged `count`; empty/omitted `prefix` = no filter; prefix + `include_detail=False` compose; omitting both reproduces prior output.
- [x] 6.4 Regression: umlauts/special chars stay plain text; ascending sort; inclusive `from`/`to`; round-trip append→read still passes.

## 7. Documentation (D-A/D-B, research facts)

- [x] 7.1 `Tool-Knowledge.md` §1: add the AE error-number table (`-1743`/`-1728`/`-600`/`-609`/`-1712`), the "-10004 is NOT the TCC-denial" note, "a denied grant is never a silent success", and "no pre-flight probe (`-1744` denied/undecided collision + hang bug)".
- [x] 7.2 `Tool-Knowledge.md` §2/§7: document the create-then-confirm read-back (append returns the note's **own** `creationDate`, read from Notes — never the server clock; modification-date fallback on `-1728`), the **at-least-once** posture (create-then-throw ⇒ the entry may exist; no auto-retry), and the folder-missing listing message; note the exact `-1743` stderr form.
- [x] 7.3 `Tool-Knowledge.md` §8: drop "folder" from the write/folder timeout description now that folder-missing is detected inline in the create/read scripts (no separate `folder_exists` op).
- [x] 7.4 `Tool-Knowledge.md` first-run checklist: add the new manual E2E acceptance steps (revoke Automation; rename folder; `date` returned; `prefix`/`include_detail`).
- [x] 7.5 Update `README.md`: document `read_log`'s `prefix` (exact, case-sensitive) and `include_detail` params, `append_log_entry`'s returned `date`, the folder-missing listing message, and the `-609` mapping in the error table.

## 8. Gate & manual macOS acceptance

- [x] 8.1 Run `make check` (ruff lint + format, pyright, pytest) — full gate green.
- [x] 8.2 Manual (real Mac): `tccutil reset AppleEvents`, deny the prompt → both tools return the `-1743` actionable message (no silent success, no empty result). **Waived 2026-07-07**: requires interactive TCC prompt handling on the live machine; not automatable. Logic is covered by `_classify` unit tests (`test_notes.py`).
- [x] 8.3 Manual (real Mac): rename the `Logbuch` folder → both tools return `Folder '…' not found` listing the existing folders. **Waived 2026-07-07**: requires renaming the live production folder; not automatable. Logic is covered by folder-missing unit tests (`test_notes.py`, `test_server.py`).
- [x] 8.4 Manual (real Mac): `append_log_entry` returns `{"created": true, "date": "…"}` matching the note's creation date; `read_log` with `prefix="TECH:"` and with `include_detail=false` behave per spec; umlauts round-trip; sort ascending; `from`/`to` inclusive. **Waived 2026-07-07**: would write a permanent note into the real Logbuch folder; not automatable. Behavior is covered end-to-end via `FakeNotesProvider` round-trip tests.
