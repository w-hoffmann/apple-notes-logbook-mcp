# logbook Specification

## Purpose

The `logbook` capability automates the behavioral log kept in an Apple Notes folder (decisions, patterns, anti-patterns) that is reviewed during the Weekly Review. It is delivered by the local, macOS-only `apple-notes-logbook-mcp` server (Python + uv + the official MCP SDK low-level `Server`, stdio transport) and exposes exactly two append-only tools — `append_log_entry` (one note per entry) and `read_log` (chronological, date-filterable consolidation). Notes access goes through a single injection-safe `osascript` subprocess with a hard timeout. There are no update/delete/edit/list/search tools and no automatic folder creation: the `Logbook` folder must pre-exist in the iCloud account, and the deliberate human approval before a write is preserved as a behavioral guardrail.

## Requirements

### Requirement: Server exposes exactly two tools over stdio

The server SHALL run as a stdio MCP server using the official MCP SDK and SHALL advertise exactly two tools: `append_log_entry` and `read_log`. No other externally visible tools (no update, delete, edit, list, or search) SHALL be exposed.

#### Scenario: Tool discovery

- **WHEN** an MCP client sends a `tools/list` request
- **THEN** the server returns exactly two tools, named `append_log_entry` and `read_log`, each with a valid JSON Schema `inputSchema`, a human-readable description, and tool annotations

#### Scenario: Tool annotations reflect behavior

- **WHEN** the client inspects the tool annotations
- **THEN** `read_log` is annotated `readOnlyHint: true` and `idempotentHint: true`, and `append_log_entry` is annotated `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`, and `openWorldHint: true`

### Requirement: Tool results follow the MCP structured-output and error contract

Each tool SHALL declare an `outputSchema` and return its success result in `structuredContent` (also serialized as JSON in a text content block for compatibility). Recoverable failures (missing folder, missing Automation permission, invalid input, Notes unavailable) SHALL be returned as a result with `isError: true` and an actionable plain-language message in a text content block; they SHALL NOT use an `{ "ok": false }` envelope inside a non-error result. Structured-output schema validation applies to success results only; error results carry a human-readable message, not `structuredContent`. Protocol-level JSON-RPC errors are reserved for malformed requests (handled by the SDK transport layer). A `tools/call` for an unknown tool name, or with parameters failing input-schema validation, is surfaced as an `isError` result with an explanatory message (the low-level SDK converts a handler-raised error and an input-validation failure into an `isError` result; it does not emit a method-level JSON-RPC error for these). The server SHALL write only valid JSON-RPC messages to stdout; all logging and diagnostics SHALL go to stderr.

#### Scenario: read_log success returns structured content

- **WHEN** `read_log` succeeds
- **THEN** the result contains `structuredContent` conforming to its declared `outputSchema` (with `count` and `entries_text`) and `isError` is false

#### Scenario: append_log_entry success returns structured content

- **WHEN** `append_log_entry` succeeds
- **THEN** the result contains `structuredContent` conforming to its declared `outputSchema`, `isError` is false, no `{ "ok": … }` envelope is used, and no note id or title is echoed back

#### Scenario: Recoverable failure is surfaced via isError

- **WHEN** a tool fails for a recoverable reason (e.g. the folder is missing)
- **THEN** the result has `isError: true` with a human-readable message describing the cause and the remediation, and no exception escapes the handler

#### Scenario: Diagnostics never corrupt the protocol stream

- **WHEN** the server emits logs or diagnostics
- **THEN** they are written to stderr only, and stdout carries nothing but MCP JSON-RPC messages

### Requirement: The Logbook folder must already exist

Both tools SHALL locate the configured log folder in the iCloud account and SHALL NOT create it. The folder name SHALL default to `Logbook` and SHALL be overridable via the `LOGBOOK_FOLDER` environment variable (resolved at process start). If the folder is missing, both tools SHALL abort with `isError: true` and a message such as `Folder 'Logbook' not found` (naming the configured folder) and SHALL create nothing. That message SHALL also list the names of the folders that do exist in the iCloud account (or state that none were found) so a misconfigured or renamed folder can be diagnosed without a separate lookup; the listing SHALL be obtained within the same Notes operation that detected the missing folder. When multiple Notes accounts exist, the server SHALL select the iCloud account explicitly rather than relying on a default.

#### Scenario: Folder missing aborts append

- **WHEN** `append_log_entry` is called and no `Logbook` folder exists in the iCloud account
- **THEN** the call returns `isError: true` with a `Folder 'Logbook' not found` message and no note is created

#### Scenario: Folder missing aborts read

- **WHEN** `read_log` is called and no `Logbook` folder exists in the iCloud account
- **THEN** the call returns `isError: true` with a `Folder 'Logbook' not found` message and nothing is created

#### Scenario: Folder-missing message lists the existing folders

- **WHEN** a tool aborts because the configured folder is missing and the iCloud account contains other folders (e.g. `Claude Logbuch`, `Notizen`)
- **THEN** the error message names the missing folder and also lists the existing folder names, guiding the user to fix `LOGBOOK_FOLDER` or the folder name

#### Scenario: Account is resolved explicitly

- **WHEN** the Mac has more than one Notes account (e.g. iCloud plus On My Mac)
- **THEN** the server targets the `Logbook` folder of the iCloud account specifically and does not act on a same-named folder in another account

### Requirement: append_log_entry creates one note per entry

`append_log_entry` SHALL accept a required `summary` (string) and an optional `detail` (string) and SHALL create exactly one new note in the `Logbook` folder. The note's first line SHALL equal `summary` with no date, timestamp, or prefix. If `detail` is provided, it SHALL appear as the body below the heading separated by one blank line; if absent, the note SHALL consist of the heading only. `summary` and `detail` SHALL be HTML-escaped (`&` first, then `<` and `>`) before insertion, and multi-line `detail` SHALL be rendered as separate body lines. Any newlines within `summary` SHALL be collapsed so the heading stays a single line. The note SHALL be created with a body only; the read-only `name`/title is derived by Notes from the first body line and SHALL NOT be set explicitly. The note's `creationDate` SHALL be the moment of creation (set by Notes; no back-dating).

Creation SHALL be confirmed by reading the newly created note back within the same Notes operation. The returned `date` SHALL be the note's own `creationDate` read from Notes, formatted as an ISO `YYYY-MM-DD` string in the user's local time zone; the server SHALL NOT substitute its own clock. If the new note's `creationDate` cannot be read (Apple Events error `-1728`), the server SHALL fall back to that same note's `modificationDate` (which for a just-created note is effectively the same instant) — still a value read from Notes. If neither date can be read (an empty or malformed read-back), the server SHALL treat the operation as a failure (`isError: true`) even if the underlying process exited successfully, so a reported success always corresponds to a persisted, read-back note. On success the result SHALL be minimal — it SHALL contain `created: true` and `date` and SHALL NOT echo the note's id or title. Because creating a note over Apple Events is not idempotent, an operation that fails after the note may already have been created (e.g. a timeout) SHALL be surfaced as `isError: true` with a message indicating the entry may or may not have been created; the server SHALL NOT automatically retry.

#### Scenario: Summary only

- **WHEN** `append_log_entry` is called with `summary` and no `detail`
- **THEN** a new note is created whose first (and only) line equals `summary`

#### Scenario: Summary with detail

- **WHEN** `append_log_entry` is called with both `summary` and `detail`
- **THEN** a new note is created whose first line equals `summary`, followed by one blank line, followed by `detail`

#### Scenario: HTML special characters are escaped

- **WHEN** `summary` or `detail` contains `<`, `>`, or `&`
- **THEN** the note stores the escaped form and the characters render correctly in Notes (no markup breakage)

#### Scenario: Non-ASCII characters are preserved

- **WHEN** `summary` or `detail` contains non-ASCII characters (e.g. `ä`, `ö`, `ü`, `—`)
- **THEN** the note stores them intact

#### Scenario: Multi-line detail becomes multiple body lines

- **WHEN** `detail` contains newline characters
- **THEN** each line appears as its own line in the note body below the heading

#### Scenario: Existing notes are never modified

- **WHEN** `append_log_entry` runs
- **THEN** it only adds a new note and does not modify or delete any existing note in the folder

#### Scenario: Success returns the entry's creation date read from Notes

- **WHEN** `append_log_entry` succeeds
- **THEN** the result's `structuredContent` contains `created: true` and `date` equal to the new note's `creationDate` read from Notes as an ISO `YYYY-MM-DD` string in the user's local time zone (never the server's own clock), and no note id or title is echoed back

#### Scenario: Unreadable creation date falls back to the note's modification date

- **WHEN** the new note is created but its `creationDate` cannot be read (Apple Events error `-1728`) while its `modificationDate` can
- **THEN** the returned `date` is derived from that same note's `modificationDate` (still read from Notes) and the creation is reported as a success

#### Scenario: Unconfirmed creation is surfaced as an error

- **WHEN** the create operation cannot read back any date for the new note (both `creationDate` and `modificationDate` are unreadable) even though the underlying process reported success
- **THEN** the call returns `isError: true` rather than `created: true`, so no unpersisted entry is reported as saved

#### Scenario: Ambiguous failure after a note may already exist

- **WHEN** the create operation fails after the note may already have been created (e.g. an Apple Events timeout)
- **THEN** the call returns `isError: true` with a message indicating the entry may or may not have been created, and the server does not automatically retry

### Requirement: User input is validated and never injected into executable script

The server SHALL validate tool inputs against their JSON Schema, SHALL reject unknown parameters (the input schema sets `additionalProperties: false` or the handler explicitly rejects extra keys, since the SDK does not reject them by default), and SHALL pass all user-provided values (summary, detail, dates) to the Notes automation layer as out-of-band arguments (osascript `on run argv`), never interpolated into an executable AppleScript/osascript source string.

#### Scenario: AppleScript-significant characters are safe

- **WHEN** `summary` or `detail` contains characters significant to AppleScript (e.g. `"`, `\`, line breaks)
- **THEN** the note is created with that literal text and no script injection or syntax breakage occurs

#### Scenario: Unknown parameters are rejected

- **WHEN** either tool is called with a parameter not declared in its input schema
- **THEN** the call is rejected as an invalid request

### Requirement: read_log consolidates entries into one chronological dated text block

`read_log` SHALL read every note in the `Logbook` folder, treating one note as one entry, and SHALL produce a single consolidated text block. For each note it SHALL convert the HTML body to plain text (converting `</div>` and `<br>` block boundaries to line breaks, stripping remaining tags, decoding HTML entities — including `&amp;`, `&lt;`, `&gt;`, `&nbsp;` (to U+00A0), and numeric character references — collapsing runs of blank lines to a single blank line, and trimming surrounding whitespace). It SHALL determine each entry's date from the date part of `creationDate` in the user's local time zone, sort entries ascending by `creationDate`, and render each entry as `YYYY-MM-DD — {first line}` with any further body lines unchanged below, entries separated by one blank line. If a note's `creationDate` cannot be read (e.g. Apple Events error `-1728`), the server SHALL fall back to that note's `modificationDate` for the entry's date rather than failing the whole read. The result SHALL include `count` (number of entries returned) and `entries_text` (the joined string). Reading SHALL be purely observational and SHALL NOT modify any note.

#### Scenario: Consolidated output format

- **WHEN** the folder contains entries on 2026-03-14 and 2026-03-21
- **THEN** `entries_text` lists them ascending, each starting with `YYYY-MM-DD — ` followed by the entry's first line, detail lines below, and a blank line between entries, and `count` equals the number of entries returned

#### Scenario: Heading/detail separator renders as one blank line

- **WHEN** a note body is a heading line, then a single `<div><br></div>` separator, then a detail line
- **THEN** the rendered entry is the dated first line, one blank line, then the detail line (the separator yields exactly one blank line, not two)

#### Scenario: HTML body is converted to clean plain text

- **WHEN** a note body contains `<div>`, `<br>`, and entities such as `&amp;`, `&lt;`, `&gt;`, `&nbsp;`, and a numeric reference (e.g. `&#8212;`)
- **THEN** the corresponding `entries_text` lines have correct line breaks, no residual tags, and all entities decoded to their literal characters (`&nbsp;` to U+00A0)

#### Scenario: Date part uses the user's local time zone

- **WHEN** a note's `creationDate` is an instant that falls on a different calendar date in UTC than in the user's local time zone
- **THEN** both the rendered `YYYY-MM-DD` and the note's inclusion under a `from`/`to` filter use the user-local calendar date

#### Scenario: Unreadable creation date falls back to modification date

- **WHEN** a note's `creationDate` cannot be read (Apple Events error `-1728`)
- **THEN** that entry uses its `modificationDate` for the date and the overall `read_log` call still succeeds for all other entries

#### Scenario: Reading does not modify notes

- **WHEN** `read_log` runs over a non-empty folder
- **THEN** every note's content and `modificationDate` are unchanged afterward

#### Scenario: Empty folder

- **WHEN** the `Logbook` folder exists but contains no notes
- **THEN** `read_log` returns `count` 0 and an empty `entries_text` with `isError` false

### Requirement: read_log filters by creation-date range

`read_log` SHALL accept optional `from` and `to` parameters as ISO `YYYY-MM-DD` strings and SHALL apply them inclusively to the date part of each note's `creationDate` in the user's local time zone. `to` SHALL default to today when omitted; `from` SHALL impose no lower bound when omitted. Filtering SHALL be performed after reading (in the server), not via AppleScript `whose` clauses. Malformed date input SHALL be rejected with `isError: true` and an example of the correct format.

#### Scenario: Inclusive range

- **WHEN** `read_log` is called with `from` = 2026-03-14 and `to` = 2026-03-21
- **THEN** entries whose `creationDate` falls on or after 2026-03-14 and on or before 2026-03-21 are included, and others are excluded

#### Scenario: to defaults to today

- **WHEN** `read_log` is called with `from` set and `to` omitted
- **THEN** the upper bound is today (user-local date), inclusive

#### Scenario: Malformed date is rejected

- **WHEN** `from` or `to` is not a valid `YYYY-MM-DD` date
- **THEN** the call returns `isError: true` with a message showing the expected `YYYY-MM-DD` format, and no notes are read

### Requirement: Round-trip fidelity between append and read

An entry created by `append_log_entry` SHALL be readable by `read_log` such that its first line equals the original `summary` and its body equals the original `detail` — up to normalization of surrounding whitespace and collapsing of blank-line runs as defined in the consolidation requirement — including correctly decoded HTML-special and non-ASCII characters.

#### Scenario: Write then read preserves content

- **WHEN** `append_log_entry` stores an entry containing non-ASCII characters and `<`, `>`, and `&`, and `read_log` is then called for the date range covering that entry
- **THEN** the entry appears with its first line equal to the original `summary` and its body equal to the original `detail` (modulo whitespace/blank-line normalization), with special characters decoded to their literal form

### Requirement: Automation failures are surfaced, not silent or hanging

Every Notes operation SHALL run under a timeout (the AppleScript `with timeout` set below the AppleScript default and the subprocess killed after a grace period) so that a stuck Notes app or pending permission dialog produces a surfaced error rather than an indefinitely hanging tool call. When Automation permission is missing or denied (Apple Events error `-1743`), an operation times out (`-1712`), or Notes is unavailable (`-600` procNotFound or `-609` connectionInvalid), the tool SHALL return `isError: true` with an actionable message pointing to System Settings → Privacy & Security → Automation (for the permission case). Failure classification SHALL key primarily on the locale-independent Apple Events error number; when the error number cannot be parsed but the error text indicates an Automation denial (it contains, case-insensitively, "not authorized to send apple events"), the failure SHALL still be classified as a permission-denied error. A denied Automation grant SHALL never be reported as a success. (Unreadable `creationDate`, `-1728`, is handled by fallback per the append and consolidation requirements, not by aborting.)

#### Scenario: Permission denied yields actionable error

- **WHEN** a tool attempts to control Notes but Automation permission has not been granted (error `-1743`)
- **THEN** the call returns `isError: true` with a message instructing the user how to grant Automation permission, and the server does not crash

#### Scenario: Permission denial is detected despite an unparseable error number

- **WHEN** a Notes operation fails with stderr that indicates the caller is not authorized to send Apple events but from which no `(-NNNN)` error number can be extracted
- **THEN** the failure is still classified as a permission-denied error with the Automation-permission remediation message, not as a generic failure

#### Scenario: Notes unavailable yields actionable error

- **WHEN** a tool attempts to control Notes but Notes cannot be reached (error `-600` procNotFound or `-609` connectionInvalid)
- **THEN** the call returns `isError: true` with an actionable message and the server does not crash

#### Scenario: Operation timeout yields an error

- **WHEN** a Notes operation does not complete within the configured timeout (surfacing as `-1712` or a subprocess kill)
- **THEN** the call returns `isError: true` describing the timeout rather than blocking indefinitely

### Requirement: read_log projects entries by prefix and detail level

`read_log` SHALL accept two optional projection parameters in addition to `from`/`to`: `prefix` (string) and `include_detail` (boolean, default `true`). This requirement refines the consolidation requirement's rendering for the projection cases; the base ascending-by-`creationDate` ordering and HTML-to-plain-text conversion are unchanged. When a non-empty `prefix` is provided, `read_log` SHALL include only entries whose **first plain-text line** — the note body converted to plain text, before the `YYYY-MM-DD — ` date prefix is prepended — starts with `prefix` using an exact, case-sensitive prefix match, composed with the `from`/`to` date filter; an omitted or empty `prefix` SHALL impose no such filter. When `include_detail` is `false`, each included entry SHALL be rendered as its dated first line only (`YYYY-MM-DD — {first line}`), with no further body lines; when `true` (the default) rendering is as defined by the consolidation requirement. `count` SHALL reflect the number of entries actually returned after all filters (including `prefix`); `include_detail` SHALL NOT change `count`. Both parameters SHALL be optional and additive: omitting them SHALL reproduce the prior behavior, and the input schema SHALL continue to reject unknown parameters (`additionalProperties: false`). Filtering SHALL be performed in the server after the bulk read, not via AppleScript `whose` clauses. The server SHALL NOT hard-code any prefix taxonomy; `prefix` is an arbitrary caller-supplied string.

#### Scenario: Prefix filters by first line

- **WHEN** `read_log` is called with `prefix` = `TECH:` and the folder contains entries whose first lines start with `TECH:` and others that do not
- **THEN** only the entries whose first line starts with `TECH:` are returned, `count` equals that number, and entries not matching the prefix are excluded

#### Scenario: Prefix match is exact and case-sensitive

- **WHEN** `read_log` is called with `prefix` = `TECH:` and an entry's first line starts with `tech:` (different case) or contains `TECH:` only later in the line
- **THEN** that entry is excluded (the match anchors at the start of the first line and is case-sensitive)

#### Scenario: Headings-only mode omits body lines

- **WHEN** `read_log` is called with `include_detail` = `false`
- **THEN** each returned entry consists of only its `YYYY-MM-DD — {first line}` line with no detail lines, while `count` is the same as it would be with `include_detail` = `true`

#### Scenario: Prefix composes with the date range

- **WHEN** `read_log` is called with both a `from`/`to` range and a `prefix`
- **THEN** an entry is returned only if it falls within the date range and its first line starts with the prefix

#### Scenario: Projection parameters are optional

- **WHEN** `read_log` is called without `prefix` and without `include_detail`
- **THEN** the result is identical to the pre-existing behavior (all entries in range, full bodies, ascending by creation date)

#### Scenario: Empty prefix imposes no filter

- **WHEN** `read_log` is called with `prefix` set to the empty string
- **THEN** it behaves as if `prefix` were omitted (no prefix filtering is applied)
