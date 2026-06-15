"""Tests for the Notes I/O boundary: folder-name config, error mapping, parsing."""

from __future__ import annotations

from datetime import datetime

from apple_notes_logbook_mcp.notes import (
    _FS,
    _RS,
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
