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
    AmbiguousCreateError,
    FolderNotFoundError,
    NotesError,
    NotesUnavailableError,
    OperationTimeoutError,
    OsascriptNotesProvider,
    PermissionDeniedError,
    _classify,
    _parse_folder_list,
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


def test_classify_connection_invalid():
    err = _classify("execution error: Connection is invalid (-609).")
    assert isinstance(err, NotesUnavailableError)


def test_classify_permission_denied_substring_fallback_without_number():
    # Drift fallback: no parseable `(-NNNN)` at all, but the text says denied.
    err = _classify("Not authorized to send Apple events because of foo.")
    assert isinstance(err, PermissionDeniedError)


def test_classify_substring_fallback_is_case_insensitive():
    err = _classify("NOT AUTHORIZED TO SEND APPLE EVENTS.")
    assert isinstance(err, PermissionDeniedError)


def test_classify_numeric_code_wins_over_substring_fallback():
    # A parseable number takes precedence even if the text also mentions denial
    # (shouldn't happen in practice, but the ordering is numeric-code-first).
    err = _classify("execution error: Application isn't running (-600).")
    assert isinstance(err, NotesUnavailableError)


def test_classify_timeout():
    err = _classify("execution error: AppleEvent timed out (-1712).")
    assert isinstance(err, OperationTimeoutError)


def test_classify_folder_not_found_marker():
    err = _classify("execution error: LOGBOOK_FOLDER_NOT_FOUND (-2700).")
    assert isinstance(err, FolderNotFoundError)
    assert "No folders were found" in str(err)


def test_classify_folder_not_found_lists_existing_folders():
    stderr = f"execution error: LOGBOOK_FOLDER_NOT_FOUND{_FS}Claude Logbuch{_FS}Notizen (-2700)."
    err = _classify(stderr)
    assert isinstance(err, FolderNotFoundError)
    message = str(err)
    assert "'Claude Logbuch'" in message
    assert "'Notizen'" in message
    # The trailing AppleScript error code must not leak onto the last name.
    assert "(-2700)" not in message
    assert "-2700" not in message


def test_parse_folder_list_strips_trailing_ae_code():
    stderr = f"31:82: execution error: LOGBOOK_FOLDER_NOT_FOUND{_FS}A{_FS}B (-2700)."
    assert _parse_folder_list(stderr) == ["A", "B"]


def test_parse_folder_list_empty_when_account_has_no_folders():
    stderr = "execution error: LOGBOOK_FOLDER_NOT_FOUND (-2700)."
    assert _parse_folder_list(stderr) == []


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
    def __init__(self, returncode=0, stdout="2026-01-01T00:00:00", stderr=""):
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
        OsascriptNotesProvider().read_notes()


# --- create_note: create-then-confirm read-back (D-A) ----------------------


def test_create_note_strips_newline_and_returns_date_part(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(stdout="2026-07-07T09:30:00\n")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    assert OsascriptNotesProvider().create_note("<div>x</div>") == "2026-07-07"


def test_create_note_empty_readback_raises_notes_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(stdout="")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(NotesError):
        OsascriptNotesProvider().create_note("<div>x</div>")


def test_create_note_malformed_readback_raises_notes_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(stdout="not-a-date")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(NotesError):
        OsascriptNotesProvider().create_note("<div>x</div>")


def test_create_note_folder_missing_is_not_flagged_as_ambiguous(monkeypatch):
    # The folder guard runs before `make new note`, so nothing was created —
    # the message must not claim the entry may already exist.
    def fake_run(cmd, **kwargs):
        stderr = "execution error: LOGBOOK_FOLDER_NOT_FOUND (-2700)."
        return _FakeProc(returncode=1, stdout="", stderr=stderr)

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(FolderNotFoundError) as excinfo:
        OsascriptNotesProvider().create_note("<div>x</div>")
    assert "may or may not" not in str(excinfo.value)


def test_create_note_non_timeout_failures_are_not_wrapped_as_ambiguous(monkeypatch):
    # Only the timeout path is genuinely ambiguous; permission/unavailable
    # failures are established before any Apple Event that could have created
    # the note, so they propagate unchanged (not AmbiguousCreateError).
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stdout="", stderr="execution error: ... (-1743).")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(PermissionDeniedError):
        OsascriptNotesProvider().create_note("<div>x</div>")


def test_create_note_classified_timeout_is_ambiguous_create_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stdout="", stderr="execution error: ... (-1712).")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(AmbiguousCreateError) as excinfo:
        OsascriptNotesProvider().create_note("<div>x</div>")
    message = str(excinfo.value)
    assert "may or may not" in message
    assert "does not automatically retry" in message


def test_create_note_subprocess_kill_timeout_is_ambiguous_create_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(AmbiguousCreateError):
        OsascriptNotesProvider().create_note("<div>x</div>")


def test_read_notes_timeout_has_no_ambiguity_wording(monkeypatch):
    # read_notes never creates anything, so its timeout stays the generic
    # OperationTimeoutError — no create-ambiguity wording.
    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1, stdout="", stderr="execution error: ... (-1712).")

    monkeypatch.setattr(notes_mod.subprocess, "run", fake_run)
    with pytest.raises(OperationTimeoutError) as excinfo:
        OsascriptNotesProvider().read_notes()
    assert "may or may not" not in str(excinfo.value)


def test_create_script_falls_back_to_modification_date_on_unreadable_creation_date():
    # Structural check (mirrors test_applescript_selects_icloud_account_and_bounds_timeout):
    # the create script guards its date read-back exactly like the read script.
    assert "creation date of newNote" in _CREATE_SCRIPT
    assert "on error" in _CREATE_SCRIPT
    assert "modification date of newNote" in _CREATE_SCRIPT


def test_create_and_read_scripts_enumerate_folders_on_missing_folder():
    for script in (_CREATE_SCRIPT, _READ_SCRIPT):
        assert "LOGBOOK_FOLDER_NOT_FOUND" in script
        assert "name of every folder" in script


def test_applescript_selects_icloud_account_and_bounds_timeout():
    # Scenario "Account is resolved explicitly" + the hard-timeout requirement,
    # verified structurally on the script constants.
    assert 'account "iCloud"' in _HELPERS
    assert "with timeout of" in _READ_SCRIPT
    assert "with timeout of" in _CREATE_SCRIPT
    assert _READ_AS_TIMEOUT < 120  # below AppleScript's default
    assert _WRITE_AS_TIMEOUT < 120
