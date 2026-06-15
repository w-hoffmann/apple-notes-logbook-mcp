"""Pure, side-effect-free core logic for the Logbook MCP server.

This module knows nothing about Apple Events or MCP. It handles:

* HTML escaping and note-body assembly for the write path (D6).
* HTML -> plain-text conversion for the read path (D6).
* Date parsing/validation and user-local-timezone date derivation (D7).
* Date-range filtering and chronological consolidation (D3/D7).

Everything here is deterministic and fully unit-testable; the user-local time
zone and "today" are injected so timezone-boundary behaviour is testable.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, tzinfo

# Em dash used between the date and the entry's first line in read_log output.
ENTRY_SEPARATOR = " — "

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidDateError(ValueError):
    """Raised when a date string is not a valid ``YYYY-MM-DD`` value."""

    def __init__(self, value: object) -> None:
        super().__init__(
            f"Invalid date {value!r}; expected ISO format YYYY-MM-DD (e.g. 2026-03-14)."
        )


@dataclass(frozen=True)
class RawNote:
    """A single note as read from the Notes provider, before consolidation.

    ``creation_date``/``modification_date`` are timezone-aware instants (or
    naive, treated as already-local). At least one of them should be present;
    consolidation falls back to ``modification_date`` when ``creation_date`` is
    missing (Apple Events ``-1728``), per the spec.
    """

    id: str
    body_html: str
    creation_date: datetime | None = None
    modification_date: datetime | None = None


@dataclass(frozen=True)
class ConsolidatedLog:
    """Result of :func:`consolidate`: the rendered block plus its entry count."""

    count: int
    entries_text: str


# ---------------------------------------------------------------------------
# Write path: escaping + body assembly (task 3.1)
# ---------------------------------------------------------------------------


def escape_html(text: str) -> str:
    """HTML-escape ``text``, escaping ``&`` first, then ``<`` and ``>``.

    Quotes are intentionally left untouched: the value is placed in element
    text content, not an attribute.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _collapse_to_single_line(text: str) -> str:
    """Collapse any newline run into a single space so a heading stays one line.

    Internal single spaces are preserved; only newline-bearing whitespace runs
    and surrounding whitespace are normalised.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s*\n\s*", " ", normalized).strip()


def _div(inner: str) -> str:
    return f"<div>{inner}</div>"


def assemble_body(summary: str, detail: str | None = None) -> str:
    """Assemble the HTML body for a new note.

    The heading is the first ``<div>`` (single line). When ``detail`` is given,
    a single ``<div><br></div>`` separator follows, then each detail line is its
    own ``<div>`` (empty lines render as ``<div><br></div>``). The note is body
    only; the ``name``/title is left for Notes to derive from the first line.
    """
    heading = _collapse_to_single_line(summary)
    parts = [_div(escape_html(heading))]
    if detail:
        parts.append("<div><br></div>")
        normalized = detail.replace("\r\n", "\n").replace("\r", "\n")
        for line in normalized.split("\n"):
            parts.append("<div><br></div>" if line == "" else _div(escape_html(line)))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Read path: HTML -> plain text (task 3.2)
# ---------------------------------------------------------------------------

# Formatting whitespace between tags (e.g. Notes pretty-prints "</div>\n<div>").
# Safe to drop because any content `<`/`>` is stored escaped as `&lt;`/`&gt;`, so
# a raw `>...<` only ever spans real tag boundaries.
_INTER_TAG_WS_RE = re.compile(r">\s+<")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_CLOSE_BLOCK_RE = re.compile(r"</(?:div|p)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of blank lines to a single blank line and trim the ends."""
    out: list[str] = []
    prev_blank = False
    for line in text.split("\n"):
        blank = line.strip() == ""
        if blank and prev_blank:
            continue
        out.append(line)
        prev_blank = blank
    while out and out[0].strip() == "":
        out.pop(0)
    while out and out[-1].strip() == "":
        out.pop()
    return "\n".join(out)


def html_to_text(body_html: str) -> str:
    """Convert a Notes HTML body to clean plain text.

    Block boundaries (``</div>``, ``</p>``, ``<br>``) become newlines; all other
    tags (including ``<object>``/attachment markup) are stripped; HTML entities
    are decoded via :func:`html.unescape` (so ``&nbsp;`` becomes U+00A0 and
    numeric references resolve to their codepoints); blank-line runs collapse to
    one and surrounding whitespace is trimmed.

    Tags are stripped *before* entity decoding so that escaped content such as
    ``&lt;div&gt;`` decodes to the literal text ``<div>`` rather than being
    mistaken for markup. Insignificant whitespace between tags (Notes pretty-prints
    a newline between block elements) is dropped first so it does not turn into a
    spurious blank line between body lines.
    """
    text = _INTER_TAG_WS_RE.sub("><", body_html)
    text = _BR_RE.sub("\n", text)
    text = _CLOSE_BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return _collapse_blank_lines(text)


# ---------------------------------------------------------------------------
# Dates & timezone (task 3.3)
# ---------------------------------------------------------------------------


def parse_iso_date(value: str) -> date:
    """Parse a strict ``YYYY-MM-DD`` string into a :class:`date`.

    Raises :class:`InvalidDateError` for anything else (including the leniency
    that bare :func:`datetime.strptime` would otherwise allow, e.g. ``2026-3-4``).
    """
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise InvalidDateError(value)
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidDateError(value) from exc


def local_date_of(instant: datetime, tz: tzinfo) -> date:
    """Return the calendar date of ``instant`` in time zone ``tz``.

    A naive ``instant`` is assumed to already be in local time.
    """
    if instant.tzinfo is None:
        return instant.date()
    return instant.astimezone(tz).date()


def _in_range(value: date, frm: date | None, to: date | None) -> bool:
    if frm is not None and value < frm:
        return False
    return not (to is not None and value > to)


# ---------------------------------------------------------------------------
# Consolidation (task 3.4)
# ---------------------------------------------------------------------------


def _effective_instant(note: RawNote) -> datetime | None:
    """Pick ``creation_date``, falling back to ``modification_date`` (``-1728``)."""
    return note.creation_date or note.modification_date


def consolidate(
    notes: list[RawNote],
    *,
    tz: tzinfo,
    today: date,
    frm: date | None = None,
    to: date | None = None,
) -> ConsolidatedLog:
    """Filter, sort and render notes into a single chronological text block.

    Entries are filtered inclusively by the local calendar date of their
    effective instant (``to`` defaults to ``today`` when omitted), sorted
    ascending by that instant, and rendered as ``YYYY-MM-DD — {first line}``
    with any further body lines unchanged below; entries are separated by a
    single blank line.
    """
    effective_to = to if to is not None else today

    dated: list[tuple[datetime, date, str, RawNote]] = []
    for note in notes:
        instant = _effective_instant(note)
        if instant is None:
            # Defensive: spec guarantees a fallback date, but never crash.
            entry_date = today
            sort_key = datetime.min
        else:
            entry_date = local_date_of(instant, tz)
            sort_key = instant
        if _in_range(entry_date, frm, effective_to):
            dated.append((sort_key, entry_date, note.id, note))

    dated.sort(key=lambda item: (item[0], item[2]))

    blocks = [_render_entry(entry_date, note) for _, entry_date, _, note in dated]
    return ConsolidatedLog(count=len(blocks), entries_text="\n\n".join(blocks))


def _render_entry(entry_date: date, note: RawNote) -> str:
    text = html_to_text(note.body_html)
    lines = text.split("\n") if text else [""]
    lines[0] = f"{entry_date.isoformat()}{ENTRY_SEPARATOR}{lines[0]}"
    return "\n".join(lines)
