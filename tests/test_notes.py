"""Tests for the Notes I/O boundary: folder-name config, error mapping, parsing."""

from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from apple_notes_logbook_mcp import notes as notes_mod
from apple_notes_logbook_mcp.notes import (
    _CREATE_SCRIPT,
    _FS,
    _HELPERS,
    _READ_AS_TIMEOUT,
    _READ_SCRIPT,
    _RS,
    _WRITE_AS_TIMEOUT,
    FOLDER_NAME,
    FolderNotFoundError,
    NotesError,
    NotesUnavailableError,
    OperationTimeoutError,
    OsascriptNotesProvider,
    PermissionDeniedError,
    _classify,
    _parse_read_output,
    _resolve_folder_name,
)

# --- folder-name configurability ------------------------------------------


def test_provider_honors_explicit_folder_name():
    assert OsascriptNotesProvider(folder_name="Logbuch").folder_name == "Logbuch"
    assert OsascriptNotesProvider().folder_name == FOLDER_NAME


def test_resolve_folder_name_env_override(monkeypatch):
    monkeypatch.setenv("LOGBOOK_FOLDER", "Logbuch")
    assert _resolve_folder_name() == "Logbuch"


def test_resolve_folder_name_defaults_to_logbook(monkeypatch):
    monkeypatch.delenv("LOGBOOK_FOLDER", raising=False)
    assert _resolve_folder_name() == "Logbook"


# --- Apple Events error classification -------------------------------------


def test_classify_permission_denied():
    err = _classify("execution error: Not authorised to send Apple events (-1743).")
    assert isinstance(err, PermissionDeniedError)


def test_classify_notes_unavailable():
    err = _classify("execution error: Application isn't running (-600).")
    assert isinstance(err, NotesUnavailableError)


def test_classify_timeout():
    err = _classify("execution error: AppleEvent timed out (-1712).")
    assert isinstance(err, OperationTimeoutError)


def test_classify_folder_not_found_marker():
    err = _classify("execution error: LOGBOOK_FOLDER_NOT_FOUND (-2700).")
    assert isinstance(err, FolderNotFoundError)


def test_classify_unknown_falls_back_to_base_error():
    err = _classify("execution error: something weird happened.")
    assert isinstance(err, NotesError)
    assert not isinstance(
        err,
        FolderNotFoundError | PermissionDeniedError | NotesUnavailableError | OperationTimeoutError,
    )


# --- bulk-read payload parsing ---------------------------------------------


def _record(note_id: str, cdate: str, mdate: str, body: str) -> str:
    return f"{note_id}{_FS}{cdate}{_FS}{mdate}{_FS}{body}{_RS}"


def test_parse_read_output_basic():
    raw = _record("x://1", "2026-03-14T09:30:00", "2026-03-13T08:00:00", "<div>Body</div>")
    parsed = _parse_read_output(raw)
    assert len(parsed) == 1
    note = parsed[0]
    assert note.id == "x://1"
    assert note.body_html == "<div>Body</div>"
    assert note.creation_date == datetime(2026, 3, 14, 9, 30, 0)
    assert note.modification_date == datetime(2026, 3, 13, 8, 0, 0)


def test_parse_read_output_missing_creation_date_is_none():
    raw = _record("x://1", "", "2026-03-13T08:00:00", "<div>Body</div>")
    note = _parse_read_output(raw)[0]
    assert note.creation_date is None
    assert note.modification_date == datetime(2026, 3, 13, 8, 0, 0)


def test_parse_read_output_skips_empty_records_and_handles_multiple():
    raw = _record("a", "2026-03-14T09:00:00", "2026-03-14T09:00:00", "<div>A</div>") + _record(
        "b", "2026-03-15T10:00:00", "2026-03-15T10:00:00", "<div>B</div>"
    )
    parsed = _parse_read_output(raw)
    assert [n.id for n in parsed] == ["a", "b"]


def test_parse_read_output_empty_payload():
    assert _parse_read_output("") == []


# --- injection safety, timeout backstop, and AppleScript structure ---------


class _FakeProc:
    def __init__(self, returncode=0, stdout="OK", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_create_note_passes_values_as_argv_not_interpolated(monkeypatch):
    # The injection-safety guarantee: user values go in as separate argv items,
    # never interpolated into the executable AppleScript source.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    body = '<div>he said "hi" \\ no\nbreak</div>'  # quote, backslash, newline
    OsascriptNotesProvider(folder_name="Logbuch").create_note(body)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/osascript"
    assert cmd[1] == "-e"
    script = cmd[2]
    assert cmd[-2] == "Logbuch"  # folder name as out-of-band argv
    assert cmd[-1] == body  # body passed verbatim as out-of-band argv
    assert body not in script  # user data NOT interpolated into the source
    assert '"hi"' not in script and "\\ no" not in script


def test_subprocess_timeout_raises_operation_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(OperationTimeoutError):
        OsascriptNotesProvider().read_notes()


def test_nonzero_returncode_is_classified(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stdout="", stderr="execution error: ... (-1743).")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(PermissionDeniedError):
        OsascriptNotesProvider().folder_exists()


def test_applescript_selects_icloud_account_and_bounds_timeout():
    # Scenario "Account is resolved explicitly" + the hard-timeout requirement,
    # verified structurally on the script constants.
    assert 'account "iCloud"' in _HELPERS
    assert "with timeout of" in _READ_SCRIPT
    assert "with timeout of" in _CREATE_SCRIPT
    assert _READ_AS_TIMEOUT < 120  # below AppleScript's default
    assert _WRITE_AS_TIMEOUT < 120
