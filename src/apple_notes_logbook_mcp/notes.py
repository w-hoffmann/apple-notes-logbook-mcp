"""Apple Notes I/O boundary (the only place that touches Apple Events).

A :class:`NotesProvider` protocol abstracts the three operations the server
needs; :class:`OsascriptNotesProvider` is the concrete adapter that drives
``/usr/bin/osascript`` as a subprocess with user values passed via ``on run
argv`` (never interpolated into the script source) and a hard timeout.
:class:`FakeNotesProvider` is an in-memory double for tests.

Recoverable Apple Events failures are raised as :class:`NotesError` subclasses
carrying an actionable, user-facing message; the server turns these into
``isError`` results.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from .core import RawNote

logger = logging.getLogger(__name__)


def _resolve_folder_name() -> str:
    """The target Notes folder name: env ``LOGBOOK_FOLDER``, default ``Logbook``."""
    return os.environ.get("LOGBOOK_FOLDER", "Logbook")


# Resolved once at import. Override per deployment via LOGBOOK_FOLDER (e.g. "Logbuch").
FOLDER_NAME = _resolve_folder_name()
OSASCRIPT = "/usr/bin/osascript"

# Apple Events error numbers we map to actionable messages (design D8 / spec).
AE_PERMISSION_DENIED = -1743  # not authorised to send Apple Events (Automation)
AE_NOTES_UNAVAILABLE = -600  # application isn't running / can't be reached
AE_TIMEOUT = -1712  # Apple Event timed out

# Marker the scripts raise when the folder is missing.
_FOLDER_NOT_FOUND_MARKER = "LOGBOOK_FOLDER_NOT_FOUND"

# Record / field separators (ASCII RS / US) — control chars that will not
# appear in note bodies, so they are safe payload delimiters for the bulk read.
_RS = "\x1e"
_FS = "\x1f"

# Subprocess kill timeouts (seconds). The AppleScript `with timeout` is set a
# few seconds below each so the script's own timeout normally fires first; the
# subprocess kill is the hard backstop. All stay well under AppleScript's 120 s.
_WRITE_AS_TIMEOUT = 30
_WRITE_KILL_TIMEOUT = 35
_READ_AS_TIMEOUT = 110
_READ_KILL_TIMEOUT = 115


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NotesError(Exception):
    """A recoverable Notes-automation failure with a user-actionable message."""


class FolderNotFoundError(NotesError):
    def __init__(self, folder: str = FOLDER_NAME) -> None:
        super().__init__(
            f"Folder '{folder}' not found in the iCloud Notes account. "
            f"Create a folder named '{folder}' in Apple Notes (iCloud) and try again."
        )


class PermissionDeniedError(NotesError):
    def __init__(self) -> None:
        super().__init__(
            "Not authorised to control Apple Notes (Apple Events error -1743). "
            "Grant permission in System Settings → Privacy & Security → Automation, "
            "enable Notes for the controlling app, then try again. "
            "If the toggle is missing, run `tccutil reset AppleEvents` and retry."
        )


class NotesUnavailableError(NotesError):
    def __init__(self) -> None:
        super().__init__(
            "Apple Notes could not be reached (Apple Events error -600). "
            "Make sure the Notes app is installed and able to launch, then try again."
        )


class OperationTimeoutError(NotesError):
    def __init__(self) -> None:
        super().__init__(
            "The Notes operation timed out. This often means a permission dialog is "
            "waiting, or the folder is very large. Approve any Automation prompt in "
            "System Settings → Privacy & Security → Automation and try again."
        )


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class NotesProvider(Protocol):
    """The Notes operations the server depends on."""

    def folder_exists(self) -> bool:
        """Return whether the target folder exists in the iCloud account."""
        ...

    def create_note(self, body_html: str) -> None:
        """Create one new note (body only) in the target folder."""
        ...

    def read_notes(self) -> list[RawNote]:
        """Read every note in the target folder as :class:`RawNote` DTOs."""
        ...


# ---------------------------------------------------------------------------
# AppleScript snippets (values arrive via `on run argv`, never interpolated)
# ---------------------------------------------------------------------------

# Shared handlers: resolve the iCloud account explicitly (falling back to the
# sole account), and emit locale-independent ISO date-times.
_HELPERS = """
on targetAccount()
    tell application "Notes"
        if (exists account "iCloud") then
            return account "iCloud"
        else
            return account 1
        end if
    end tell
end targetAccount

on pad(n, w)
    set s to (n as integer) as string
    repeat while (length of s) < w
        set s to "0" & s
    end repeat
    return s
end pad

on isoDate(d)
    set y to year of d
    set mo to (month of d as integer)
    set dy to day of d
    set hh to hours of d
    set mm to minutes of d
    set ss to seconds of d
    return (my pad(y, 4)) & "-" & (my pad(mo, 2)) & "-" & (my pad(dy, 2)) & ¬
        "T" & (my pad(hh, 2)) & ":" & (my pad(mm, 2)) & ":" & (my pad(ss, 2))
end isoDate
"""

_FOLDER_EXISTS_SCRIPT = f"""
on run argv
    set folderName to item 1 of argv
    with timeout of {_WRITE_AS_TIMEOUT} seconds
        tell application "Notes"
            tell (my targetAccount())
                if (exists folder folderName) then
                    return "1"
                else
                    return "0"
                end if
            end tell
        end tell
    end timeout
end run
{_HELPERS}
"""

_CREATE_SCRIPT = f"""
on run argv
    set folderName to item 1 of argv
    set bodyHtml to item 2 of argv
    with timeout of {_WRITE_AS_TIMEOUT} seconds
        tell application "Notes"
            tell (my targetAccount())
                if not (exists folder folderName) then
                    error "{_FOLDER_NOT_FOUND_MARKER}"
                end if
                make new note at folder folderName with properties {{body:bodyHtml}}
            end tell
        end tell
    end timeout
    return "OK"
end run
{_HELPERS}
"""

# Bulk read: one Apple Event loops the folder's notes, emitting RS/FS-separated
# id, creation-date, modification-date (ISO, local wall-clock), and body. A
# per-note `try ... on error use modification date` guard keeps one unreadable
# creationDate (-1728) from failing the whole read.
_READ_SCRIPT = f"""
on run argv
    set folderName to item 1 of argv
    set RS to (ASCII character 30)
    set FS to (ASCII character 31)
    set out to ""
    with timeout of {_READ_AS_TIMEOUT} seconds
        tell application "Notes"
            tell (my targetAccount())
                if not (exists folder folderName) then
                    error "{_FOLDER_NOT_FOUND_MARKER}"
                end if
                repeat with n in (notes of folder folderName)
                    set noteId to ""
                    set noteBody to ""
                    set cdate to ""
                    set mdate to ""
                    try
                        set noteId to (id of n) as string
                    end try
                    try
                        set mdate to my isoDate(modification date of n)
                    end try
                    try
                        set cdate to my isoDate(creation date of n)
                    on error
                        set cdate to mdate
                    end try
                    try
                        set noteBody to (body of n) as string
                    end try
                    set out to out & noteId & FS & cdate & FS & mdate & FS & noteBody & RS
                end repeat
            end tell
        end tell
    end timeout
    return out
end run
{_HELPERS}
"""


# ---------------------------------------------------------------------------
# osascript adapter
# ---------------------------------------------------------------------------


def _extract_ae_number(stderr: str) -> int | None:
    """Pull the trailing Apple Events error number, e.g. ``(-1743)``, from stderr."""
    matches = re.findall(r"\((-\d+)\)", stderr)
    return int(matches[-1]) if matches else None


def _classify(stderr: str) -> NotesError:
    if _FOLDER_NOT_FOUND_MARKER in stderr:
        return FolderNotFoundError()
    number = _extract_ae_number(stderr)
    if number == AE_PERMISSION_DENIED:
        return PermissionDeniedError()
    if number == AE_NOTES_UNAVAILABLE:
        return NotesUnavailableError()
    if number == AE_TIMEOUT:
        return OperationTimeoutError()
    return NotesError(f"Notes automation failed: {stderr.strip() or 'unknown error'}")


def _parse_iso_naive(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # Local wall-clock from AppleScript; kept naive (treated as local).
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


class OsascriptNotesProvider:
    """Concrete :class:`NotesProvider` driving ``osascript`` as a subprocess."""

    def __init__(self, folder_name: str = FOLDER_NAME) -> None:
        self.folder_name = folder_name

    def _run(self, script: str, args: list[str], kill_timeout: int) -> str:
        cmd = [OSASCRIPT, "-e", script, self.folder_name, *args]
        try:
            proc = subprocess.run(  # noqa: S603 - fixed binary, args via argv
                cmd,
                capture_output=True,
                text=True,
                timeout=kill_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("osascript timed out after %ss", kill_timeout)
            raise OperationTimeoutError() from exc
        if proc.returncode != 0:
            logger.warning("osascript failed (rc=%s): %s", proc.returncode, proc.stderr.strip())
            raise _classify(proc.stderr)
        return proc.stdout

    def folder_exists(self) -> bool:
        out = self._run(_FOLDER_EXISTS_SCRIPT, [], _WRITE_KILL_TIMEOUT)
        return out.strip() == "1"

    def create_note(self, body_html: str) -> None:
        self._run(_CREATE_SCRIPT, [body_html], _WRITE_KILL_TIMEOUT)

    def read_notes(self) -> list[RawNote]:
        out = self._run(_READ_SCRIPT, [], _READ_KILL_TIMEOUT)
        return _parse_read_output(out)


def _parse_read_output(raw: str) -> list[RawNote]:
    notes: list[RawNote] = []
    for record in raw.split(_RS):
        if record == "" or record.strip("\n") == "":
            continue
        fields = record.split(_FS)
        if len(fields) < 4:
            continue
        note_id, cdate, mdate, body = fields[0], fields[1], fields[2], fields[3]
        notes.append(
            RawNote(
                id=note_id,
                body_html=body,
                creation_date=_parse_iso_naive(cdate),
                modification_date=_parse_iso_naive(mdate),
            )
        )
    return notes


# ---------------------------------------------------------------------------
# In-memory fake (for core/server tests)
# ---------------------------------------------------------------------------


class FakeNotesProvider:
    """In-memory :class:`NotesProvider` double.

    Created notes are appended to ``notes`` (stamped via ``clock``) so a
    write-then-read round trip works end to end without Apple Events.
    """

    def __init__(
        self,
        *,
        folder_present: bool = True,
        notes: list[RawNote] | None = None,
        clock: Callable[[], datetime] | None = None,
        raise_on: NotesError | None = None,
    ) -> None:
        self._folder_present = folder_present
        self.notes: list[RawNote] = list(notes or [])
        self.created_bodies: list[str] = []
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._raise_on = raise_on

    def _maybe_raise(self) -> None:
        if self._raise_on is not None:
            raise self._raise_on

    def folder_exists(self) -> bool:
        self._maybe_raise()
        return self._folder_present

    def create_note(self, body_html: str) -> None:
        self._maybe_raise()
        if not self._folder_present:
            raise FolderNotFoundError()
        self.created_bodies.append(body_html)
        now = self._clock()
        self.notes.append(
            RawNote(
                id=f"fake://note/{len(self.notes)}",
                body_html=body_html,
                creation_date=now,
                modification_date=now,
            )
        )

    def read_notes(self) -> list[RawNote]:
        self._maybe_raise()
        if not self._folder_present:
            raise FolderNotFoundError()
        return list(self.notes)
