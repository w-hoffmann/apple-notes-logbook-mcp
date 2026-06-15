"""Unit tests for the pure core (tasks 3.1-3.5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from apple_notes_logbook_mcp.core import (
    ConsolidatedLog,
    InvalidDateError,
    RawNote,
    assemble_body,
    consolidate,
    escape_html,
    html_to_text,
    local_date_of,
    parse_iso_date,
)

TODAY = date(2026, 12, 31)


# --- 3.1 escaping & body assembly -----------------------------------------


def test_escape_html_order_ampersand_first():
    assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    # A literal entity in user text must be double-escaped, not left as markup.
    assert escape_html("&lt;") == "&amp;lt;"


def test_assemble_body_summary_only():
    assert assemble_body("Hello world") == "<div>Hello world</div>"


def test_assemble_body_summary_and_detail():
    assert assemble_body("Head", "Detail") == "<div>Head</div><div><br></div><div>Detail</div>"


def test_assemble_body_empty_detail_is_heading_only():
    assert assemble_body("Head", "") == "<div>Head</div>"
    assert assemble_body("Head", None) == "<div>Head</div>"


def test_assemble_body_multiline_detail_one_div_per_line():
    assert assemble_body("H", "l1\nl2") == ("<div>H</div><div><br></div><div>l1</div><div>l2</div>")


def test_assemble_body_escapes_special_characters():
    assert assemble_body("a<b>c&d") == "<div>a&lt;b&gt;c&amp;d</div>"


def test_assemble_body_collapses_newlines_in_summary():
    assert assemble_body("line a\nline b") == "<div>line a line b</div>"


def test_assemble_body_preserves_non_ascii():
    assert assemble_body("äöü — straße") == "<div>äöü — straße</div>"


# --- 3.2 HTML -> plain text -----------------------------------------------


def test_html_to_text_div_and_br_to_newlines():
    html = "<div>Head</div><div><br></div><div>Detail</div>"
    assert html_to_text(html) == "Head\n\nDetail"


def test_html_to_text_decodes_named_entities():
    assert html_to_text("<div>a &amp; b &lt; c &gt; d</div>") == "a & b < c > d"


def test_html_to_text_nbsp_becomes_u00a0_not_space():
    assert html_to_text("<div>a&nbsp;b</div>") == "a b"


def test_html_to_text_numeric_character_reference():
    assert html_to_text("<div>&#8212;</div>") == "—"


def test_html_to_text_collapses_blank_line_runs():
    html = "<div>A</div><div><br></div><div><br></div><div><br></div><div>B</div>"
    assert html_to_text(html) == "A\n\nB"


def test_html_to_text_empty_body():
    assert html_to_text("") == ""


def test_html_to_text_ignores_object_attachment_markup():
    html = "<div>before<object data='x'></object>after</div>"
    assert html_to_text(html) == "beforeafter"


def test_html_to_text_strips_inter_tag_whitespace_from_notes():
    # Notes pretty-prints a newline between block tags; multi-line detail must come
    # back as consecutive lines (no blank line between each), with exactly one blank
    # line after the heading. Mirrors the real stored format observed end to end.
    html = (
        "<div>H</div>\n<div><br></div>\n<div>Zeile 1</div>\n<div>Zeile 2</div>\n<div>Zeile 3</div>"
    )
    assert html_to_text(html) == "H\n\nZeile 1\nZeile 2\nZeile 3"


# --- 3.3 dates & timezone --------------------------------------------------


def test_parse_iso_date_valid():
    assert parse_iso_date("2026-03-14") == date(2026, 3, 14)


@pytest.mark.parametrize("bad", ["2026-3-4", "not-a-date", "2026-13-01", "14-03-2026", ""])
def test_parse_iso_date_rejects_malformed(bad):
    with pytest.raises(InvalidDateError):
        parse_iso_date(bad)


def test_local_date_of_uses_local_zone_at_utc_boundary():
    # 01:00 UTC on the 15th is the previous day (the 14th) at UTC-5.
    instant = datetime(2026, 3, 15, 1, 0, tzinfo=UTC)
    minus5 = timezone(timedelta(hours=-5))
    assert instant.date() == date(2026, 3, 15)  # UTC calendar date
    assert local_date_of(instant, minus5) == date(2026, 3, 14)  # local date


def test_local_date_of_naive_is_treated_as_local():
    instant = datetime(2026, 3, 14, 23, 30)
    assert local_date_of(instant, UTC) == date(2026, 3, 14)


# --- 3.4 consolidation -----------------------------------------------------


def test_consolidate_empty_folder():
    result = consolidate([], tz=UTC, today=TODAY)
    assert result == ConsolidatedLog(count=0, entries_text="")


def test_consolidate_orders_ascending_with_dated_headings():
    notes = [
        RawNote("a", "<div>Later</div>", creation_date=datetime(2026, 3, 21, 10, 0)),
        RawNote("b", "<div>Earlier</div>", creation_date=datetime(2026, 3, 14, 9, 0)),
    ]
    result = consolidate(notes, tz=UTC, today=TODAY)
    assert result.count == 2
    assert result.entries_text == "2026-03-14 — Earlier\n\n2026-03-21 — Later"


def test_consolidate_separator_renders_one_blank_line():
    notes = [
        RawNote(
            "a",
            "<div>Head</div><div><br></div><div>Detail</div>",
            creation_date=datetime(2026, 3, 14, 9, 0),
        )
    ]
    result = consolidate(notes, tz=UTC, today=TODAY)
    assert result.entries_text == "2026-03-14 — Head\n\nDetail"


def test_consolidate_falls_back_to_modification_date():
    notes = [
        RawNote(
            "a",
            "<div>Body</div>",
            creation_date=None,
            modification_date=datetime(2026, 3, 10, 8, 0),
        )
    ]
    result = consolidate(notes, tz=UTC, today=TODAY)
    assert result.entries_text == "2026-03-10 — Body"


def test_consolidate_empty_body_entry():
    notes = [RawNote("a", "", creation_date=datetime(2026, 3, 14, 9, 0))]
    result = consolidate(notes, tz=UTC, today=TODAY)
    assert result.count == 1
    assert result.entries_text == "2026-03-14 — "


def test_consolidate_inclusive_range_filter():
    notes = [
        RawNote(str(d), f"<div>day {d}</div>", creation_date=datetime(2026, 3, d, 9, 0))
        for d in (13, 14, 15, 16)
    ]
    result = consolidate(notes, tz=UTC, today=TODAY, frm=date(2026, 3, 14), to=date(2026, 3, 15))
    assert result.count == 2
    assert "day 14" in result.entries_text
    assert "day 15" in result.entries_text
    assert "day 13" not in result.entries_text
    assert "day 16" not in result.entries_text


def test_consolidate_to_defaults_to_today():
    notes = [
        RawNote("p", "<div>past</div>", creation_date=datetime(2026, 6, 10, 9, 0)),
        RawNote("f", "<div>future</div>", creation_date=datetime(2026, 6, 20, 9, 0)),
    ]
    result = consolidate(notes, tz=UTC, today=date(2026, 6, 15))
    assert result.count == 1
    assert "past" in result.entries_text
    assert "future" not in result.entries_text


def test_consolidate_timezone_boundary_filter_uses_local_date():
    # 01:00 UTC on the 15th -> 14th locally at UTC-5; a from/to of the 14th
    # must include it, and it must render as the 14th.
    minus5 = timezone(timedelta(hours=-5))
    notes = [RawNote("x", "<div>edge</div>", creation_date=datetime(2026, 3, 15, 1, 0, tzinfo=UTC))]
    result = consolidate(notes, tz=minus5, today=TODAY, frm=date(2026, 3, 14), to=date(2026, 3, 14))
    assert result.count == 1
    assert result.entries_text == "2026-03-14 — edge"


# --- 3.5 round-trip --------------------------------------------------------


def test_write_then_read_round_trip_with_special_and_non_ascii():
    summary = "Decision: prefer A > B & keep < notes >"
    detail = "Weil: latency\nund: ä/ö/ü — straße"
    body = assemble_body(summary, detail)
    text = html_to_text(body)
    lines = text.split("\n")
    assert lines[0] == summary
    # one blank line separates heading from detail
    assert lines[1] == ""
    assert "\n".join(lines[2:]) == detail
