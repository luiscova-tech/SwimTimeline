#!/usr/bin/env python3
"""Extract the AZSI (Arizona Swimming) Qualifying Time Standards into a JSON catalog.

This script has two modes, selected with ``--mode``:

``--mode age-group`` (default) handles the Age Group documents. Arizona publishes two separate
Age Group sheets, both effective Sep 1 2025 - Aug 31 2026: a State Qualifying and a Regional
Qualifying sheet. Each covers all three courses (Short Course Yards, Long Course Meters, Short
Course Meters), both genders, and the three age-group bands 10-&-under / 11-12 / 13-14. Output
goes to ``data/azsi_standards.json`` keyed course -> gender -> age group -> event ->
{state, regional}, mirroring ``data/motivational_standards.json``.

  Layout (per Age Group document): page 1 = WOMEN, page 2 = MEN. Each page stacks three
  age-band blocks, each introduced by a "Short Course Yards / Long Course Meters / Short Course
  Meters" header followed by event rows "<event> <SCY> <LCM> <SCM>". Bands are identified by
  their event set, not position, since the "WOMEN 11-12"/etc. labels are positioned graphics
  that do not extract in reading order.

``--mode senior`` handles the Senior documents. These have NO age bands: the Senior standard is
one cut per event/course/gender, applied to any qualifying swimmer regardless of age (the meet
flyer's eligibility is "open to any registered athlete who has met the Senior Qualifying Time",
with no age floor or ceiling stated). For this app -- whose benchmark scope is 18-and-under --
the Senior cuts complement the Age Group bands (which top out at 13-14): see standards.py, which
applies them to ages 15-18. Output goes to ``data/azsi_senior_standards.json`` keyed
course -> gender -> event -> {state, regional} (no band level).

  Layout (per Senior document): both gender blocks sit on page 1 (WOMEN then MEN in reading
  order), each a single "Short Course Yards / Long Course Meters / Short Course Meters" header
  plus event rows. The "WOMEN SENIOR"/"MEN SENIOR" labels are positioned graphics that extract
  out of order, so gender is assigned by a speed vote instead: men's cuts are faster than
  women's in the overwhelming majority of cells (one known source quirk -- SCM 800 Free on the
  State sheet -- has the men's cut slightly slower, so a majority vote is used, not a universal
  rule). The faster block is MEN.

Shared to both modes: combined-distance free rows print "meters/yards" ("400/500 Free" -> 400
for LC/SC meters, 500 for SC yards). "x" marks an event not contested in a course. The State
sheets use non-breaking / narrow spaces; they are normalized to plain spaces.

Update path when Arizona republishes: drop the new PDFs into ``docs/Sources/``, rerun the
relevant ``--mode`` (optionally with ``--state-pdf`` / ``--regional-pdf``), review the JSON
diff, and commit. No parser changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "docs" / "Sources" / "azsi-age-group-state-qualifying-time-standards-2025-2026.pdf"
DEFAULT_REGIONAL = ROOT / "docs" / "Sources" / "azsi-age-group-regional-qualifying-time-standards-2025-2026.pdf"
DEFAULT_OUT = ROOT / "data" / "azsi_standards.json"
DEFAULT_SENIOR_STATE = ROOT / "docs" / "Sources" / "azsi-senior-state-qualifying-time-standards-2025-2026.pdf"
DEFAULT_SENIOR_REGIONAL = ROOT / "docs" / "Sources" / "azsi-senior-regional-qualifying-time-standards-2025-2026.pdf"
DEFAULT_SENIOR_OUT = ROOT / "data" / "azsi_senior_standards.json"

# Output/catalog course order -- matches data/motivational_standards.json so the two datasets
# read the same way side by side. NOTE: this is NOT the PDF's physical column order.
COURSES = ["SCY", "SCM", "LCM"]
# The PDF prints columns as Short Course Yards / Long Course Meters / Short Course Meters, so
# the three time cells in each row map to this order -- used only when reading rows.
PDF_COLUMN_ORDER = ["SCY", "LCM", "SCM"]
AGE_BANDS = ["10 & under", "11-12", "13-14"]
STROKE = {"Free": "free", "Back": "back", "Breast": "breast", "Fly": "fly", "IM": "im"}
TIME = r"\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}|x"
ROW_RE = re.compile(r"^([\d/]+)\s+(Free|Back|Breast|Fly|IM)\s+(.*)$")


def canon_event(dist_field: str, stroke_word: str, course: str) -> str:
    """'400/500' + 'Free' + 'LCM' -> '400 free' (500 for SCY); '50' + 'Back' -> '50 back'."""
    if "/" in dist_field:
        meters_dist, yards_dist = dist_field.split("/")  # source prints meters/yards
        dist = yards_dist if course == "SCY" else meters_dist
    else:
        dist = dist_field
    return f"{dist} {STROKE[stroke_word]}"


def identify_band(events: set[str]) -> str | None:
    has_distance = any(ev.startswith(("800", "1000", "1500", "1650")) for ev in events)
    has_400im = "400 im" in events
    has_100im = "100 im" in events
    if not has_distance and not has_400im:
        return "10 & under"
    if has_100im and has_400im:
        return "11-12"
    if has_400im and not has_100im:
        return "13-14"
    return None


def parse_doc(path: Path) -> tuple[dict, list[str]]:
    """Return ({gender: {band: {event: {course: time}}}}, warnings) for one AZSI PDF."""
    reader = PdfReader(path)
    out: dict = {}
    warnings: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = re.sub(r"[\xa0    ]", " ", page.extract_text())
        gender = "girls" if "WOMEN" in text.upper() else "boys" if "MEN" in text.upper() else None
        if gender is None:
            warnings.append(f"page {page_index}: no WOMEN/MEN label found")
            continue
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        blocks: list[list[str]] = []
        current: list[str] | None = None
        for line in lines:
            if line.startswith("Short") and "Course Yards" in line:
                current = []
                blocks.append(current)
            elif current is not None and ROW_RE.match(line):
                current.append(line)
        for block in blocks:
            events: dict[str, dict[str, str]] = {}
            for row in block:
                m = ROW_RE.match(row)
                times = re.findall(TIME, m.group(3))
                if len(times) != 3:
                    warnings.append(f"page {page_index} {gender} row {row!r}: expected 3 times, got {len(times)}")
                    continue
                for course, value in zip(PDF_COLUMN_ORDER, times):
                    if value == "x":
                        continue
                    key = canon_event(m.group(1), m.group(2), course)
                    events.setdefault(key, {})[course] = value
            band = identify_band(set(events))
            if band is None:
                warnings.append(f"page {page_index} {gender}: could not identify band from events {sorted(events)[:4]}")
                continue
            out.setdefault(gender, {})[band] = events
    return out, warnings


def build_catalog(state: dict, regional: dict, state_path: Path, regional_path: Path) -> tuple[dict, list[str]]:
    """Merge the two documents into course -> gender -> band -> event -> {state, regional}."""
    problems: list[str] = []
    standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for gender in ("girls", "boys"):
        for band in AGE_BANDS:
            s_events = state.get(gender, {}).get(band, {})
            r_events = regional.get(gender, {}).get(band, {})
            for event in sorted(set(s_events) | set(r_events), key=event_sort_key):
                for course in COURSES:
                    s_val = s_events.get(event, {}).get(course)
                    r_val = r_events.get(event, {}).get(course)
                    if s_val is None and r_val is None:
                        continue
                    cell = {}
                    if s_val is not None:
                        cell["state"] = s_val
                    if r_val is not None:
                        cell["regional"] = r_val
                    if "state" in cell and "regional" in cell:
                        if parse_seconds(cell["state"]) > parse_seconds(cell["regional"]):
                            problems.append(
                                f"{course} {gender} {band} {event}: state {cell['state']} slower than regional {cell['regional']}"
                            )
                    else:
                        problems.append(f"{course} {gender} {band} {event}: missing {'regional' if 'state' in cell else 'state'} cut")
                    standards[course][gender].setdefault(band, {})[event] = cell

    catalog = {
        "source": {
            "name": "AZSI 2025-2026 Age Group State and Regional Qualifying Time Standards",
            "documents": {"state": state_path.name, "regional": regional_path.name},
            "effective": "2025-09-01 through 2026-08-31",
            "url": "https://www.azswimming.org/page/calendar/time-standards",
            "courses": list(COURSES),
            "age_bands": list(AGE_BANDS),
            "tiers": ["state", "regional"],
            "generator": "scripts/extract_azsi_standards.py",
            "notes": (
                "State cuts are faster than Regional in every event; a swimmer who meets the "
                "State cut is no longer eligible for Regionals in that event. 15-18 Senior "
                "standards are published separately and are not included here."
            ),
        },
        "standards": standards,
    }
    return catalog, problems


def _block_events(block: list[str], page_index: int, path: Path, warnings: list[str]) -> dict[str, dict[str, str]]:
    """Turn one Short-Course-header block of event rows into {event: {course: time}}."""
    events: dict[str, dict[str, str]] = {}
    for row in block:
        m = ROW_RE.match(row)
        times = re.findall(TIME, m.group(3))
        if len(times) != 3:
            warnings.append(f"{path.name} page {page_index} row {row!r}: expected 3 times, got {len(times)}")
            continue
        for course, value in zip(PDF_COLUMN_ORDER, times):
            if value == "x":
                continue
            key = canon_event(m.group(1), m.group(2), course)
            events.setdefault(key, {})[course] = value
    return events


def parse_senior_doc(path: Path) -> tuple[dict, list[str]]:
    """Return ({gender: {event: {course: time}}}, warnings) for one AZSI Senior PDF.

    Senior sheets have no age bands: exactly two Short-Course-header blocks (one per gender) on
    page 1. The WOMEN/MEN labels extract out of reading order, so gender is assigned by a speed
    vote -- the block whose cuts are faster in the majority of shared (event, course) cells is
    MEN. (A universal "men faster everywhere" rule is not used: the State sheet's SCM 800 Free
    women's cut is faster than the men's, a lone source quirk that a majority vote absorbs.)
    """
    reader = PdfReader(path)
    warnings: list[str] = []
    blocks: list[list[str]] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = re.sub(r"[\xa0    ]", " ", page.extract_text())
        current: list[str] | None = None
        for line in (l.strip() for l in text.split("\n") if l.strip()):
            if line.startswith("Short") and "Course Yards" in line:
                current = []
                blocks.append(current)
            elif current is not None and ROW_RE.match(line):
                current.append(line)
    if len(blocks) != 2:
        warnings.append(f"{path.name}: expected 2 gender blocks, found {len(blocks)}")
        return {}, warnings

    block_a = _block_events(blocks[0], 1, path, warnings)
    block_b = _block_events(blocks[1], 1, path, warnings)

    a_faster = b_faster = 0
    for event in set(block_a) & set(block_b):
        for course in PDF_COLUMN_ORDER:
            va, vb = block_a[event].get(course), block_b[event].get(course)
            if va is not None and vb is not None:
                sa, sb = parse_seconds(va), parse_seconds(vb)
                if sa < sb:
                    a_faster += 1
                elif sb < sa:
                    b_faster += 1
    total = a_faster + b_faster
    if total == 0 or a_faster == b_faster:
        warnings.append(f"{path.name}: could not distinguish genders by speed ({a_faster} vs {b_faster})")
        return {}, warnings
    if max(a_faster, b_faster) / total < 0.9:
        warnings.append(f"{path.name}: gender speed vote not decisive ({a_faster} vs {b_faster}); review assignment")
    boys, girls = (block_a, block_b) if a_faster > b_faster else (block_b, block_a)
    return {"girls": girls, "boys": boys}, warnings


def build_senior_catalog(state: dict, regional: dict, state_path: Path, regional_path: Path) -> tuple[dict, list[str]]:
    """Merge the two Senior documents into course -> gender -> event -> {state, regional}."""
    problems: list[str] = []
    standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for gender in ("girls", "boys"):
        s_events = state.get(gender, {})
        r_events = regional.get(gender, {})
        for event in sorted(set(s_events) | set(r_events), key=event_sort_key):
            for course in COURSES:
                s_val = s_events.get(event, {}).get(course)
                r_val = r_events.get(event, {}).get(course)
                if s_val is None and r_val is None:
                    continue
                cell = {}
                if s_val is not None:
                    cell["state"] = s_val
                if r_val is not None:
                    cell["regional"] = r_val
                if "state" in cell and "regional" in cell:
                    if parse_seconds(cell["state"]) > parse_seconds(cell["regional"]):
                        problems.append(
                            f"{course} {gender} senior {event}: state {cell['state']} slower than regional {cell['regional']}"
                        )
                else:
                    problems.append(f"{course} {gender} senior {event}: missing {'regional' if 'state' in cell else 'state'} cut")
                standards[course][gender][event] = cell

    catalog = {
        "source": {
            "name": "AZSI 2025-2026 Senior State and Regional Qualifying Time Standards",
            "documents": {"state": state_path.name, "regional": regional_path.name},
            "effective": "2025-09-01 through 2026-08-31",
            "url": "https://www.azswimming.org/page/calendar/time-standards",
            "courses": list(COURSES),
            "tiers": ["state", "regional"],
            "generator": "scripts/extract_azsi_standards.py --mode senior",
            "notes": (
                "Senior standards have no age bands: one cut per event/course/gender. The meet "
                "flyer's eligibility ('open to any registered athlete who has met the Senior "
                "Qualifying Time') states no age floor or ceiling; a qualifying time in one "
                "course qualifies in all courses. Within this app's 18-and-under benchmark "
                "scope they cover ages 15-18 (Age Group bands cover 14 and under). State cuts "
                "are faster than Regional in every event; meeting the State cut removes Regional "
                "eligibility for that event."
            ),
        },
        "standards": standards,
    }
    return catalog, problems


def summarize_senior(catalog: dict) -> str:
    lines = []
    for course in COURSES:
        for gender in ("girls", "boys"):
            events = catalog["standards"][course][gender]
            if events:
                lines.append(f"  {course}  {gender:5}  {len(events)} events")
    total = sum(
        len(events)
        for genders in catalog["standards"].values()
        for events in genders.values()
    )
    return f"{total} (course, gender, event) cells\n" + "\n".join(lines)


_DIST_ORDER = ["50", "100", "200", "400", "500", "800", "1000", "1500", "1650"]
_STROKE_ORDER = ["free", "back", "breast", "fly", "im"]


def event_sort_key(event: str):
    dist, stroke = event.split(" ", 1)
    di = _DIST_ORDER.index(dist) if dist in _DIST_ORDER else len(_DIST_ORDER)
    si = _STROKE_ORDER.index(stroke) if stroke in _STROKE_ORDER else len(_STROKE_ORDER)
    return (si, di)


def parse_seconds(value: str) -> float:
    parts = value.split(":")
    return float(parts[0]) if len(parts) == 1 else int(parts[0]) * 60 + float(parts[1])


def summarize(catalog: dict) -> str:
    lines = []
    for course in COURSES:
        for gender in ("girls", "boys"):
            for band in AGE_BANDS:
                events = catalog["standards"][course][gender].get(band, {})
                if events:
                    lines.append(f"  {course}  {gender:5}  {band:11}  {len(events)} events")
    total = sum(
        len(events)
        for genders in catalog["standards"].values()
        for bands in genders.values()
        for events in bands.values()
    )
    return f"{total} (course, gender, band, event) cells\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["age-group", "senior"], default="age-group",
                        help="which AZSI document set to extract (default: age-group)")
    parser.add_argument("--state-pdf", type=Path, help="override the default State PDF for the mode")
    parser.add_argument("--regional-pdf", type=Path, help="override the default Regional PDF for the mode")
    parser.add_argument("--out", type=Path, help="override the default output JSON for the mode")
    parser.add_argument("--check-only", action="store_true", help="parse and verify but do not write")
    args = parser.parse_args(argv)

    if args.mode == "senior":
        state_pdf = args.state_pdf or DEFAULT_SENIOR_STATE
        regional_pdf = args.regional_pdf or DEFAULT_SENIOR_REGIONAL
        out = args.out or DEFAULT_SENIOR_OUT
    else:
        state_pdf = args.state_pdf or DEFAULT_STATE
        regional_pdf = args.regional_pdf or DEFAULT_REGIONAL
        out = args.out or DEFAULT_OUT

    for label, path in (("state", state_pdf), ("regional", regional_pdf)):
        if not path.exists():
            parser.error(f"{label} PDF not found: {path}")

    if args.mode == "senior":
        state, state_warnings = parse_senior_doc(state_pdf)
        regional, regional_warnings = parse_senior_doc(regional_pdf)
        catalog, problems = build_senior_catalog(state, regional, state_pdf, regional_pdf)
        print(summarize_senior(catalog))
    else:
        state, state_warnings = parse_doc(state_pdf)
        regional, regional_warnings = parse_doc(regional_pdf)
        catalog, problems = build_catalog(state, regional, state_pdf, regional_pdf)
        print(summarize(catalog))
    warnings = state_warnings + regional_warnings
    if warnings:
        print(f"\n{len(warnings)} parse warning(s):", file=sys.stderr)
        for w in warnings:
            print(f"  {w}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} verification problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("\nVerification: OK (every cell has both state and regional; state faster than regional).")

    if args.check_only:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out  # --out may point outside the repo (e.g. a temp verification file)
    print(f"\nWrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
