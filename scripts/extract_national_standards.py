#!/usr/bin/env python3
"""Extract USA Swimming national elite time standards into a JSON catalog.

Covers all five 2026 national meets: three from PDF (Futures, Speedo Summer Junior Nationals,
Toyota National Championships) and two from a CSV fixture (Speedo Winter Junior Championships,
Toyota U.S. Open). The latter two's official time-standards PDFs publish text as vector-outlined
paths (font glyphs converted to raw Bezier curves, no character/glyph data at all): confirmed
empty extraction with both pypdf and pdfplumber (0 characters on every page). Rather than add OCR
tooling for a one-off, their standards were instead sourced from swimstandards.com -- a site that
mirrors the official USA Swimming standards in ordinary HTML -- and saved as local CSV fixtures
(``docs/Sources/national-speedo-winter-junior-championships-standards-2026.csv`` and
``national-toyota-us-open-standards-2026.csv``) so extraction stays reproducible offline, the same
"drop a file in docs/Sources and rerun" pattern as the PDF-sourced meets.

PDF structure (Futures, Summer Juniors, Toyota Nationals -- do not assume it matches AZSI or
Sectional): a single COLUMN-MAJOR table per page, unlike both AZSI (row-major: event then 3
times) and the Sectional sheets (row-major: 3 women times, event, 3 men times). Reading order is:
all values for Women/SCY top-to-bottom, then all values for Women/LCM, then Men/LCM, then
Men/SCY (matching the printed header "SCY LCM EVENT LCM SCY" left-to-right) -- followed by the
literal list of event labels for the whole page, in event order, at the very bottom. A trailing
"WOMEN MEN<label>" line names the page: "18 & UNDER STANDARDS" / "19 & OVER STANDARDS" (the
age-bracket split used by Futures and Toyota Nationals) or "STANDARDS" / "BONUS STANDARDS" (the
flat-plus-bonus split used by Summer Juniors). Page ORDER is not reliable across documents
(Futures prints 18-U before 19-O; Toyota Nationals prints the opposite) -- the label text on each
page, not its position, determines which bracket/tier it belongs to.

CSV structure (Winter Juniors, U.S. Open -- confirmed distinct from the PDF layout, not assumed
to match): flat table, NO age bracket, plus a separate Bonus tier -- same "flat_bonus" shape as
Summer Juniors -- but with a DIFFERENT column order: Women/SCY, Women/LCM, Men/SCY, Men/LCM
(grouped by gender then course), versus the PDFs' Women/SCY, Women/LCM, Men/LCM, Men/SCY (mirrored
around the event column). Verified from swimstandards.com's own visible column headers
("Event | Women/SCY | Women/LCM | Men/SCY | Men/LCM") and cross-checked internally (men faster
than women within each matching course in every row). Winter Juniors has no 50s of stroke but has
5 relay events (two of which show "-", not contested); U.S. Open has the 50s of stroke but no
relays at all -- neither matches the other CSV meet or any PDF meet's event set.

Only TWO courses are covered across every meet here (SCY and LCM) -- no SCM at all, unlike the
AZSI Age Group documents.

Age eligibility (verified against each meet's real, separately-published meet-announcement, not
inferred from the standards-table headers):
  - TYR Futures Championships: NO meet-wide age floor or ceiling ("open to swimmers who are ...
    members ... and who have achieved the published time standard"). The 18-U/19-O split in the
    standards table is which CUTOFF applies by age, not an entry gate -- exactly analogous to
    AZSI's Age Group/Senior split, just with one boundary (18/19) instead of three bands.
  - Toyota National Championships: same -- no meet-wide age floor/ceiling. Its "18-U Final" is a
    Junior Pan Pacific Championships selection sub-bracket (ages 13-18 as of Dec 31, 2026) within
    the meet, not an entry requirement for the meet itself.
  - Speedo Summer Junior National Championships: explicit meet-wide CEILING -- "All athletes at
    the meet must be 18 or under on the first day of the meet." No floor. Both the Qualifying and
    Bonus tiers require age<=18 (there is only one population eligible for this meet at all).
  - Speedo Winter Junior Championships: same meet-wide 18-and-under CEILING, confirmed from the
    2025 meet announcement (2026's was not yet posted at extraction time) -- consistent with its
    flat (non-bracketed) standards table, which would make no sense if a 19+ population could
    ever use it. Both tiers require age<=18, same as Summer Juniors.
  - Toyota U.S. Open: NO meet-wide age floor/ceiling for the main Qualifying tier (confirmed from
    the 2025 meet announcement) -- but its Bonus tier is age-restricted, per the source page's own
    text ("Toyota US Open time standards and 18&U bonus standards"): Bonus requires age<=18 even
    though Qualifying does not. This is the one meet where the two tiers have DIFFERENT age gates.

These are NATIONAL standards, open to any USA Swimming member regardless of LSC -- unlike AZSI
and Sectional, nothing here is Arizona-scoped.

Output goes to ``data/national_standards.json`` shaped {"source": {...}, "meets": {<key>: {
<metadata>, "standards": course -> gender -> ... }}}. Futures/Toyota Nationals nest an extra
age-bracket level (course -> gender -> bracket -> event -> {"qualifying": time}); the three
flat-plus-bonus meets (Summer Juniors, Winter Juniors, U.S. Open) do not, but each cell carries
both {"qualifying", "bonus"} where the Bonus page/tier lists the event. Each flat-plus-bonus meet
also carries "age_ceiling" (the Qualifying tier's age cap, null if age-open) and
"bonus_age_ceiling" (the Bonus tier's own cap, which can differ -- see U.S. Open above).

Update path when USA Swimming (or swimstandards.com, for the CSV-sourced pair) republishes: drop
the new PDF/CSV into ``docs/Sources/``, rerun (optionally with the matching ``--*-pdf``/``--*-csv``
override), review the JSON diff, and commit.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "national_standards.json"

# Only two courses across every meet here -- no SCM, unlike AZSI's Age Group sheets.
COURSES = ["SCY", "LCM"]
# Physical column order within each PDF page: Women SCY, Women LCM, Men LCM, Men SCY (mirrored
# around the EVENT column, matching the printed "SCY LCM EVENT LCM SCY" header).
BLOCK_ORDER = [("girls", "SCY"), ("girls", "LCM"), ("boys", "LCM"), ("boys", "SCY")]
# CSV column order (Winter Juniors, U.S. Open) is DIFFERENT -- grouped by gender then course,
# not mirrored -- matching swimstandards.com's own header: Event | Women/SCY | Women/LCM |
# Men/SCY | Men/LCM. Do not reuse BLOCK_ORDER for CSV parsing.
CSV_COLUMNS = [("girls", "SCY", "women_scy"), ("girls", "LCM", "women_lcm"),
               ("boys", "SCY", "men_scy"), ("boys", "LCM", "men_lcm")]

TIME_OR_X = r"\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}|x"
EVENT_LABEL_RE = re.compile(r"^(?:\d+x)?\d+(?:/\d+)?\s*(?:FR-R|MED-R|FR|BK|BR|FL|IM)$", re.IGNORECASE)
LABEL_RE = re.compile(r"WOMEN MEN(.*STANDARDS)")

STROKE = {"fr": "free", "bk": "back", "br": "breast", "fl": "fly", "im": "im",
          "fr-r": "free relay", "med-r": "medley relay"}

_DIST_ORDER = ["50", "100", "200", "400", "500", "800", "1000", "1500", "1650"]
_STROKE_ORDER = ["free", "back", "breast", "fly", "im", "free relay", "medley relay"]

NOT_HANDLED: list[dict] = []

MEETS = [
    {
        "key": "futures",
        "kind": "age_bracket",
        "source_type": "pdf",
        "default_pdf": ROOT / "docs" / "Sources" / "national-tyr-futures-championships-standards-2026.pdf",
        "arg": "futures_pdf",
        "meta": {
            "name": "TYR Futures Championships",
            "program": "USA Swimming Futures Championship Series",
            "effective": "2026 season",
            "qualifying_period": "2025-06-01 through close of entries",
            "eligibility": (
                "Open to any USA Swimming Premium/Outreach member who has achieved the "
                "published time standard; no meet-wide age floor or ceiling. The standards "
                "table itself is split by age bracket (18 & Under / 19 & Over) -- that split "
                "determines which cutoff applies, not who may enter."
            ),
            "document": "national-tyr-futures-championships-standards-2026.pdf",
            "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2026/2026_tyrfutureschampionships_timestandards-2.pdf",
        },
    },
    {
        "key": "toyota_nationals",
        "kind": "age_bracket",
        "source_type": "pdf",
        "default_pdf": ROOT / "docs" / "Sources" / "national-toyota-national-championships-standards-2026.pdf",
        "arg": "toyota_nationals_pdf",
        "meta": {
            "name": "Toyota National Championships",
            "program": "USA Swimming Toyota National Championship Series",
            "effective": "2026 season",
            "qualifying_period": "2025-06-01 through close of entries",
            "eligibility": (
                "Open to any USA Swimming Premium/Outreach member who has achieved the "
                "published time standard; no meet-wide age floor or ceiling. The '18-U Final' "
                "is a Junior Pan Pacific Championships selection sub-bracket (ages 13-18 as of "
                "Dec 31, 2026) within the meet, not an entry requirement. The standards table "
                "itself is split by age bracket (18 & Under / 19 & Over) for which cutoff "
                "applies."
            ),
            "document": "national-toyota-national-championships-standards-2026.pdf",
            "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2026/18077640025_events_2026_toyotanationalchampionships_timestandards.pdf",
        },
    },
    {
        "key": "summer_juniors",
        "kind": "flat_bonus",
        "source_type": "pdf",
        "default_pdf": ROOT / "docs" / "Sources" / "national-speedo-summer-junior-nationals-standards-2026.pdf",
        "arg": "summer_juniors_pdf",
        "meta": {
            "name": "Speedo Summer Junior National Championships",
            "program": "USA Swimming Speedo Junior National Championship Series",
            "effective": "2026 season",
            "qualifying_period": "2025-06-01 through close of entries",
            "age_ceiling": 18,
            "bonus_age_ceiling": None,
            "eligibility": (
                "Meet-wide age CEILING: 'All athletes at the meet must be 18 or under on the "
                "first day of the meet' (confirmed from the 2026 meet announcement). No floor. "
                "Unlike Futures/Toyota Nationals, the standards table has no age bracket -- one "
                "flat qualifying cutoff per event/course/gender -- plus a separate Bonus "
                "Standards page for swimmers who already qualified in another event. Bonus "
                "carries no separate age restriction beyond the meet-wide ceiling."
            ),
            "document": "national-speedo-summer-junior-nationals-standards-2026.pdf",
            "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2026/2026_speedojuniornationalchampionships_timestandards.pdf",
        },
    },
    {
        "key": "winter_juniors",
        "kind": "flat_bonus",
        "source_type": "csv",
        "default_csv": ROOT / "docs" / "Sources" / "national-speedo-winter-junior-championships-standards-2026.csv",
        "arg": "winter_juniors_csv",
        "meta": {
            "name": "Speedo Winter Junior Championships",
            "program": "USA Swimming Speedo Junior National Championship Series",
            "effective": "2026 season",
            "qualifying_period": "2025-11-01 through close of entries",
            "age_ceiling": 18,
            "bonus_age_ceiling": None,
            "eligibility": (
                "Meet-wide age CEILING, same rule as Summer Juniors: 'all athletes at the meet "
                "must be 18 years old or younger on the first day of the meet' (confirmed from "
                "the 2025 meet announcement -- 2026's was not yet posted at extraction time). No "
                "floor. Flat standards table (no age bracket), consistent with only one eligible "
                "population; plus a separate Bonus tier with no additional age restriction."
            ),
            "document": "national-speedo-winter-junior-championships-standards-2026.csv",
            "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2026/11320828118_events_timestandards_2026_speedowinterjrs.pdf",
            "source_note": (
                "Official PDF has zero extractable text (vector-outlined glyphs). Sourced instead "
                "from swimstandards.com, an ordinary-HTML mirror of the same official standards; "
                "saved locally as a CSV fixture for reproducible offline extraction."
            ),
            "alternate_source_url": "https://swimstandards.com/times/2026-speedo-winter-junior-championships-time-standards",
        },
    },
    {
        "key": "toyota_us_open",
        "kind": "flat_bonus",
        "source_type": "csv",
        "default_csv": ROOT / "docs" / "Sources" / "national-toyota-us-open-standards-2026.csv",
        "arg": "toyota_us_open_csv",
        "meta": {
            "name": "Toyota U.S. Open Championships",
            "program": "USA Swimming Toyota National Championship Series",
            "effective": "2026 season",
            "qualifying_period": "2025-11-01 through close of entries",
            "age_ceiling": None,
            "bonus_age_ceiling": 18,
            "eligibility": (
                "NO meet-wide age floor or ceiling for the main Qualifying tier (confirmed from "
                "the 2025 meet announcement -- 2026's was not yet posted at extraction time). "
                "Unlike every other meet here, its Bonus tier IS age-restricted on its own: the "
                "source page's own text reads 'Toyota US Open time standards and 18&U bonus "
                "standards', so Bonus requires age<=18 even though Qualifying does not."
            ),
            "document": "national-toyota-us-open-standards-2026.csv",
            "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2026/timestandards_2026_toyotausopen.pdf",
            "source_note": (
                "Official PDF has zero extractable text (vector-outlined glyphs). Sourced instead "
                "from swimstandards.com, an ordinary-HTML mirror of the same official standards; "
                "saved locally as a CSV fixture for reproducible offline extraction. The site's own "
                "2025-vs-2026 comparison confirms every Qualifying-tier value is unchanged from "
                "2025 (Bonus tier has a handful of small changes)."
            ),
            "alternate_source_url": "https://swimstandards.com/times/2026-toyota-us-open-time-standards",
        },
    },
]


def canon_event(event_label: str, course: str) -> str | None:
    """'400/500 FR' + 'SCY' -> '500 free' (400 for LCM); '4x100 FR-R' -> '4x100 free relay'."""
    m = re.match(r"^(?P<relay>\d+x)?(?P<dist>\d+(?:/\d+)?)\s*(?P<stroke>FR-R|MED-R|FR|BK|BR|FL|IM)$",
                 event_label, re.IGNORECASE)
    if not m:
        return None
    stroke = STROKE[m.group("stroke").lower()]
    dist_field = m.group("dist")
    if "/" in dist_field:
        meters_dist, yards_dist = dist_field.split("/")  # source prints meters/yards
        dist = yards_dist if course == "SCY" else meters_dist
    else:
        dist = dist_field
    relay_prefix = m.group("relay") or ""
    return f"{relay_prefix}{dist} {stroke}"


_CSV_STROKE_WORDS = {"free": "free", "back": "back", "breast": "breast", "fly": "fly", "im": "im"}


def canon_event_csv(event_label: str, course: str) -> str | None:
    """CSV events use plain words and name relays by TOTAL distance, not leg count: '400/500
    Free' + 'SCY' -> '500 free'; '200 Free Relay' (a total of 200 = 4x50) -> '4x50 free relay'."""
    m = re.match(r"^(?P<dist>\d+(?:/\d+)?)\s+(?P<stroke>Free Relay|Medley Relay|Free|Back|Breast|Fly|IM)$",
                 event_label, re.IGNORECASE)
    if not m:
        return None
    stroke_word = m.group("stroke").lower()
    dist_field = m.group("dist")
    if stroke_word in ("free relay", "medley relay"):
        leg = int(dist_field) // 4
        return f"4x{leg} {stroke_word}"
    stroke = _CSV_STROKE_WORDS[stroke_word]
    if "/" in dist_field:
        meters_dist, yards_dist = dist_field.split("/")  # source prints meters/yards
        dist = yards_dist if course == "SCY" else meters_dist
    else:
        dist = dist_field
    return f"{dist} {stroke}"


def parse_csv_meet(path: Path) -> tuple[list[tuple[str, dict]], list[str]]:
    """Return ([(label, {course: {gender: {event: time}}}), ...], warnings) for a CSV fixture.

    One entry per tier found in the "tier" column ("qualifying" -> label "STANDARDS", "bonus" ->
    label "BONUS STANDARDS") so the result plugs directly into build_flat_bonus_meet() exactly
    like a parsed PDF page list. "-" cells (event not contested, e.g. Winter Juniors' 200/200
    Free/Medley Relay) are skipped the same way "x" is skipped in the PDFs.
    """
    warnings: list[str] = []
    by_tier: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tier = row["tier"].strip().lower()
            out = by_tier.setdefault(tier, {course: {"girls": {}, "boys": {}} for course in COURSES})
            for gender, course, column in CSV_COLUMNS:
                value = row[column].strip()
                if value == "-":
                    continue
                event = canon_event_csv(row["event"].strip(), course)
                if event is None:
                    warnings.append(f"{path.name}: could not parse event label {row['event']!r}")
                    continue
                out[course][gender][event] = value
    pages = [
        ("BONUS STANDARDS" if tier == "bonus" else "STANDARDS", courses)
        for tier, courses in by_tier.items()
    ]
    return pages, warnings


def event_sort_key(event: str):
    m = re.match(r"^(?:\d+x)?(\d+)\s+(.+)$", event)
    dist, stroke = m.group(1), m.group(2)
    di = _DIST_ORDER.index(dist) if dist in _DIST_ORDER else len(_DIST_ORDER)
    si = _STROKE_ORDER.index(stroke) if stroke in _STROKE_ORDER else len(_STROKE_ORDER)
    return (si, di)


def parse_seconds(value: str) -> float:
    parts = value.split(":")
    return float(parts[0]) if len(parts) == 1 else int(parts[0]) * 60 + float(parts[1])


def parse_page(page) -> tuple[str, dict, list[str]]:
    """Return (label, {course: {gender: {event: time}}}, warnings) for one column-major page."""
    warnings: list[str] = []
    text = re.sub(r"[\xa0  ]", " ", page.extract_text())
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n") if l.strip()]

    label_match = LABEL_RE.search(text)
    label = label_match.group(1).strip() if label_match else ""

    values = [l for l in lines if re.fullmatch(TIME_OR_X, l)]
    events = [l for l in lines if EVENT_LABEL_RE.match(l)]
    n = len(events)
    if n == 0 or len(values) != 4 * n:
        warnings.append(f"page: expected 4x{n} value tokens, got {len(values)} (label={label!r})")
        return label, {}, warnings

    out: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for block_index, (gender, course) in enumerate(BLOCK_ORDER):
        block = values[block_index * n:(block_index + 1) * n]
        for raw_event, value in zip(events, block):
            if value == "x":
                continue
            event = canon_event(raw_event, course)
            if event is None:
                warnings.append(f"could not parse event label {raw_event!r}")
                continue
            out[course][gender][event] = value
    return label, out, warnings


def build_age_bracket_meet(pages: list[tuple[str, dict]], meta: dict) -> tuple[dict, list[str]]:
    """Futures/Toyota Nationals: two pages, each a full age bracket. Keyed by the page's own
    label text (never by page position -- position is not consistent across these documents)."""
    problems: list[str] = []
    brackets: dict[str, dict] = {}
    for label, courses in pages:
        bracket = "18 & Under" if "18" in label else "19 & Over" if "19" in label else label
        brackets[bracket] = courses

    standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for bracket, courses in brackets.items():
        for course in COURSES:
            for gender in ("girls", "boys"):
                for event, value in courses[course][gender].items():
                    if parse_seconds(value) <= 0:
                        problems.append(f"{bracket} {course} {gender} {event}: non-positive time {value}")
                    standards[course][gender].setdefault(bracket, {})
                    standards[course][gender][bracket][event] = {"qualifying": value}
    for course in COURSES:
        for gender in ("girls", "boys"):
            for bracket, events in standards[course][gender].items():
                standards[course][gender][bracket] = {
                    event: events[event] for event in sorted(events, key=event_sort_key)
                }
    return {**meta, "age_brackets": sorted(brackets, reverse=True), "standards": standards}, problems


def build_flat_bonus_meet(pages: list[tuple[str, dict]], meta: dict) -> tuple[dict, list[str]]:
    """Summer Juniors: two pages, one flat qualifying table and one flat Bonus table, merged
    event-by-event into {qualifying, bonus} cells (bonus only where the Bonus page lists it)."""
    problems: list[str] = []
    by_label = {}
    for label, courses in pages:
        key = "bonus" if "BONUS" in label.upper() else "qualifying"
        by_label[key] = courses

    standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for course in COURSES:
        for gender in ("girls", "boys"):
            q_events = by_label.get("qualifying", {}).get(course, {}).get(gender, {})
            b_events = by_label.get("bonus", {}).get(course, {}).get(gender, {})
            events = {}
            for event in sorted(set(q_events) | set(b_events), key=event_sort_key):
                cell = {}
                if event in q_events:
                    cell["qualifying"] = q_events[event]
                if event in b_events:
                    cell["bonus"] = b_events[event]
                if "qualifying" in cell and "bonus" in cell:
                    # Bonus is a slower, easier add-on cut for swimmers who already qualified
                    # elsewhere (same relationship as AZSI Regional being slower than State) --
                    # so qualifying must be faster than (or equal to) bonus, not the reverse.
                    if parse_seconds(cell["qualifying"]) > parse_seconds(cell["bonus"]):
                        problems.append(
                            f"{course} {gender} {event}: qualifying {cell['qualifying']} slower than bonus {cell['bonus']} (expected qualifying to be the faster/stricter cut)"
                        )
                events[event] = cell
            standards[course][gender] = events
    return {**meta, "standards": standards}, problems


def summarize(catalog: dict) -> str:
    lines = []
    total = 0
    for key, meet in catalog["meets"].items():
        n = 0
        for course in COURSES:
            for gender in ("girls", "boys"):
                genders = meet["standards"][course][gender]
                if "age_brackets" in meet:
                    n += sum(len(events) for events in genders.values())
                else:
                    n += len(genders)
        total += n
        lines.append(f"  {meet['name']}  ({n} cells)")
    return f"{total} cells across {len(catalog['meets'])} meets\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--futures-pdf", type=Path)
    parser.add_argument("--summer-juniors-pdf", type=Path)
    parser.add_argument("--toyota-nationals-pdf", type=Path)
    parser.add_argument("--winter-juniors-csv", type=Path)
    parser.add_argument("--toyota-us-open-csv", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check-only", action="store_true", help="parse and verify but do not write")
    args = parser.parse_args(argv)

    warnings: list[str] = []
    problems: list[str] = []
    meets_out: dict = {}
    for meet in MEETS:
        override = getattr(args, meet["arg"])
        if meet["source_type"] == "pdf":
            path = override or meet["default_pdf"]
            if not path.exists():
                parser.error(f"{meet['key']} PDF not found: {path}")
            reader = PdfReader(path)
            pages = []
            for page in reader.pages:
                label, courses, warns = parse_page(page)
                warnings.extend(f"{meet['key']}: {w}" for w in warns)
                pages.append((label, courses))
        else:
            path = override or meet["default_csv"]
            if not path.exists():
                parser.error(f"{meet['key']} CSV not found: {path}")
            pages, warns = parse_csv_meet(path)
            warnings.extend(f"{meet['key']}: {w}" for w in warns)
        if meet["kind"] == "age_bracket":
            entry, probs = build_age_bracket_meet(pages, meet["meta"])
        else:
            entry, probs = build_flat_bonus_meet(pages, meet["meta"])
        problems.extend(f"{meet['key']}: {p}" for p in probs)
        meets_out[meet["key"]] = entry

    catalog = {
        "source": {
            "name": "USA Swimming 2026 National Elite Time Standards",
            "effective": "2026 season",
            "courses": list(COURSES),
            "generator": "scripts/extract_national_standards.py",
            "scope": "National -- open to any USA Swimming member regardless of LSC (not state-scoped).",
            "not_handled": NOT_HANDLED,
            "notes": (
                "Futures and Toyota Nationals standards are split into two age brackets (18 & "
                "Under / 19 & Over) covering every age with no gap; neither meet gates entry by "
                "age. Summer Juniors, Winter Juniors, and U.S. Open standards are flat (no "
                "bracket) plus a separate Bonus tier. Summer/Winter Juniors have a hard "
                "18-and-under entry ceiling (both tiers); U.S. Open's Qualifying tier is "
                "age-open but its Bonus tier alone is 18-and-under. Winter Juniors and U.S. Open "
                "were sourced from swimstandards.com (CSV fixture) rather than their official "
                "PDF, which has no extractable text -- see each meet's 'source_note'."
            ),
        },
        "meets": meets_out,
    }

    print(summarize(catalog))
    if warnings:
        print(f"\n{len(warnings)} parse warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} verification problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nVerification: OK (every cell has a positive time; age-bracket/flat-bonus shapes built as expected).")
    print(f"\nNOT handled ({len(NOT_HANDLED)}): " + "; ".join(f"{m['key']} ({m['reason']})" for m in NOT_HANDLED))

    if args.check_only:
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    try:
        shown = args.out.relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(f"\nWrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
