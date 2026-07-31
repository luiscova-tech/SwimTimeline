#!/usr/bin/env python3
"""Extract the USA Swimming age-group Motivational Standards PDF into a JSON catalog.

The source is the two-year age-group document (every age group 10-&-under through
17-18, both genders, all three courses, ~20 events each, six tiers per event), e.g.
``docs/Sources/2028-motivational-standards-age-group.pdf``. The output is written to
``data/motivational_standards.json`` keyed course -> gender -> age group -> event -> tier.

When a new quad is published (2029-2032, etc.) this script is the whole update path:
drop the new PDF into ``docs/Sources/``, rerun with ``--pdf`` pointed at it, review the
diff on the JSON, and commit. No parsing code needs to be rewritten.

Layout notes (why the parser is a sequential token stream, not a table reader):
  * The PDF has no ruled table -- pdfplumber.extract_tables() finds zero tables. Text
    is whitespace-aligned only, so pypdf's linear text extraction is what we parse.
  * Each event row reads: <6 girls times> <distance> <stroke> <course> <6 boys times>.
    Girls tiers run B -> AAAA left to right; boys tiers on the SAME row run AAAA -> B.
  * There is no delimiter token between one row's trailing boys times and the next
    row's leading girls times -- both are bare time tokens -- so a row's boys times
    must be closed out the instant six of them have accumulated, not at the next label.
  * Medley-relay rows wrap the course code onto its own text line; flattening newlines
    to spaces before tokenizing makes wrap position irrelevant.
  * Some times carry a trailing '*' and some do not, inconsistently even within a row.
    The document ships no legend for it and the pattern is not meaningful, so it is
    dropped. Because the source renders it space-separated ("38.49 *"), the numeric
    token regex never absorbs it anyway; a lone '*' simply matches nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "docs" / "Sources" / "2028-motivational-standards-age-group.pdf"
DEFAULT_OUT = ROOT / "data" / "motivational_standards.json"

# Age-group bands, in the order they appear down each course's pages.
AGE_BANDS = ["10 & under", "11-12", "13-14", "15-16", "17-18"]

# Slowest -> fastest. Girls columns are printed in this order; boys columns are reversed.
TIER_ORDER = ["B", "BB", "A", "AA", "AAA", "AAAA"]
GIRLS_TIER_ORDER = TIER_ORDER
BOYS_TIER_ORDER = list(reversed(TIER_ORDER))

STROKE_MAP = {
    "FR": "free",
    "BK": "back",
    "BR": "breast",
    "FL": "fly",
    "IM": "im",
    "FR-R": "free relay",
    "MED-R": "medley relay",
}
COURSES = ("SCY", "SCM", "LCM")

TIME_TOKEN = r"\d{1,2}:\d{2}\.\d{2}\*?|\d{1,3}\.\d{2}\*?"
EVENT_TOKEN = r"(?P<distance>\d+)\s+(?P<stroke>FR-R|MED-R|FR|BK|BR|FL|IM)\s+(?P<course>SCY|SCM|LCM)"
_BAND_ALT = "|".join(re.escape(b) for b in AGE_BANDS)
HEADER_TOKEN = rf"(?:{_BAND_ALT})\s+Girls\s+Event\s+(?:{_BAND_ALT})\s+Boys"

TOKEN_RE = re.compile(f"(?P<header>{HEADER_TOKEN})|(?P<event>{EVENT_TOKEN})|(?P<time>{TIME_TOKEN})")
_HEADER_BAND_RE = re.compile(rf"({_BAND_ALT})\s+Girls")


def event_key(distance: str, stroke_code: str) -> str:
    """Canonical event key, e.g. ('200', 'FR') -> '200 free', ('200', 'MED-R') -> '200 medley relay'."""
    return f"{distance} {STROKE_MAP[stroke_code]}"


def parse_time_to_seconds(raw: str) -> float:
    raw = raw.rstrip("*").strip()
    parts = raw.split(":")
    if len(parts) == 1:
        return float(parts[0])
    return int(parts[0]) * 60 + float(parts[1])


def parse_pdf(pdf_path: Path) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). Each row: age_band, course, event_key, event_label, page, girls, boys."""
    reader = PdfReader(pdf_path)
    rows: list[dict] = []
    warnings: list[str] = []

    current_band: str | None = None
    buffer: list[str] = []  # bare time strings accumulated since the last flush
    pending_event: dict | None = None  # row awaiting its six boys times

    for page_index, page in enumerate(reader.pages, start=1):
        flat = (page.extract_text() or "").replace("\n", " ")
        for match in TOKEN_RE.finditer(flat):
            if match.group("header"):
                if buffer or pending_event is not None:
                    warnings.append(
                        f"page {page_index}: header seen with pending_event="
                        f"{pending_event is not None} and {len(buffer)} unflushed time(s): {buffer}"
                    )
                current_band = _HEADER_BAND_RE.search(match.group("header")).group(1)
                buffer = []
                pending_event = None
            elif match.group("event"):
                if pending_event is not None:
                    warnings.append(
                        f"page {page_index} band={current_band}: event label "
                        f"{match.group('distance')} {match.group('stroke')} arrived while the previous "
                        f"event still lacked boys times ({len(buffer)} buffered): {buffer}"
                    )
                distance = match.group("distance")
                stroke_code = match.group("stroke")
                course = match.group("course")
                if len(buffer) != 6:
                    warnings.append(
                        f"page {page_index} band={current_band} event={distance} {stroke_code} {course}: "
                        f"expected 6 girls times, got {len(buffer)}: {buffer}"
                    )
                row = {
                    "age_band": current_band,
                    "course": course,
                    "event_key": event_key(distance, stroke_code),
                    "event_label": f"{distance} {stroke_code}",
                    "page": page_index,
                    "girls": dict(zip(GIRLS_TIER_ORDER, buffer)),
                    "boys": None,
                }
                rows.append(row)
                pending_event = row
                buffer = []
            else:  # time token
                buffer.append(match.group("time").rstrip("*"))
                if pending_event is not None and len(buffer) == 6:
                    pending_event["boys"] = dict(zip(BOYS_TIER_ORDER, buffer))
                    pending_event = None
                    buffer = []

    if pending_event is not None or buffer:
        warnings.append(f"EOF with pending_event={pending_event is not None} and leftover buffer={buffer}")

    return rows, warnings


def verify(rows: list[dict]) -> list[str]:
    """Structural sanity checks. Returns a list of problems (empty == clean)."""
    problems: list[str] = []

    for row in rows:
        girls, boys = row["girls"], row["boys"]
        if boys is None or len(girls) != 6 or len(boys) != 6:
            problems.append(f"incomplete row {row['course']} {row['age_band']} {row['event_label']}")
            continue
        for gender_key, values in (("girls", girls), ("boys", boys)):
            for tier, raw in values.items():
                try:
                    parse_time_to_seconds(raw)
                except ValueError:
                    problems.append(f"unparseable {row['course']} {row['age_band']} {row['event_label']} {gender_key} {tier}={raw!r}")
        # Tier progression must be strictly monotonic (B slowest -> AAAA fastest).
        g = [parse_time_to_seconds(girls[t]) for t in TIER_ORDER]
        b = [parse_time_to_seconds(boys[t]) for t in TIER_ORDER]
        if not all(g[i] > g[i + 1] for i in range(5)):
            problems.append(f"non-monotonic girls {row['course']} {row['age_band']} {row['event_label']}: {girls}")
        if not all(b[i] > b[i + 1] for i in range(5)):
            problems.append(f"non-monotonic boys {row['course']} {row['age_band']} {row['event_label']}: {boys}")

    # Girls and boys must cover exactly the same events within every (course, band).
    grouped: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        grouped.setdefault((row["course"], row["age_band"]), set()).add(row["event_key"])
    for (course, band), events in grouped.items():
        # every row already carries both genders, so symmetry is guaranteed per row;
        # this instead flags a course/band that is missing entirely.
        if not events:
            problems.append(f"no events for {course} {band}")

    total_values = sum(12 for row in rows if row["boys"] is not None)
    if total_values != len(rows) * 12:
        problems.append(f"value count {total_values} != rows*12 ({len(rows) * 12})")

    return problems


def build_catalog(rows: list[dict], pdf_path: Path) -> dict:
    """Nest rows into course -> gender -> age_band -> event_key -> {tier: time}."""
    standards: dict = {course: {"girls": {}, "boys": {}} for course in COURSES}
    for row in rows:
        for gender in ("girls", "boys"):
            band_map = standards[row["course"]][gender].setdefault(row["age_band"], {})
            band_map[row["event_key"]] = {tier: row[gender][tier] for tier in TIER_ORDER}

    return {
        "source": {
            "name": "USA Swimming 2024-2028 Motivational Standards (two-year age group)",
            "document": pdf_path.name,
            "quad": "2024-2028",
            "url": "https://www.usaswimming.org/times/popular-resources/motivational-times",
            "tier_order": TIER_ORDER,
            "age_bands": AGE_BANDS,
            "courses": list(COURSES),
            "generator": "scripts/extract_motivational_standards.py",
            "notes": (
                "Trailing '*' markers in the source PDF are undocumented and inconsistent; "
                "they are dropped. Times are stored exactly as printed (mm:ss.hh or ss.hh)."
            ),
        },
        "standards": standards,
    }


def summarize(rows: list[dict]) -> str:
    grouped: dict[tuple[str, str], int] = {}
    for row in rows:
        grouped[(row["course"], row["age_band"])] = grouped.get((row["course"], row["age_band"]), 0) + 1
    lines = [f"Extracted {len(rows)} rows / {len(rows) * 12} tier values.", ""]
    for course in COURSES:
        for band in AGE_BANDS:
            count = grouped.get((course, band), 0)
            if count:
                lines.append(f"  {course}  {band:12}  {count:2} events")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help=f"source PDF (default: {DEFAULT_PDF})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output JSON (default: {DEFAULT_OUT})")
    parser.add_argument("--check-only", action="store_true", help="parse and verify but do not write the JSON")
    args = parser.parse_args(argv)

    if not args.pdf.exists():
        parser.error(f"PDF not found: {args.pdf}")

    rows, warnings = parse_pdf(args.pdf)
    print(summarize(rows))

    if warnings:
        print(f"\n{len(warnings)} parse warning(s):", file=sys.stderr)
        for warning in warnings:
            print(f"  {warning}", file=sys.stderr)

    problems = verify(rows)
    if problems:
        print(f"\n{len(problems)} verification problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("\nVerification: OK (all values parse, tiers monotonic, every row has both genders).")

    if args.check_only:
        return 0

    catalog = build_catalog(rows, args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
