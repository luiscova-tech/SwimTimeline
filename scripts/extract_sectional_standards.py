#!/usr/bin/env python3
"""Extract the USA Swimming Speedo Sectional Qualifying Time Standards into a JSON catalog.

Two Western-Region Speedo Sectional meets are relevant to Arizona swimmers, and they are kept
as SEPARATE, individually-labeled datasets -- never merged into a generic "Sectionals" -- so the
app can present them as distinct options:

  four_corners_spring     Four Corners Spring Speedo Sectional (Carmel, IN; late March)
  western_region_summer   Western Region Summer Speedo Sectional (Boise, ID; mid-July)

Structure (both documents, and unlike the AZSI sheets): a single combined table with the columns
"Women SCY SCM LCM | EVENTS | Men SCY SCM LCM" -- so each row carries six time cells, three women
then three men, in SCY/SCM/LCM order (AZSI prints SCY/LCM/SCM instead). There are NO age bands
(these are open/senior-level cuts, one qualifying time per event/course/gender) and NO bonus
standard alongside the qualifying one (only a single cut per cell). Relay events are included.
The two meets publish the SAME individual-event cutoffs for the 2026 season; they differ in that
the Summer meet also lists the 50s of Back/Breast/Fly (the Spring meet omits them) and in their
dates, venue, and qualifying period. They are still two distinct meets and are stored as such.

Output goes to ``data/sectional_standards.json`` shaped:
  {"source": {...}, "meets": {<key>: {<metadata>, "standards": course -> gender -> event ->
  {"qualifying": time}}}}
mirroring the established AZSI/motivational JSON pattern (a "standards" catalog per course/gender)
but with a per-meet wrapper and a single "qualifying" cut in place of the {state, regional} pair.

Combined-distance free rows print "meters/yards" ("400/500 Free" -> 400 for LC/SC meters, 500 for
SC yards). "Ind. Medley" maps to the "im" stroke; "Free Relay"/"Medley Relay" to relay events.

Update path when USA Swimming republishes: drop the new PDFs into ``docs/Sources/``, rerun
(optionally pass ``--four-corners-pdf`` / ``--western-region-pdf``), review the JSON diff, commit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "sectional_standards.json"

# Catalog course order, matching data/azsi_standards.json / motivational_standards.json so the
# datasets read the same side by side. For these Sectional sheets it also happens to be the
# physical column order (Women SCY/SCM/LCM, then Men SCY/SCM/LCM).
COURSES = ["SCY", "SCM", "LCM"]

# Per-meet configuration: the source PDF, the copy kept in docs/Sources/, and the metadata that
# distinguishes the two meets. Kept explicit (not scraped from the sheet) so each meet stays
# clearly and correctly labeled.
MEETS = [
    {
        "key": "four_corners_spring",
        "default_pdf": ROOT / "docs" / "Sources" / "sectional-four-corners-spring-standards-2026.pdf",
        "arg": "four_corners_pdf",
        "meta": {
            "name": "Four Corners Spring Speedo Sectional",
            "series": "2026 USA Swimming Speedo Championship Series",
            "location": "Carmel Aquatic Center, Carmel, IN",
            "dates": "2026-03-26 through 2026-03-29",
            "qualifying_period": "2024-12-01 through close of entries",
            "document": "sectional-four-corners-spring-standards-2026.pdf",
            "url": "https://www.gomotionapp.com/azseals/__doc__/211074_4_2026-speedo-sectionals-four-corners-final.pdf",
        },
    },
    {
        "key": "western_region_summer",
        "default_pdf": ROOT / "docs" / "Sources" / "sectional-western-region-summer-standards-2026.pdf",
        "arg": "western_region_pdf",
        "meta": {
            "name": "Western Region Summer Speedo Sectional",
            "series": "2026 USA Swimming Speedo Championship Series",
            "location": "Idaho Central Aquatic Center, Boise, ID",
            "dates": "2026-07-16 through 2026-07-19",
            "qualifying_period": "2025-06-01 through close of entries",
            "document": "sectional-western-region-summer-standards-2026.pdf",
            "url": "https://www.gomotionapp.com/wzone/UserFiles/Image/QuickUpload/2026-speedo-sectionals---vtime-standards_099437.pdf",
        },
    },
]

TIME = r"\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}"
# A data row: three women times, the event label (possibly multi-word, e.g. "200 Ind. Medley"),
# then three men times. The event label is captured non-greedily between the two time triples.
ROW_RE = re.compile(
    rf"^({TIME})\s+({TIME})\s+({TIME})\s+(.+?)\s+({TIME})\s+({TIME})\s+({TIME})$"
)

_DIST_ORDER = ["50", "100", "200", "400", "500", "800", "1000", "1500", "1650"]
_STROKE_ORDER = ["free", "back", "breast", "fly", "im", "free relay", "medley relay"]


def stroke_key(phrase: str) -> str | None:
    """Map an event's stroke words to a canonical stroke key. Relays are checked first so
    "Free Relay"/"Medley Relay" are not shortened to "free"/"im"; "Ind. Medley" -> "im"."""
    p = phrase.lower()
    if "free relay" in p:
        return "free relay"
    if "medley relay" in p:
        return "medley relay"
    if "free" in p:
        return "free"
    if "back" in p:
        return "back"
    if "breast" in p:
        return "breast"
    if "fly" in p:
        return "fly"
    if "medley" in p or re.search(r"\bim\b", p):
        return "im"
    return None


def canon_event(event_field: str, course: str) -> str | None:
    """'400/500 Free' + 'SCY' -> '500 free' (400 for meters courses); '200 Ind. Medley' -> '200 im'."""
    parts = event_field.split(None, 1)
    if len(parts) != 2:
        return None
    dist_field, stroke_phrase = parts
    stroke = stroke_key(stroke_phrase)
    if stroke is None:
        return None
    if "/" in dist_field:
        meters_dist, yards_dist = dist_field.split("/")  # source prints meters/yards
        dist = yards_dist if course == "SCY" else meters_dist
    else:
        dist = dist_field
    return f"{dist} {stroke}"


def event_sort_key(event: str):
    dist, stroke = event.split(" ", 1)
    di = _DIST_ORDER.index(dist) if dist in _DIST_ORDER else len(_DIST_ORDER)
    si = _STROKE_ORDER.index(stroke) if stroke in _STROKE_ORDER else len(_STROKE_ORDER)
    return (si, di)


def parse_seconds(value: str) -> float:
    parts = value.split(":")
    return float(parts[0]) if len(parts) == 1 else int(parts[0]) * 60 + float(parts[1])


def parse_doc(path: Path) -> tuple[dict, list[str]]:
    """Return ({course: {gender: {event: time}}}, warnings) for one Sectional PDF.

    Each data row supplies a women cut and a men cut for the same event across three courses, so
    a single pass fills both genders. The gender split is columnar (women left, men right) -- no
    speed heuristic needed, unlike the AZSI Senior sheet.
    """
    reader = PdfReader(path)
    warnings: list[str] = []
    out: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    seen_rows = 0
    for page in reader.pages:
        text = re.sub(r"[\xa0    ]", " ", page.extract_text())
        for raw in text.split("\n"):
            line = re.sub(r"\s+", " ", raw).strip()
            m = ROW_RE.match(line)
            if not m:
                continue
            seen_rows += 1
            women = (m.group(1), m.group(2), m.group(3))
            men = (m.group(5), m.group(6), m.group(7))
            for course, w_val, m_val in zip(COURSES, women, men):
                event = canon_event(m.group(4), course)
                if event is None:
                    warnings.append(f"{path.name}: could not parse event {m.group(4)!r}")
                    continue
                out[course]["girls"][event] = w_val
                out[course]["boys"][event] = m_val
    if seen_rows == 0:
        warnings.append(f"{path.name}: no data rows matched")
    return out, warnings


def build_catalog(parsed: dict[str, dict], out_path: Path) -> tuple[dict, list[str]]:
    """Assemble the per-meet catalog, keyed meets -> meet -> course -> gender -> event ->
    {qualifying}. ``parsed`` maps meet key -> {course: {gender: {event: time}}}."""
    problems: list[str] = []
    meets_out: dict = {}
    for meet in MEETS:
        key = meet["key"]
        courses = parsed[key]
        standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
        for course in COURSES:
            for gender in ("girls", "boys"):
                events = courses[course][gender]
                for event in sorted(events, key=event_sort_key):
                    value = events[event]
                    if parse_seconds(value) <= 0:
                        problems.append(f"{key} {course} {gender} {event}: non-positive time {value}")
                    standards[course][gender][event] = {"qualifying": value}
        meets_out[key] = {**meet["meta"], "standards": standards}

    # Cross-meet sanity: the two meets must stay distinct. Report (not merge) where they differ,
    # and confirm the cutoffs they share are identical for this season.
    keys = [m["key"] for m in MEETS]
    a, b = meets_out[keys[0]]["standards"], meets_out[keys[1]]["standards"]
    shared_mismatch = 0
    for course in COURSES:
        for gender in ("girls", "boys"):
            shared = set(a[course][gender]) & set(b[course][gender])
            for event in shared:
                if a[course][gender][event] != b[course][gender][event]:
                    shared_mismatch += 1

    catalog = {
        "source": {
            "name": "USA Swimming 2026 Speedo Championship Series -- Western Region Sectionals",
            "program": "USA Swimming Speedo Championship Series (Sectionals)",
            "effective": "2026 season",
            "courses": list(COURSES),
            "tiers": ["qualifying"],
            "generator": "scripts/extract_sectional_standards.py",
            "notes": (
                "Two distinct Western-Region Sectional meets kept separately identifiable (never "
                "merged): four_corners_spring and western_region_summer. No age bands (open/senior "
                "cuts, one qualifying time per event/course/gender); no bonus standard. Relay "
                "events included. For the 2026 season the two meets share identical individual "
                "cutoffs; the Summer meet additionally lists the 50s of Back/Breast/Fly. Both meet "
                "flyers are age-open (eligibility is purely by qualifying time, no age floor or "
                "ceiling); standards.py applies these to Arizona swimmers of any age."
            ),
        },
        "meets": meets_out,
    }
    return catalog, problems, shared_mismatch


def summarize(catalog: dict) -> str:
    lines = []
    total = 0
    for key, meet in catalog["meets"].items():
        per_meet = 0
        parts = []
        for course in COURSES:
            for gender in ("girls", "boys"):
                n = len(meet["standards"][course][gender])
                per_meet += n
                parts.append(f"{course} {gender[:1]}:{n}")
        total += per_meet
        lines.append(f"  {meet['name']}  ({per_meet} cells)  " + " ".join(parts))
    return f"{total} (meet, course, gender, event) cells across {len(catalog['meets'])} meets\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--four-corners-pdf", type=Path, help="override the Four Corners Spring PDF")
    parser.add_argument("--western-region-pdf", type=Path, help="override the Western Region Summer PDF")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check-only", action="store_true", help="parse and verify but do not write")
    args = parser.parse_args(argv)

    parsed: dict[str, dict] = {}
    warnings: list[str] = []
    for meet in MEETS:
        override = getattr(args, meet["arg"])
        path = override or meet["default_pdf"]
        if not path.exists():
            parser.error(f"{meet['key']} PDF not found: {path}")
        courses, warns = parse_doc(path)
        parsed[meet["key"]] = courses
        warnings.extend(warns)

    catalog, problems, shared_mismatch = build_catalog(parsed, args.out)

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
    print(
        f"\nVerification: OK (every cell has a positive qualifying time; the two meets are stored "
        f"separately; {shared_mismatch} mismatch(es) among shared cutoffs)."
    )

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
