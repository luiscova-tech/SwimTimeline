"""Printable badge-card schedules for meet officials, from a HY-TEK Session Report PDF.

One 2"x3" (144x216pt) card per session: event number, abbreviated event name, heat count, and
start time, sized to drop into a credential holder.

Relationship to extract.py -- this module adds NOTHING to it and changes NOTHING in it. The
Session Report is already parsed there by parse_timeline()/cached_timeline(), which returns every
field a card needs (per event: event_number, event_name, heats, start, session_number,
session_name, date; per session: number, name, day_of_meet, date, start_time, finish_time). This
module only imports those results. That matters because parse_timeline() is on the family-facing
swimmer-calendar critical path, and this project's standing rule is not to touch that parsing
unless a change is provably additive -- so the heat-interval scan below re-reads the same page
text through the already-reusable extract_text_pages() instead of extending session_header,
day_header or event_line. See tests/test_badges.py for the regression evidence on both real
timeline fixtures.

What is genuinely new here, and why each piece could not just be reused:
  * parse_heat_intervals()      -- nothing anywhere captured the "Heat Interval:" text.
  * session_age_qualifiers() / abbreviate_age_qualifier() -- the age group was only ever embedded
    in the raw event_name string, never a field, and no code decided constant-vs-mixed per session.
  * badge_event_name()          -- extract.py's event_short_name() STRIPS gender words, but
    badge_lib's gender_color() colors each row by a literal "Boys"/"Girls" prefix, so gender must
    be KEPT. Different contract, separate function.
  * events_by_session()         -- parse_timeline() returns one flat list; nothing grouped it.

draw_card() and its palette are lifted near-verbatim from the already-built, already-visually-
tested badge_lib.py. Its throwaway SESSION1_EVENTS/SESSION1_META sample block is deliberately not
carried over -- real card data comes from build_session_cards() below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .extract import (
    SessionInfo,
    TimelineEvent,
    extract_text_pages,
    normalize_space,
    parse_timeline,
    short_meet_name,
)


# Card geometry: 2" x 3" at 72pt/inch, per the spec.
CARD_W = 144.0
CARD_H = 216.0


# ---------------------------------------------------------------------------
# Card rendering -- near-verbatim from badge_lib.py (already built and tested)
# ---------------------------------------------------------------------------

NAVY = colors.HexColor("#13294B")
NAVY_LIGHT = colors.HexColor("#EAF0F7")
MAROON = colors.HexColor("#7A1F3D")
GRAY_TXT = colors.HexColor("#3A3A3A")
GRAY_LINE = colors.HexColor("#C9C9C9")
STRIPE = colors.HexColor("#F5F6F8")
HEADER_SUB = colors.HexColor("#C7D2E3")


def gender_color(event_name):
    return NAVY if event_name.startswith("Boys") else MAROON


def draw_card(c, ox, oy, W, H, meet_name, session_label, date_label, start_label,
              heat_interval, events, finish_label, cut_marks=True):
    """Draws one badge card with its lower-left corner at (ox, oy) in canvas c,
    occupying a W x H box. Reusable for a single-card PDF or stamped many
    times onto one sheet.

    NOTE on heat_interval: this parameter is declared (and callers pass the real parsed value,
    see build_session_cards) but the current two-line header design does not render it -- the
    header is session label + "meet - date - start". Kept in the signature as-is rather than
    altering this already-visually-tested drawing code.
    """

    margin = 0.055 * W if W < 200 else 0.16 * 72  # tiny inner margin, scales with card
    margin = max(3.5, W * 0.035)
    content_w = W - 2 * margin
    x0 = ox + margin

    # ---------- card outline (helps when printed edge-to-edge) ----------
    if cut_marks:
        c.setStrokeColor(GRAY_LINE)
        c.setLineWidth(0.4)
        c.rect(ox, oy, W, H, stroke=1, fill=0)

    top = oy + H

    # ---------- HEADER (2 lines: session label + compact meta line) ----------
    header_h = 0.13 * H
    c.setFillColor(NAVY)
    c.rect(ox, top - header_h, W, header_h, stroke=0, fill=1)

    sess_fs = max(8.5, W * 0.068)
    meta_fs = max(4.6, W * 0.038)

    ln1 = top - header_h * 0.42
    ln2 = top - header_h * 0.80

    # session label, shrink-to-fit
    label = session_label
    fs = sess_fs
    max_w = W * 0.94
    while stringWidth(label, "Helvetica-Bold", fs) > max_w and fs > 6.5:
        fs -= 0.3
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", fs)
    c.drawCentredString(ox + W / 2, ln1, label)

    # compact meta line: meet + date + start, shrink-to-fit
    meta = f"{meet_name} • {date_label} • Start {start_label}"
    fs2 = meta_fs
    while stringWidth(meta, "Helvetica", fs2) > max_w and fs2 > 3.6:
        fs2 -= 0.2
    c.setFillColor(HEADER_SUB)
    c.setFont("Helvetica", fs2)
    c.drawCentredString(ox + W / 2, ln2, meta)

    # ---------- FOOTER ----------
    footer_h = 0.08 * H
    fin_fs = max(6, W * 0.048)
    c.setStrokeColor(GRAY_LINE)
    c.setLineWidth(0.4)
    c.line(x0, oy + footer_h, x0 + content_w, oy + footer_h)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", fin_fs)
    c.drawCentredString(ox + W / 2, oy + footer_h * 0.32, f"Est. Finish {finish_label}")

    # ---------- TABLE ----------
    table_top = top - header_h - 0.01 * H
    table_bottom = oy + footer_h + 0.01 * H
    table_h = table_top - table_bottom

    HEADER_ROW_FRAC = 0.62
    units = len(events) + HEADER_ROW_FRAC
    row_h = table_h / units
    hdr_row_h = row_h * HEADER_ROW_FRAC

    col_num_w = content_w * 0.10
    col_time_w = content_w * 0.225
    col_ht_w = content_w * 0.20
    col_event_w = content_w - col_num_w - col_time_w - col_ht_w

    x_num = x0
    x_event = x_num + col_num_w
    x_ht = x_event + col_event_w
    x_time = x_ht + col_ht_w

    base_fs = max(5.0, min(8.3, row_h * 0.5))
    hdr_fs = max(4.0, base_fs - 1.1)

    # column header row
    y = table_top - hdr_row_h
    c.setFillColor(NAVY_LIGHT)
    c.rect(x0, y, content_w, hdr_row_h, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", hdr_fs)
    c.drawString(x_num + 1.5, y + hdr_row_h / 2 - hdr_fs * 0.32, "#")
    c.drawString(x_event + 1.5, y + hdr_row_h / 2 - hdr_fs * 0.32, "EVENT")
    c.drawCentredString(x_ht + col_ht_w / 2, y + hdr_row_h / 2 - hdr_fs * 0.32, "HEATS")
    c.drawRightString(x_time + col_time_w - 2, y + hdr_row_h / 2 - hdr_fs * 0.32, "TIME")

    for i, ev in enumerate(events):
        y = table_top - hdr_row_h - row_h * (i + 1)
        if i % 2 == 1:
            c.setFillColor(STRIPE)
            c.rect(x0, y, content_w, row_h, stroke=0, fill=1)

        c.setFillColor(gender_color(ev["name"]))
        c.rect(x0, y, max(1.2, W * 0.012), row_h, stroke=0, fill=1)

        c.setFillColor(GRAY_TXT)
        c.setFont("Helvetica-Bold", base_fs)
        c.drawString(x_num + 3, y + row_h / 2 - base_fs * 0.33, str(ev["num"]))

        name = ev["name"]
        max_w = col_event_w - 4
        fs = base_fs
        while stringWidth(name, "Helvetica", fs) > max_w and fs > 4.0:
            fs -= 0.2
        c.setFont("Helvetica", fs)
        c.drawString(x_event + 2, y + row_h / 2 - fs * 0.33, name)

        c.setFont("Helvetica", base_fs)
        c.drawCentredString(x_ht + col_ht_w / 2, y + row_h / 2 - base_fs * 0.33, str(ev["heats"]))

        c.setFont("Helvetica-Bold", base_fs)
        c.drawRightString(x_time + col_time_w - 2, y + row_h / 2 - base_fs * 0.33, ev["time"])

        c.setStrokeColor(GRAY_LINE)
        c.setLineWidth(0.3)
        c.line(x0, y, x0 + content_w, y)

    c.setStrokeColor(GRAY_LINE)
    c.setLineWidth(0.5)
    c.rect(x0, table_bottom, content_w, table_h, stroke=1, fill=0)


# ---------------------------------------------------------------------------
# New: heat interval (isolated re-scan, extract.py untouched)
# ---------------------------------------------------------------------------

# HY-TEK prints the interval on the SAME line as "Day of Meet:", after the start time -- which is
# exactly where extract.py's day_header regex stops. Rather than extend that regex (it decides
# whether a session exists at all for the family-facing calendar), this re-scans the same page
# text independently: worst case it finds nothing and callers get no interval.
_SESSION_LINE_RE = re.compile(r"Session:\s*(\d+)\b")
_HEAT_INTERVAL_RE = re.compile(r"Heat\s+Interval:\s*(?P<interval>.+?)\s*$", re.IGNORECASE)


def parse_heat_intervals(timeline_pdf: Path) -> dict[int, str]:
    """Heat interval text per session number, e.g. {1: "25 Seconds / Back +15 Seconds"}.

    Real strings vary in part count -- Herculean prints two parts ("25 Seconds / Back +15
    Seconds"), WZAG prints three on its prelim sessions ("20 Seconds / Back +10 Seconds / Chase
    -30") and two on its finals -- so the whole remainder of the line is captured verbatim rather
    than split into a fixed shape. Sessions whose line omits it are simply absent from the result.
    """
    intervals: dict[int, str] = {}
    for page_text in extract_text_pages(Path(timeline_pdf)):
        current: int | None = None
        for raw_line in page_text.splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue
            session_match = _SESSION_LINE_RE.search(line)
            if session_match:
                current = int(session_match.group(1))
            if current is None:
                continue
            interval_match = _HEAT_INTERVAL_RE.search(line)
            if interval_match:
                intervals.setdefault(current, normalize_space(interval_match.group("interval")))
    return intervals


def compact_heat_interval(interval: str | None) -> str:
    """Shorten an interval for display, following badge_lib's own "25s/Back+15" sample convention:
    the leading base interval keeps a unit suffix, later qualifiers drop the word entirely.

      "25 Seconds / Back +15 Seconds"             -> "25s/Back+15"
      "20 Seconds / Back +10 Seconds / Chase -30" -> "20s/Back+10/Chase-30"   (WZAG prelims)

    Unrecognized text passes through with whitespace collapsed rather than being dropped.
    """
    if not interval:
        return ""
    parts: list[str] = []
    for index, part in enumerate(interval.split("/")):
        token = normalize_space(part)
        # First part is the base interval ("25 Seconds" -> "25s"); later parts are qualifiers
        # ("Back +15 Seconds" -> "Back+15") where the unit is already implied by the base.
        replacement = "s" if index == 0 else ""
        token = re.sub(r"\s*Seconds?\b", replacement, token, flags=re.IGNORECASE)
        token = re.sub(r"\s*([+-])\s*", r"\1", token)
        parts.append(normalize_space(token))
    return "/".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# New: event name -> (gender, age qualifier, distance/stroke)
# ---------------------------------------------------------------------------

# HY-TEK event names are "<Gender> <Age qualifier> <Distance> <Stroke>", e.g.
# "Girls 12 & Over 100 Freestyle". "Mixed" shows up on WZAG's relays ("Mixed 10 & Under 200
# Freestyle Relay"). The age group is non-greedy so it yields to the distance digits.
_EVENT_NAME_RE = re.compile(
    r"^(?P<gender>Girls|Boys|Women|Men|Mixed)\s+(?P<age>.+?)\s+(?P<distance>\d+)\s+(?P<stroke>.+)$"
)

_STROKE_ABBREVIATIONS = [
    (re.compile(r"\bFreestyle\b", re.IGNORECASE), "Free"),
    (re.compile(r"\bBackstroke\b", re.IGNORECASE), "Back"),
    (re.compile(r"\bBreaststroke\b", re.IGNORECASE), "Breast"),
    (re.compile(r"\bButterfly\b", re.IGNORECASE), "Fly"),
]


@dataclass(frozen=True)
class ParsedEventName:
    gender: str
    age_qualifier: str
    distance_stroke: str


def parse_event_name(event_name: str) -> ParsedEventName | None:
    """Split a raw HY-TEK event name into gender / age qualifier / distance+stroke.

    None when the name does not follow that shape, so callers fall back to the raw text instead of
    rendering a mangled guess.
    """
    match = _EVENT_NAME_RE.match(normalize_space(event_name))
    if not match:
        return None
    return ParsedEventName(
        gender=match.group("gender"),
        age_qualifier=normalize_space(match.group("age")),
        distance_stroke=f"{match.group('distance')} {normalize_space(match.group('stroke'))}",
    )


def abbreviate_stroke(text: str) -> str:
    """Freestyle->Free, Backstroke->Back, Breaststroke->Breast, Butterfly->Fly; IM unchanged."""
    result = text
    for pattern, replacement in _STROKE_ABBREVIATIONS:
        result = pattern.sub(replacement, result)
    return normalize_space(result)


def abbreviate_age_qualifier(qualifier: str) -> str:
    """General rule, not a hardcoded list: "N & Under"->"N&U", "N & Over"->"N&O", "N-M" unchanged,
    "N Year Olds"->"Nyo". Anything else collapses whitespace and is otherwise left alone.
    """
    text = normalize_space(qualifier)
    under = re.fullmatch(r"(\d+)\s*&\s*Under", text, flags=re.IGNORECASE)
    if under:
        return f"{under.group(1)}&U"
    over = re.fullmatch(r"(\d+)\s*&\s*Over", text, flags=re.IGNORECASE)
    if over:
        return f"{over.group(1)}&O"
    year_olds = re.fullmatch(r"(\d+)\s*Year\s*Olds?", text, flags=re.IGNORECASE)
    if year_olds:
        return f"{year_olds.group(1)}yo"
    return text


def badge_event_name(event_name: str, include_age: bool) -> str:
    """The row label for a badge card: gender KEPT (badge_lib's gender_color() colors each row by
    a literal "Boys"/"Girls" prefix), stroke abbreviated, age qualifier included only when the
    session is mixed.

    Deliberately NOT extract.py's event_short_name(), which strips gender words entirely -- that
    would send every row through gender_color()'s else-branch and paint the whole card maroon.

    "Mixed" relay events keep that token, so they take gender_color()'s non-Boys branch (maroon).
    """
    parsed = parse_event_name(event_name)
    if parsed is None:
        return abbreviate_stroke(event_name)
    pieces = [parsed.gender]
    if include_age:
        pieces.append(abbreviate_age_qualifier(parsed.age_qualifier))
    pieces.append(abbreviate_stroke(parsed.distance_stroke))
    return " ".join(piece for piece in pieces if piece)


# ---------------------------------------------------------------------------
# New: grouping and per-session age analysis
# ---------------------------------------------------------------------------


def events_by_session(events: list[TimelineEvent]) -> dict[int, list[TimelineEvent]]:
    """parse_timeline() returns one flat event list; this groups it by session_number, each
    session's events ordered by start time then event number. Session order follows first
    appearance, which for a Session Report is meet order.
    """
    grouped: dict[int, list[TimelineEvent]] = {}
    for event in events:
        grouped.setdefault(event.session_number, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda event: (event.start, event.event_number))
    return grouped


def session_age_qualifiers(session_events: list[TimelineEvent]) -> list[str]:
    """The distinct raw age qualifiers in one session, in first-appearance order."""
    seen: list[str] = []
    for event in session_events:
        parsed = parse_event_name(event.event_name)
        if parsed is None:
            continue
        if parsed.age_qualifier not in seen:
            seen.append(parsed.age_qualifier)
    return seen


def constant_age_qualifier(session_events: list[TimelineEvent]) -> str | None:
    """The one age qualifier shared by every event in the session, or None when it is mixed.

    Constant -> the caller drops it from each row and states it once in the card header. Mixed ->
    each row keeps a short tag. Both cases occur in real data: Herculean's 12&Over sessions are
    constant while its 11&Under sessions mix "10 & Under" / "10-11" / "11 & Under" / "11 Year
    Olds"; every WZAG session is mixed.
    """
    qualifiers = session_age_qualifiers(session_events)
    if len(qualifiers) != 1:
        return None
    # An unparseable event name has no known age, so constancy can't be claimed over it -- keep the
    # per-row tags instead of asserting a qualifier that may not cover every row.
    if any(parse_event_name(event.event_name) is None for event in session_events):
        return None
    return qualifiers[0]


# ---------------------------------------------------------------------------
# New: card data assembly
# ---------------------------------------------------------------------------


def session_crosses_noon(session_events: list[TimelineEvent]) -> bool:
    """True when a session spans both AM and PM, so row times cannot safely drop the meridiem.

    The spec assumed a session never crosses noon and flagged it as worth checking -- it is not
    true in real data: WZAG's Wednesday/Thursday/Saturday prelims all start 8:30 AM and run past
    noon (finishing 12:49/12:55/12:53 PM).
    """
    return len({event.start.strftime("%p") for event in session_events}) > 1


def row_time_label(moment: datetime, keep_meridiem: bool) -> str:
    """A row's start time: "5:30" normally (the header states "Start 5:30 PM" once), or "12:30p"
    when the session crosses noon and the bare clock would be ambiguous.
    """
    clock = moment.strftime("%I:%M").lstrip("0")
    if not keep_meridiem:
        return clock
    return f"{clock}{moment.strftime('%p').lower()[0]}"


def format_clock_label(value: str | None) -> str:
    """A 24-hour "HH:MM" session time (SessionInfo.start_time/finish_time) as "5:30 PM"."""
    if not value:
        return ""
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError:
        return value
    return datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p").lstrip("0")


# A sanction number is meet paperwork, not something an official reads off a badge, and WZAG's
# parsed meet name carries one (", Sanction #: SR2608-CH01"). It matters because draw_card's
# shrink-to-fit floors at 3.6pt and then draws anyway: the untrimmed 70-character name overflows
# the meta line and clips at both ends on a 2" card.
_SANCTION_SUFFIX_RE = re.compile(r",?\s*Sanction\s*#?\s*:?.*$", re.IGNORECASE)


def badge_meet_name(meet_name: str) -> str:
    """The meet name as a badge card's meta line should carry it.

    Reuses extract.py's short_meet_name() for the shared normalization (drop a leading year, "MAC
    "/"Arizona " prefixes, "Invitational" -> "Invite") and additionally drops a trailing sanction
    clause. short_meet_name() is deliberately NOT changed to do this itself -- it also names the
    family-facing calendars, whose titles existing tests pin.
    """
    cleaned = _SANCTION_SUFFIX_RE.sub("", normalize_space(meet_name)).strip(" ,")
    return short_meet_name(cleaned) if cleaned else normalize_space(meet_name)


def format_date_label(day: date) -> str:
    """"Fri, Sept 11" -- the compact form the card header meta line uses."""
    month = day.strftime("%B")
    short_month = "Sept" if month == "September" else day.strftime("%b")
    return f"{day.strftime('%a')}, {short_month} {day.day}"


@dataclass
class SessionCard:
    """Everything one card needs, in exactly the shape draw_card() consumes."""

    session_number: int
    session_name: str
    meet_name: str
    session_label: str
    date_label: str
    start_label: str
    finish_label: str
    heat_interval: str
    age_qualifier: str | None  # the constant one, when the session has it
    events: list[dict]

    @property
    def event_count(self) -> int:
        return len(self.events)


def build_session_cards(
    meet_name: str,
    sessions: dict[int, SessionInfo],
    events: list[TimelineEvent],
    heat_intervals: dict[int, str] | None = None,
) -> list[SessionCard]:
    """One SessionCard per session, in meet order (session number).

    The card header carries the constant age qualifier when there is one ("SESSION 1 - 12 & OVER");
    when the session is mixed it carries the meet's own session name instead ("SESSION 2 - FRIDAY
    PM 11&UNDER") so a two-pool meet's cards stay distinguishable, and each row keeps its own age
    tag.
    """
    heat_intervals = heat_intervals or {}
    grouped = events_by_session(events)
    card_meet_name = badge_meet_name(meet_name)
    cards: list[SessionCard] = []
    for session_number in sorted(grouped):
        session_events = grouped[session_number]
        session = sessions.get(session_number)
        constant_age = constant_age_qualifier(session_events)
        keep_meridiem = session_crosses_noon(session_events)
        session_name = session.name if session else f"Session {session_number}"
        suffix = constant_age.upper() if constant_age else session_name.upper()
        rows = [
            {
                "num": event.event_number,
                "name": badge_event_name(event.event_name, include_age=constant_age is None),
                "heats": event.heats if event.heats is not None else "",
                "time": row_time_label(event.start, keep_meridiem),
            }
            for event in session_events
        ]
        session_date = session.date if session else session_events[0].date
        cards.append(
            SessionCard(
                session_number=session_number,
                session_name=session_name,
                meet_name=card_meet_name,
                session_label=f"SESSION {session_number} — {suffix}",
                date_label=format_date_label(session_date),
                start_label=format_clock_label(session.start_time if session else None)
                or row_time_label(session_events[0].start, True),
                finish_label=format_clock_label(session.finish_time if session else None) or "--",
                heat_interval=compact_heat_interval(heat_intervals.get(session_number)),
                age_qualifier=constant_age,
                events=rows,
            )
        )
    return cards


def cards_for_timeline(
    timeline_pdf: Path, flyer_text: str = "", meet_venue: str | None = None
) -> tuple[str, list[SessionCard]]:
    """Read a Session Report PDF straight through to card data: (meet_name, cards).

    parse_timeline() supplies every field except the heat interval, which parse_heat_intervals()
    adds from its own isolated scan of the same pages.
    """
    timeline_pdf = Path(timeline_pdf)
    meet_name, sessions, events = parse_timeline(timeline_pdf, flyer_text=flyer_text, meet_venue=meet_venue)
    if not events:
        raise ValueError("No sessions or events were found in that timeline PDF.")
    intervals = parse_heat_intervals(timeline_pdf)
    return meet_name, build_session_cards(meet_name, sessions, events, intervals)


# ---------------------------------------------------------------------------
# New: PDF output
# ---------------------------------------------------------------------------


def render_cards_pdf(cards: list[SessionCard]) -> bytes:
    """One 144x216pt page per card, in the order given -- the same draw_card() call per session
    whether this is the whole meet (many pages) or a single session (one page).
    """
    if not cards:
        raise ValueError("No sessions to render.")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(CARD_W, CARD_H))
    for card in cards:
        pdf.setTitle(f"{card.meet_name} badge cards")
        draw_card(
            pdf,
            0,
            0,
            CARD_W,
            CARD_H,
            meet_name=card.meet_name,
            session_label=card.session_label,
            date_label=card.date_label,
            start_label=card.start_label,
            heat_interval=card.heat_interval,
            events=card.events,
            finish_label=card.finish_label,
        )
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def card_filename(meet_name: str, card: SessionCard | None = None) -> str:
    """A safe download filename: whole meet, or one session."""
    slug = re.sub(r"[^a-z0-9]+", "-", meet_name.lower()).strip("-") or "meet"
    slug = slug[:60].strip("-")
    if card is None:
        return f"{slug}-badge-cards.pdf"
    return f"{slug}-session-{card.session_number}-badge-card.pdf"
