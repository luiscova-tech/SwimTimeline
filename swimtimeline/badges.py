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

from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
import math
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .failure_alerts import InputError
from .site import CARD_CREDIT_URL
from .extract import (
    AMBIGUOUS_NAME_MARKER,
    SessionInfo,
    TimelineEvent,
    extract_psych_entries,
    extract_text_pages,
    normalize_space,
    parse_timeline,
    short_meet_name,
)


# Card geometry: 2" x 3" at 72pt/inch, per the spec.
CARD_W = 144.0
CARD_H = 216.0
# Vertical budget, as fractions of card height, shared with anything that has to reason about the
# table's available space (tests/test_badges.py replicates draw_card's row-height math to check
# that a star or an event name still fits). These were duplicated as bare literals in both places
# until the footer grew a second line for the site URL and the copies silently disagreed -- one
# definition now, so that can't happen again.
CARD_HEADER_FRAC = 0.13
CARD_FOOTER_FRAC = 0.10  # two lines: "Est. Finish ..." plus site.CARD_CREDIT_URL
CARD_TABLE_GAP_FRAC = 0.01  # breathing room above AND below the table, so 2x this in total

# Print-sheet geometry: several DIFFERENT sessions' cards tiled on shared letter pages, at native
# card size. This exists so a whole meet's reference schedule doesn't print as N mostly-blank
# full sheets, one per session -- it is NOT the cut-out-and-wear format (that is render_cards_pdf's
# one-card-per-page output, whose page IS the card), and NOT the deferred "12-up" idea from the
# original spec (which repeats ONE session's card 12 times to hand out to that session's officials).
SHEET_W = 612.0  # US Letter, 8.5" x 11"
SHEET_H = 792.0
SHEET_GUTTER = 18.0  # 0.25" between neighbouring cards, so a cut line is visible between them
# 3 columns, not 4. Four native-width cards need 4 x 144 = 576pt, which leaves only 18pt of side
# margin with a ZERO gutter, and overflows the sheet outright (-9pt) once any gutter is added --
# and 18pt (0.25") is exactly the unprintable edge on typical consumer laser/inkjet printers, so
# the outer cards' borders would be clipped. Three columns fit with room to spare.
SHEET_COLS = 3
SHEET_ROWS = 3
SHEET_SLOTS_PER_PAGE = SHEET_COLS * SHEET_ROWS
# Derived, not hardcoded, so changing the grid or gutter keeps the block centred: the 3x3/18pt
# grid lands on exactly 72pt (1.00") horizontal and 54pt (0.75") vertical margins.
SHEET_MARGIN_X = (SHEET_W - (SHEET_COLS * CARD_W + (SHEET_COLS - 1) * SHEET_GUTTER)) / 2
SHEET_MARGIN_Y = (SHEET_H - (SHEET_ROWS * CARD_H + (SHEET_ROWS - 1) * SHEET_GUTTER)) / 2


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
# Watched-swimmer highlight: a warm fill that stays legible behind the same GRAY_TXT row text and
# is clearly distinct from STRIPE's near-white zebra banding, plus a deeper gold for the star.
GOLD = colors.HexColor("#FDF0C2")
GOLD_MARK = colors.HexColor("#A8760B")


def gender_color(event_name):
    return NAVY if event_name.startswith("Boys") else MAROON


def num_column_fraction(events: list[dict]) -> float:
    """What share of the card width the # column takes, decided ONCE per card.

    Per card, not per row, so every row's columns stay aligned. Two things widen it beyond the
    0.10 default, and the extra always comes out of EVENT, which has the most slack and its own
    shrink-to-fit:
      * a star needs room beside the number (0.145, unchanged);
      * a combined Girls/Boys row's number is "95/96" rather than at most 3 digits.
    A card with neither returns exactly 0.10, and a card with only stars exactly 0.145, which is
    what keeps every card that has no combined row rendering byte-identically to before.

    Exposed as a function so tests that replay draw_card's geometry can read the real value
    instead of restating the fractions -- those copies had already drifted apart once.
    """
    any_highlight = any(ev.get("highlight") for ev in events)
    any_combined = any(len(ev.get("nums") or ()) > 1 for ev in events)
    if any_highlight and any_combined:
        return 0.185
    if any_highlight:
        return 0.145
    if any_combined:
        return 0.13
    return 0.10


def row_accent_colors(row: dict) -> list:
    """The accent-bar colour(s) for one card row: two for a combined Girls/Boys row, one otherwise.

    A combined row's name has no gender word left for gender_color() to read, so its colours come
    from the row's recorded "genders" instead. Every other row -- including one whose name did not
    parse and therefore has no recorded gender -- falls back to the original gender_color(name)
    call, so its bar is unchanged.
    """
    genders = row.get("genders") or ()
    if len(genders) == 2:
        return [gender_color(gender) for gender in genders]
    return [gender_color(row["name"])]


def draw_star(c, cx, cy, radius, points=5):
    """A filled five-pointed star centred on (cx, cy), drawn with canvas path primitives.

    Deliberately NOT a text glyph. Helvetica's WinAnsiEncoding contains no star at all, yet
    stringWidth("★", "Helvetica", 8) still returns a plausible 6.53 -- so glyph code looks
    correct, raises nothing, and measures fine. What actually happens is that reportlab silently
    substitutes a ZapfDingbats resource into the PDF for U+2605 (verified by inspecting the
    generated file's /Font dict), while its near neighbour U+2606 renders as a tofu box. That
    substitution is outside our control and viewer-dependent, which is unacceptable for a card
    whose whole purpose is to be printed. A vector path renders identically everywhere, and stays
    crisp at the ~3pt marker size a 28-event session's row height forces.
    """
    outer = radius
    inner = radius * 0.42
    path = c.beginPath()
    for index in range(points * 2):
        # Start at the TOP point and alternate outer/inner vertices. PDF user space has y
        # increasing upward, so "up" is +pi/2 here -- using -pi/2 (as screen coordinates would)
        # draws the star point-down, which is a recognisably wrong star rather than an obvious
        # bug, so it is worth being explicit about.
        angle = math.pi / 2 + index * math.pi / points
        r = outer if index % 2 == 0 else inner
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def draw_card(c, ox, oy, W, H, meet_name, session_label, date_label, start_label,
              heat_interval, events, finish_label, cut_marks=True):
    """Draws one badge card with its lower-left corner at (ox, oy) in canvas c,
    occupying a W x H box. Reusable for a single-card PDF or stamped many
    times onto one sheet.

    Each entry in `events` is a dict of {num, name, heats, time}, plus an optional truthy
    "highlight" key marking an event a watched swimmer is entered in: that row takes a gold fill
    instead of its normal/zebra one and gains a vector star in the # column (see draw_star). Rows
    without the key render exactly as before.

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
    header_h = CARD_HEADER_FRAC * H
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

    # ---------- FOOTER (2 lines: est. finish + where to make your own) ----------
    # Taller than the original single-line footer by 0.02*H to make room for the URL line. That
    # 4.3pt comes out of the table, which at the worst real case (WZAG's 28-event session) is
    # already at its 5.0pt font floor either way -- so it costs a little row padding, not
    # legibility. Verified by rasterizing that exact session, not assumed.
    footer_h = CARD_FOOTER_FRAC * H
    fin_fs = max(6, W * 0.048)
    c.setStrokeColor(GRAY_LINE)
    c.setLineWidth(0.4)
    c.line(x0, oy + footer_h, x0 + content_w, oy + footer_h)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", fin_fs)
    c.drawCentredString(ox + W / 2, oy + footer_h * 0.50, f"Est. Finish {finish_label}")
    # Whoever is handed this card can get their own. Deliberately the bare base path from
    # site.CARD_CREDIT_URL -- no scheme, no query string -- since this is read off paper and
    # retyped. Smaller and grey so it stays clearly secondary to the finish time, with the same
    # shrink-to-fit guard every other text element here has.
    url_fs = max(4.0, W * 0.035)
    while stringWidth(CARD_CREDIT_URL, "Helvetica", url_fs) > content_w and url_fs > 3.2:
        url_fs -= 0.1
    c.setFillColor(GRAY_TXT)
    c.setFont("Helvetica", url_fs)
    c.drawCentredString(ox + W / 2, oy + footer_h * 0.14, CARD_CREDIT_URL)

    # ---------- TABLE ----------
    table_top = top - header_h - CARD_TABLE_GAP_FRAC * H
    table_bottom = oy + footer_h + CARD_TABLE_GAP_FRAC * H
    table_h = table_top - table_bottom

    HEADER_ROW_FRAC = 0.62
    units = len(events) + HEADER_ROW_FRAC
    row_h = table_h / units
    hdr_row_h = row_h * HEADER_ROW_FRAC

    # The # column normally takes 10% of the card, which a 3-digit event number already nearly
    # fills on its own. When this card has any starred row, it widens ONCE for the whole card --
    # not per row -- so the star and the number both sit at full size and every row's columns stay
    # aligned. The extra comes out of EVENT, which has the most slack and its own shrink-to-fit.
    # A combined Girls/Boys row's number is "95/96" -- roughly 5 characters where a single row's
    # worst case is 3 -- so the column widens ONCE for the whole card when any row is combined,
    # on the same all-rows-stay-aligned principle as the star widening. Measured against the real
    # 42-event session (21 combined rows at the 5.0pt floor): "95/96" needs ~12.5pt, which does
    # not fit the default 13.4pt column once the 3pt left pad is taken, but fits 0.13 comfortably.
    # The star case needs room for star + "95/96" together, hence the widest bucket.
    col_num_w = content_w * num_column_fraction(events)
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

    accent_w = max(1.2, W * 0.012)
    for i, ev in enumerate(events):
        y = table_top - hdr_row_h - row_h * (i + 1)
        highlighted = bool(ev.get("highlight"))
        if highlighted:
            # Gold REPLACES this row's normal/zebra fill (it must win on both odd and even rows),
            # and is painted before the accent bar so the bar still reads on top of it.
            c.setFillColor(GOLD)
            c.rect(x0, y, content_w, row_h, stroke=0, fill=1)
        elif i % 2 == 1:
            c.setFillColor(STRIPE)
            c.rect(x0, y, content_w, row_h, stroke=0, fill=1)

        # Accent bar. A combined row splits it -- top half the first event's gender colour, bottom
        # half the second's -- so the pairing reads at a glance. This is secondary reinforcement
        # only: the # and HEATS columns already disambiguate the two events positionally, so a
        # reader who cannot tell the colours apart loses nothing. A one-event row takes the exact
        # same single-colour path (and the same gender_color(name) call) it always has.
        accents = row_accent_colors(ev)
        if len(accents) == 2:
            half = row_h / 2
            c.setFillColor(accents[0])
            c.rect(x0, y + half, accent_w, row_h - half, stroke=0, fill=1)
            c.setFillColor(accents[1])
            c.rect(x0, y, accent_w, half, stroke=0, fill=1)
        else:
            c.setFillColor(accents[0])
            c.rect(x0, y, accent_w, row_h, stroke=0, fill=1)

        num_text = str(ev["num"])
        num_x = x_num + 3
        num_fs = base_fs
        if highlighted:
            # Star first, then the number. The star gives up size before the number does: it is
            # sized to whatever is left beside a full-size number (down to a still-visible floor),
            # and only if that floor still does not fit does the number shrink -- the same
            # measured-width idiom the event name and header lines already use, rather than
            # letting either spill into the EVENT column.
            slot_left = x_num + accent_w + 0.6
            slot_right = x_event - 1.0
            room = (slot_right - slot_left) - stringWidth(num_text, "Helvetica-Bold", base_fs) - 0.8
            star_r = min(row_h * 0.24, base_fs * 0.34, 2.6, max(room, 0.0) / 2)
            star_r = max(star_r, min(1.15, row_h * 0.24))
            star_cx = slot_left + star_r
            c.setFillColor(GOLD_MARK)
            draw_star(c, star_cx, y + row_h / 2, star_r)
            num_x = star_cx + star_r + 0.8
            available = slot_right - num_x
            while stringWidth(num_text, "Helvetica-Bold", num_fs) > available and num_fs > 3.6:
                num_fs -= 0.2
        else:
            # Plain rows had no shrink-to-fit at all: a 3-digit number always fit the 10% column,
            # so the number was the one text element on the card drawn at a fixed size. A combined
            # row's "95/96" can exceed even the widened column on a narrow card, so the same
            # measured-width guard the star branch (and the event name) already use now applies
            # here too. It is a no-op for every number that already fit, which is why plain rows
            # still render byte-identically.
            available = (x_event - 1.0) - num_x
            while stringWidth(num_text, "Helvetica-Bold", num_fs) > available and num_fs > 3.6:
                num_fs -= 0.2

        c.setFillColor(GRAY_TXT)
        c.setFont("Helvetica-Bold", num_fs)
        c.drawString(num_x, y + row_h / 2 - num_fs * 0.33, num_text)

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
# (\S+) mirrors extract.py's session_header token exactly, so these keys always match
# SessionInfo.number. The old (\d+)\b matched NOTHING on a lettered id -- there is no word
# boundary between "1" and "B" -- so "1B"'s interval was silently attributed to whichever session
# was seen last, and the first session of a lettered meet got none at all.
_SESSION_LINE_RE = re.compile(r"Session:\s*(\S+)")
_HEAT_INTERVAL_RE = re.compile(r"Heat\s+Interval:\s*(?P<interval>.+?)\s*$", re.IGNORECASE)


def parse_heat_intervals(timeline_pdf: Path) -> dict[str, str]:
    """Heat interval text per session label, e.g. {"1": "25 Seconds / Back +15 Seconds"}, or
    {"1B": ...} for a meet that runs two pools in parallel.

    Real strings vary in part count -- Herculean prints two parts ("25 Seconds / Back +15
    Seconds"), WZAG prints three on its prelim sessions ("20 Seconds / Back +10 Seconds / Chase
    -30") and two on its finals -- so the whole remainder of the line is captured verbatim rather
    than split into a fixed shape. Sessions whose line omits it are simply absent from the result.
    """
    intervals: dict[str, str] = {}
    for page_text in extract_text_pages(Path(timeline_pdf)):
        current: str | None = None
        for raw_line in page_text.splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue
            session_match = _SESSION_LINE_RE.search(line)
            if session_match:
                current = session_match.group(1)
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


def badge_event_name(event_name: str, include_age: bool, include_gender: bool = True) -> str:
    """The row label for a badge card: gender KEPT by default (badge_lib's gender_color() colors
    each row by a literal "Boys"/"Girls" prefix), stroke abbreviated, age qualifier included only
    when the session is mixed.

    Deliberately NOT extract.py's event_short_name(), which strips gender words entirely -- that
    would send every row through gender_color()'s else-branch and paint the whole card maroon.

    "Mixed" relay events keep that token, so they take gender_color()'s non-Boys branch (maroon).

    include_gender=False drops the leading gender word for a COMBINED Girls/Boys row (see
    combine_gender_pairs), where one shared name covers both events and printing either gender
    would be wrong. Such a row carries its colors separately (the row dict's "genders"), so
    gender_color() is not consulted for it -- which is why dropping the word here is safe. The
    default keeps every existing caller's output byte-identical.
    """
    parsed = parse_event_name(event_name)
    if parsed is None:
        return abbreviate_stroke(event_name)
    pieces = [parsed.gender] if include_gender else []
    if include_age:
        pieces.append(abbreviate_age_qualifier(parsed.age_qualifier))
    pieces.append(abbreviate_stroke(parsed.distance_stroke))
    return " ".join(piece for piece in pieces if piece)


# ---------------------------------------------------------------------------
# New: which events a watched swimmer is entered in
# ---------------------------------------------------------------------------


@dataclass
class SwimmerHighlights:
    """Which event numbers to star, plus who resolved and who didn't.

    event_numbers is the UNION across every name that resolved -- highlighting is deliberately
    uniform, with no per-swimmer colour, so an official scanning the card sees "one of mine" at a
    glance rather than decoding a legend.
    """

    event_numbers: set[int]
    matched: dict[str, list[int]]  # typed name -> that swimmer's own event numbers
    warnings: list[str]
    # Raw exceptions from reading the psych sheet, kept alongside the human-readable warning. The
    # per-name swallow below is deliberate (one unreadable name must not cost the other names
    # their highlights), but a psych sheet that will not parse is still a real document failure
    # someone should hear about -- so the library REPORTS it here and the server layer decides
    # whether to email, rather than this module knowing anything about alerting.
    processing_errors: list[tuple[str, Exception]] = field(default_factory=list)

    @property
    def any_matched(self) -> bool:
        return bool(self.event_numbers)


def swimmer_event_numbers(psych_pdf: Path, swimmer_names: list[str]) -> SwimmerHighlights:
    """Resolve each typed name against the meet's psych sheet and collect the event numbers.

    Matching itself is entirely extract_psych_entries() -- the same machinery the family calendar
    flow uses, including its exact/fuzzy passes and its ambiguity guard -- so a name behaves here
    exactly as it does there. The only new part is the reduction to a set of event numbers.

    Each name is processed INDEPENDENTLY: extract_psych_entries() reports an ambiguous or
    unresolvable name by returning no entries plus a warning string (ambiguity is flagged by
    AMBIGUOUS_NAME_MARKER appearing in that string, not by an exception), so one bad name
    contributes no highlights and its warning is surfaced, while every other name in the batch
    still highlights normally.
    """
    event_numbers: set[int] = set()
    matched: dict[str, list[int]] = {}
    warnings: list[str] = []
    processing_errors: list[tuple[str, Exception]] = []
    for swimmer_name in swimmer_names:
        name = swimmer_name.strip()
        if not name:
            continue
        try:
            entries, _page_counts, name_warnings = extract_psych_entries(Path(psych_pdf), name)
        except Exception as exc:  # A bad psych sheet must not take the whole card down.
            warnings.append(f"Could not search the psych sheet for '{name}': {exc}")
            processing_errors.append((name, exc))
            continue
        warnings.extend(name_warnings)
        numbers = sorted({entry.event_number for entry in entries})
        if numbers:
            matched[name] = numbers
            event_numbers.update(numbers)
        elif not name_warnings:
            # No entries and nothing already said why -- say it, rather than silently
            # highlighting nothing.
            warnings.append(f"'{name}' was not found in this meet's psych sheet.")
    return SwimmerHighlights(
        event_numbers=event_numbers,
        matched=matched,
        warnings=warnings,
        processing_errors=processing_errors,
    )


def is_ambiguous_warning(warning: str) -> bool:
    """Whether a warning from swimmer_event_numbers is the "be more specific" kind, so the page can
    say that instead of "not found" (the same distinction the family payload draws)."""
    return AMBIGUOUS_NAME_MARKER in warning


# ---------------------------------------------------------------------------
# New: grouping and per-session age analysis
# ---------------------------------------------------------------------------


_SESSION_SORT_RE = re.compile(r"(\d+)(.*)$")


def session_sort_key(session_number: str) -> tuple[int, str]:
    """Meet order for a session LABEL: numeric prefix first, then the suffix.

    A plain str sort is wrong in both directions and silently so:
      * Existing plain-numbered meets break. Shark Open has ten sessions, and sorted() on strings
        puts "10" immediately after "1", so its cards would print 1, 10, 2, 3... -- a real
        regression to a real hosted meet, introduced by nothing more than the type change.
      * Lettered meets need the numeric part compared as a number too, so "2G" precedes "3" and
        "4B" follows it, which is the meet's real chronological order.
    A label with no leading digits sorts last rather than raising, so an unanticipated format can
    never crash a download.
    """
    match = _SESSION_SORT_RE.match(session_number)
    if not match:
        return (10**9, session_number)
    return (int(match.group(1)), match.group(2))


def events_by_session(events: list[TimelineEvent]) -> dict[str, list[TimelineEvent]]:
    """parse_timeline() returns one flat event list; this groups it by session_number, each
    session's events ordered by start time then event number. Session order follows first
    appearance, which for a Session Report is meet order.
    """
    grouped: dict[str, list[TimelineEvent]] = {}
    for event in events:
        grouped.setdefault(event.session_number, []).append(event)
    for session_events in grouped.values():
        session_events.sort(key=lambda event: (event.start, event.event_number))
    return grouped


_COMBINABLE_GENDERS = frozenset({"Girls", "Boys"})


def events_combine(first: TimelineEvent, second: TimelineEvent) -> bool:
    """True when these two events are the Girls/Boys halves of the same race and can share a row.

    Both names must parse, the pair must be exactly one Girls and one Boys, and the age qualifier
    and distance+stroke must match exactly. Order-agnostic on purpose: every real fixture checked
    prints Girls first, but nothing in the document format guarantees it, and a meet that printed
    Boys first should still combine rather than silently rendering twice as many rows.

    "Mixed"/"Women"/"Men" never combine -- a Mixed relay is one event with its own entry, not half
    of a pair -- and neither does a name that does not parse, which falls through to a normal row.
    """
    left = parse_event_name(first.event_name)
    right = parse_event_name(second.event_name)
    if left is None or right is None:
        return False
    if {left.gender, right.gender} != _COMBINABLE_GENDERS:
        return False
    return (
        left.age_qualifier == right.age_qualifier
        and left.distance_stroke == right.distance_stroke
    )


def combine_gender_pairs(session_events: list[TimelineEvent]) -> list[list[TimelineEvent]]:
    """Group one session's events into rows: [[a, b], [c], [d, e], ...].

    A single left-to-right greedy scan over the events in the order they were already sorted (start
    time, then event number) -- nothing is reordered, and only the IMMEDIATE neighbour is ever
    considered. A successful pair consumes both events and advances two; anything else falls
    through as a one-event row and advances one.

    Deliberately not a smarter matcher: searching past the neighbour (or sorting to bring halves
    together) would reorder a card away from the running order an official reads it in, which is
    the one thing the time column exists to convey. A meet whose data is not strictly interleaved
    simply renders fewer combined rows, which is a legibility loss, not a correctness one.
    """
    rows: list[list[TimelineEvent]] = []
    index = 0
    while index < len(session_events):
        current = session_events[index]
        following = session_events[index + 1] if index + 1 < len(session_events) else None
        if following is not None and events_combine(current, following):
            rows.append([current, following])
            index += 2
        else:
            rows.append([current])
            index += 1
    return rows


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
# A single-day meet's parsed name carries its own date: parse_meet_name()'s title branch requires
# the "M/D/YYYY to M/D/YYYY" range shape, so a one-day Session Report ("2026 Croswhite Invite -
# 9/12/2026") falls through to its keyword branch and keeps the whole line, date included. The
# card's meta line already prints the date right next to the name, so leaving it in prints it
# twice and eats header width that shrink-to-fit then has to give back.
_TRAILING_DATE_RE = re.compile(r"\s*[-–—]\s*\d{1,2}/\d{1,2}/\d{4}\s*$")


def badge_meet_name(meet_name: str) -> str:
    """The meet name as a badge card's meta line should carry it.

    Reuses extract.py's short_meet_name() for the shared normalization (drop a leading year, "MAC
    "/"Arizona " prefixes, "Invitational" -> "Invite") and additionally drops a trailing sanction
    clause and a trailing date. short_meet_name() is deliberately NOT changed to do either -- it
    also names the family-facing calendars, whose titles existing tests pin.
    """
    cleaned = normalize_space(meet_name)
    cleaned = _SANCTION_SUFFIX_RE.sub("", cleaned).strip(" ,")
    cleaned = _TRAILING_DATE_RE.sub("", cleaned).strip(" ,-")
    return short_meet_name(cleaned) if cleaned else normalize_space(meet_name)


def format_date_label(day: date) -> str:
    """"Fri, Sept 11" -- the compact form the card header meta line uses."""
    month = day.strftime("%B")
    short_month = "Sept" if month == "September" else day.strftime("%b")
    return f"{day.strftime('%a')}, {short_month} {day.day}"


@dataclass
class SessionCard:
    """Everything one card needs, in exactly the shape draw_card() consumes."""

    session_number: str
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
        """How many real EVENTS this session has -- not how many rows the card prints.

        These stopped being the same number once adjacent Girls/Boys events began sharing a row:
        a 42-event session prints 21 rows. This feeds the officials page's "EVENTS" column and its
        "N sessions - M events" summary, which describe the meet, so they must keep counting
        events. Use len(card.events) for the row count.
        """
        return sum(len(row["nums"]) for row in self.events)

    @property
    def row_count(self) -> int:
        """How many rows this card prints -- one per single event, one per combined Girls/Boys
        pair. This is what draw_card's row-height math divides the table by."""
        return len(self.events)

    @property
    def highlighted_event_numbers(self) -> list[int]:
        """The starred event numbers on this card, for the page's own session list.

        Reads "starred_nums" -- the event numbers on this row that are ACTUALLY watched -- rather
        than the "num" display text (a combined row's is the string "95/96") or the row's full
        "nums". When only one half of a combined Girls/Boys row is a watched swimmer's event, the
        row still draws a star, but only that one event number belongs in this list; returning both
        would tell the officials page a swimmer is in a race they are not entered in.
        """
        return [number for row in self.events for number in row.get("starred_nums") or ()]


def build_card_row(
    row_events: list[TimelineEvent],
    include_age: bool,
    keep_meridiem: bool,
    highlight_events: set[int],
) -> dict:
    """One card row from one or two events (see combine_gender_pairs).

    A ONE-event row is byte-identical to what this module has always produced, down to `num` and
    `heats` staying the original int/"" values rather than becoming strings.

    A TWO-event (combined Girls/Boys) row:
      * num/heats carry BOTH values joined with "/", in the events' existing order -- the two
        columns line up positionally, so neither needs a gender label. This project already leans
        on that convention (the card has no legend for anything).
      * name is printed ONCE with the gender word dropped.
      * time is the EARLIER of the two starts only. The time column's job is telling an official
        when to be at their post; showing the later one risks missing the first race of the pair.
      * highlight is true if EITHER event is watched, so combining a row can never hide a starred
        swimmer. starred_nums keeps only the halves that really are watched, so the page's own
        starred list stays truthful for a one-sided pair.
      * genders records each event's gender in the same order, for the split accent bar -- the row
        name no longer carries a gender word for gender_color() to read.
    """
    first = row_events[0]
    nums = [event.event_number for event in row_events]
    starred = [number for number in nums if number in highlight_events]
    genders = []
    for event in row_events:
        parsed = parse_event_name(event.event_name)
        if parsed is not None:
            genders.append(parsed.gender)
    heats = ["" if event.heats is None else event.heats for event in row_events]
    combined = len(row_events) > 1
    return {
        "num": "/".join(str(n) for n in nums) if combined else nums[0],
        "nums": nums,
        "name": badge_event_name(first.event_name, include_age=include_age, include_gender=not combined),
        "heats": "/".join(str(h) for h in heats) if combined else heats[0],
        "time": row_time_label(min(event.start for event in row_events), keep_meridiem),
        "highlight": bool(starred),
        "starred_nums": starred,
        "genders": genders,
    }


def build_session_cards(
    meet_name: str,
    sessions: dict[str, SessionInfo],
    events: list[TimelineEvent],
    heat_intervals: dict[str, str] | None = None,
    highlight_events: set[int] | None = None,
) -> list[SessionCard]:
    """One SessionCard per session, in meet order (see session_sort_key).

    The card header carries the constant age qualifier when there is one ("SESSION 1 - 12 & OVER");
    when the session is mixed it carries the meet's own session name instead ("SESSION 2 - FRIDAY
    PM 11&UNDER") so a two-pool meet's cards stay distinguishable, and each row keeps its own age
    tag.

    highlight_events is a set of event numbers (see swimmer_event_numbers) whose rows draw_card
    should star. Event numbers are unique across a whole Session Report, so a set is enough to
    place every watched swimmer's events on whichever cards they fall on.
    """
    heat_intervals = heat_intervals or {}
    highlight_events = highlight_events or set()
    grouped = events_by_session(events)
    card_meet_name = badge_meet_name(meet_name)
    cards: list[SessionCard] = []
    for session_number in sorted(grouped, key=session_sort_key):
        session_events = grouped[session_number]
        session = sessions.get(session_number)
        constant_age = constant_age_qualifier(session_events)
        keep_meridiem = session_crosses_noon(session_events)
        session_name = session.name if session else f"Session {session_number}"
        suffix = constant_age.upper() if constant_age else session_name.upper()
        rows = [
            build_card_row(row_events, include_age=constant_age is None,
                           keep_meridiem=keep_meridiem, highlight_events=highlight_events)
            for row_events in combine_gender_pairs(session_events)
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
    timeline_pdf: Path,
    flyer_text: str = "",
    meet_venue: str | None = None,
    psych_pdf: Path | None = None,
    swimmer_names: list[str] | None = None,
) -> tuple[str, list[SessionCard], SwimmerHighlights]:
    """Read a Session Report PDF straight through to card data:
    (meet_name, cards, highlights).

    parse_timeline() supplies every field except the heat interval, which parse_heat_intervals()
    adds from its own isolated scan of the same pages.

    When both a psych sheet and swimmer names are given, those swimmers' events are resolved
    through swimmer_event_numbers() and starred on whichever cards they fall on. With either
    missing, highlights come back empty and every row renders exactly as it did before this
    feature existed.
    """
    timeline_pdf = Path(timeline_pdf)
    meet_name, sessions, events = parse_timeline(timeline_pdf, flyer_text=flyer_text, meet_venue=meet_venue)
    if not events:
        raise ValueError("No sessions or events were found in that timeline PDF.")
    intervals = parse_heat_intervals(timeline_pdf)
    if psych_pdf and swimmer_names:
        highlights = swimmer_event_numbers(Path(psych_pdf), swimmer_names)
    elif swimmer_names:
        highlights = SwimmerHighlights(
            event_numbers=set(),
            matched={},
            warnings=[
                "No psych sheet is available for this meet, so swimmer events can't be "
                "highlighted. Upload a psych/heat sheet alongside the timeline to highlight them."
            ],
        )
    else:
        highlights = SwimmerHighlights(event_numbers=set(), matched={}, warnings=[])
    cards = build_session_cards(
        meet_name, sessions, events, intervals, highlight_events=highlights.event_numbers
    )
    return meet_name, cards, highlights


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
        pdf.setTitle(f"{card.meet_name} session event cards")
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


def sheet_slot_origin(slot_index: int) -> tuple[float, float]:
    """The (x, y) lower-left corner of one grid slot on a print sheet, in reading order: left to
    right, top row first. slot_index is the position WITHIN a page (0 .. SHEET_SLOTS_PER_PAGE-1).

    Split out from render_sheet_pdf so the grid itself is checkable without parsing PDF bytes.
    PDF user space puts y=0 at the BOTTOM, so the top row is the highest y, not the lowest.
    """
    if not 0 <= slot_index < SHEET_SLOTS_PER_PAGE:
        raise ValueError(f"slot_index {slot_index} is outside a {SHEET_SLOTS_PER_PAGE}-slot page.")
    row, column = divmod(slot_index, SHEET_COLS)
    x = SHEET_MARGIN_X + column * (CARD_W + SHEET_GUTTER)
    y = SHEET_H - SHEET_MARGIN_Y - (row + 1) * CARD_H - row * SHEET_GUTTER
    return x, y


def render_sheet_pdf(cards: list[SessionCard]) -> bytes:
    """Letter-size sheets with up to SHEET_SLOTS_PER_PAGE different sessions' cards tiled on each,
    every card drawn at its native 144x216pt size by the SAME draw_card() call the one-card-per-page
    output uses -- no scaling, no separate drawing path, so a card here is byte-for-byte the same
    content as its standalone page.

    Cards fill reading order and wrap onto further sheets past nine. That pagination is NOT
    exercised by any real fixture in this repo today: the largest, WZAG, has 8 sessions and
    Herculean has 6, so both land on a single sheet. The loop is written for it regardless rather
    than assuming one page is always enough.
    """
    if not cards:
        raise ValueError("No sessions to render.")
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(SHEET_W, SHEET_H))
    pdf.setTitle(f"{cards[0].meet_name} session event card sheets")
    for index, card in enumerate(cards):
        slot = index % SHEET_SLOTS_PER_PAGE
        if slot == 0 and index:
            pdf.showPage()  # previous sheet is full
        origin_x, origin_y = sheet_slot_origin(slot)
        draw_card(
            pdf,
            origin_x,
            origin_y,
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


# Resource-abuse guard for the "handout" layout below: /api/officials/badges has no auth, and
# rendering builds the whole PDF in memory before responding, so an unbounded `copies` value from
# an arbitrary query param is a real DoS/memory vector on a free-tier host -- not a hypothetical
# one, since nothing else on this request path limits it. Measured cost scales linearly at roughly
# 0.6ms and 0.6KB PER COPY (benchmarked by rendering a real card 9/100/500/1000/5000 times), so an
# unbounded request costs the server exactly as much as the attacker asks it to. 200 copies is
# ~0.12s and ~120KB -- trivial -- while comfortably exceeding any real single-session officiating
# crew (even a very large meet's session realistically runs a few dozen officials, not hundreds):
# 200 copies is ~23 letter sheets, already more than anyone would want to cut apart and laminate
# by hand.
MAX_HANDOUT_COPIES = 200


def render_handout_sheet_pdf(card: SessionCard, copies: int) -> bytes:
    """`copies` copies of ONE session's card, tiled across as many 9-per-sheet pages as needed.

    This is the original spec's deferred "12-up" idea: enough copies of one session's card to hand
    out to that session's officials, each cut apart from the sheet. It is built on the exact same
    grid as the different-sessions sheet layout -- literally render_sheet_pdf() fed `copies`
    references to the SAME card instead of one card per session -- so the slot geometry and
    multi-sheet pagination are not reimplemented at all.
    """
    if copies < 1:
        raise InputError("copies must be at least 1.")
    if copies > MAX_HANDOUT_COPIES:
        raise InputError(f"copies is capped at {MAX_HANDOUT_COPIES} per request.")
    return render_sheet_pdf([card] * copies)


def card_filename(
    meet_name: str,
    card: SessionCard | None = None,
    layout: str = "cards",
    highlighted_only: bool = False,
    copies: int | None = None,
) -> str:
    """A safe download filename: whole meet, one session, tiled print sheets, or N copies of one
    session's card.

    highlighted_only is part of the name because a filtered and an unfiltered download of the same
    meet are different documents -- without it the browser just appends "(1)" and the two are
    indistinguishable in a Downloads folder. copies works the same way: it names the count so a
    10-copy and a 50-copy handout of the same session don't collide either.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", meet_name.lower()).strip("-") or "meet"
    slug = slug[:60].strip("-")
    if card is not None:
        # Sanitized the same way the meet slug above is: a session id is the meet's own label, so
        # it is not guaranteed to be filename-safe the way a bare integer was.
        session_slug = re.sub(r"[^A-Za-z0-9]+", "-", str(card.session_number)).strip("-") or "x"
        if copies:
            return f"{slug}-session-{session_slug}-session-event-cards-x{copies}.pdf"
        return f"{slug}-session-{session_slug}-session-event-card.pdf"
    suffix = "-highlighted" if highlighted_only else ""
    if layout == "sheet":
        return f"{slug}-session-event-card-sheets{suffix}.pdf"
    return f"{slug}-session-event-cards{suffix}.pdf"
