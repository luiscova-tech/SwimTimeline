"""Warm-up window as the first line of the daily calendar.

Two independent AUTHORITATIVE-FIRST-LINE sources:
  * SIMPLE  -- one universal window per meet (a manually set field, or a flyer-stated range),
    shown the same on every day.
  * COMPLEX -- a per-meet warm-up-assignments PDF whose prelim windows vary by BOTH day-of-week and
    team/LSC (WZAG), resolved by the swimmer's own LSC x the specific day x session type; finals are
    universal. The complex doc wins when it resolves a window for the swimmer.

A meet with NEITHER shows no warm-up window first line at all (the existing per-session "Warm-up:"
line, flyer-derived or estimated, is unaffected).

Note on the task premise: the flyer parser already DOES extract per-session warm-up times for
AZ-style flyers, and the daily calendar already shows a "Warm-up:" line -- so this feature adds an
*authoritative window* first line on top of that, it does not fill a total absence.

A THIRD, separate source feeds that underlying per-session "Warm-up:" line itself (not the
authoritative first line above): a day-varying but swimmer-universal time stated once per calendar
day in the flyer's own event-order section (Herculean Invitational: "Session 1-- Friday, September
11, 2026" followed by "4:45 warm up/5:30 start"), as opposed to parse_flyer_sessions' per-SESSION-
NUMBER "Warm-up: ..., Meet Start: ..." line. See DayVaryingPerSessionWarmupTest.
"""

from datetime import date
from pathlib import Path
import re
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    build_warmup_resolver,
    extract_flyer_warmup_window,
    parse_flyer_day_warmups,
    parse_warmup_assignments,
    resolve_bare_clock_before_start,
    warmup_token_to_lsc,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
SHARK = ROOT / "meets/2026-shark-open/input"
NARWHAL = ROOT / "meets/2026-narwhal-invite/input"
HERCULEAN = ROOT / "meets/2026-herculean-invitational/input"
WARMUP_PDF = WZAG / "wzag warm-up assignments.pdf"


def daily_warmup_by_weekday(out_dir: Path) -> dict[str, str]:
    """Map each daily VEVENT's weekday (from its title) to its first description line."""
    ics = (out_dir / "daily.ics").read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
    result: dict[str, str] = {}
    for block in ics.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"SUMMARY:(.*)", block)
        description = re.search(r"DESCRIPTION:(.*)", block)
        weekday = re.search(r"\((\w+day)\)", summary.group(1)) if summary else None
        first_line = description.group(1).split("\\n")[0].strip() if description else ""
        if weekday:
            result[weekday.group(1)] = first_line
    return result


def analyze_wzag(name, **kwargs):
    out = Path(tempfile.mkdtemp())
    analyze_uploads(
        flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
        psych_pdf=WZAG / "wzag psych sheet v3.pdf",
        timeline_pdf=WZAG / "wzag timelines v4.pdf",
        swimmer_name=name, output_dir=out, state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["daily"], **kwargs,
    )
    return out


class TokenResolutionTest(unittest.TestCase):
    def test_warmup_doc_tokens_resolve_to_lsc_codes(self):
        cases = {
            "AZ": "AZ", "UT": "UT", "OR": "OR", "CO": "CO", "NM": "NM", "HI": "HI",
            "PAC": "PC", "SDI": "SI",  # irregulars
            "PNS": "PN", "SNS": "SN", "SRS": "SR", "CCS": "CC", "IES": "IE",  # suffix-S forms
        }
        for token, expected in cases.items():
            self.assertEqual(warmup_token_to_lsc(token), expected, token)

    def test_open_and_junk_tokens_resolve_to_none(self):
        for token in ("Open", "Pace", "Start", "Ledge*", "", "ZZ"):
            self.assertIsNone(warmup_token_to_lsc(token), token)


class ParseAssignmentsTest(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_warmup_assignments(WARMUP_PDF)

    def test_finals_is_a_single_universal_window(self):
        self.assertEqual(self.parsed["finals"], ("3:50pm", "4:50pm"))

    def test_prelim_windows_vary_by_day_and_team(self):
        prelim = self.parsed["prelim"]
        # AZ: 7:25-8:20 Wed/Fri, 6:30-7:25 Thu/Sat.
        self.assertEqual(prelim["Wednesday"]["AZ"], ("7:25am", "8:20am"))
        self.assertEqual(prelim["Thursday"]["AZ"], ("6:30am", "7:25am"))
        self.assertEqual(prelim["Friday"]["AZ"], ("7:25am", "8:20am"))
        self.assertEqual(prelim["Saturday"]["AZ"], ("6:30am", "7:25am"))
        # SR (via "SRS" in the AK/SRS combined cell) is in the OPPOSITE group from AZ.
        self.assertEqual(prelim["Wednesday"]["SR"], ("6:30am", "7:25am"))
        self.assertEqual(prelim["Thursday"]["SR"], ("7:25am", "8:20am"))
        # All 16 Western Zone LSCs parsed each day.
        self.assertEqual(len(prelim["Wednesday"]), 16)

    def test_no_file_returns_none(self):
        self.assertIsNone(parse_warmup_assignments(None))


class FlyerWindowExtractTest(unittest.TestCase):
    def test_extracts_an_explicit_range(self):
        self.assertEqual(extract_flyer_warmup_window("Warm-up 5:45-6:30 PM daily"), "5:45 PM-6:30 PM")
        self.assertEqual(extract_flyer_warmup_window("warm-up 7:00 AM to 8:30 AM"), "7:00 AM-8:30 AM")
        self.assertEqual(extract_flyer_warmup_window("Warm-up 5:45–6:30 PM"), "5:45 PM-6:30 PM")

    def test_does_not_fire_on_per_session_single_time(self):
        # The AZ-style per-session line is a single time, not a range -> must not be mistaken for one.
        self.assertIsNone(extract_flyer_warmup_window("Session #2 Warm-up: 7:00 am, Meet Start: 8:30 am"))
        self.assertIsNone(extract_flyer_warmup_window("Meet starts at 9:00 am"))


class ResolverPrecedenceTest(unittest.TestCase):
    def test_complex_doc_wins_over_universal_window(self):
        from datetime import date
        resolver = build_warmup_resolver(WARMUP_PDF, "5:45 PM-6:30 PM", "AZ")
        # Wednesday prelims for AZ come from the doc, not the universal fallback.
        hit = resolver(date(2026, 8, 5), False)
        self.assertEqual(hit["display"], "7:25 AM-8:20 AM")
        self.assertIn("AZ", hit["qualifier"])

    def test_universal_window_used_when_doc_has_no_match(self):
        from datetime import date
        # LSC not in the matrix -> falls back to the universal window.
        resolver = build_warmup_resolver(WARMUP_PDF, "5:45 PM-6:30 PM", "FL")
        hit = resolver(date(2026, 8, 5), False)
        self.assertEqual(hit["display"], "5:45 PM-6:30 PM")

    def test_no_data_returns_no_resolver(self):
        self.assertIsNone(build_warmup_resolver(None, None, "AZ"))


class SimpleUniversalCaseTest(unittest.TestCase):
    def test_manual_window_appears_first_on_every_day(self):
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=SHARK / "2026-shark-open-flyer.pdf",
            psych_pdf=SHARK / "2026-shark-open-heat-sheet.pdf",
            timeline_pdf=SHARK / "2026-shark-open-timeline.pdf",
            swimmer_name="Alegi, Grace", output_dir=out, state="",
            modes=["daily"], meet_warmup_window="5:45 PM - 6:30 PM",
        )
        by_day = daily_warmup_by_weekday(out)
        self.assertTrue(by_day)
        for weekday, first_line in by_day.items():
            self.assertEqual(first_line, "Warm-up: 5:45 PM - 6:30 PM", weekday)


class ComplexPerTeamPerDayCaseTest(unittest.TestCase):
    """The heart of requirement 4/6: real WZAG data, day-sensitivity and per-team correctness."""

    def test_az_window_varies_by_day_in_the_generated_calendar(self):
        by_day = daily_warmup_by_weekday(analyze_wzag("Cova, Mila L", warmup_pdf=WARMUP_PDF))
        # Cova (AZ) swims all four days; the same team's window flips between days.
        self.assertIn("Warm-up: 7:25 AM-8:20 AM", by_day["Wednesday"])
        self.assertIn("Warm-up: 6:30 AM-7:25 AM", by_day["Thursday"])
        self.assertIn("Warm-up: 7:25 AM-8:20 AM", by_day["Friday"])
        self.assertIn("Warm-up: 6:30 AM-7:25 AM", by_day["Saturday"])
        self.assertIn("AZ", by_day["Wednesday"])

    def test_a_different_team_resolves_to_a_different_window_same_day(self):
        # Steinbis (SR) on Wednesday is 6:30-7:25 -- the OPPOSITE of AZ's 7:25-8:20 that day,
        # proving the match is driven by the swimmer's own LSC, not hard-coded to AZ.
        by_day = daily_warmup_by_weekday(analyze_wzag("Steinbis, River", warmup_pdf=WARMUP_PDF))
        self.assertIn("Warm-up: 6:30 AM-7:25 AM", by_day["Wednesday"])
        self.assertIn("Warm-up: 7:25 AM-8:20 AM", by_day["Thursday"])
        self.assertIn("SR", by_day["Wednesday"])


class DayVaryingPerSessionWarmupTest(unittest.TestCase):
    """Herculean Invitational's flyer states one warm-up time per calendar day, shared by BOTH
    pools running that day ("All swimmers will have the same warm up times listed"), bare (no
    am/pm) and under a "Session N" label that does NOT share numbering with the meet's own HY-TEK
    session-report timeline (the flyer's "Session 2" is Saturday; the timeline's session 2 is
    Friday's second, simultaneous 11&Under pool) -- so this must resolve by DATE, not by matching
    the flyer's own session number against the timeline's.
    """

    def test_parse_flyer_day_warmups_keys_by_date_not_flyer_session_number(self):
        from swimtimeline.extract import extract_text_pages

        text = "\n".join(extract_text_pages(HERCULEAN / "2026-herculean-invitational-flyer.pdf"))
        parsed = parse_flyer_day_warmups(text)
        self.assertEqual(
            parsed,
            {date(2026, 9, 11): "4:45", date(2026, 9, 12): "8:15", date(2026, 9, 13): "8:15"},
        )

    def test_resolve_bare_clock_picks_the_reading_shortly_before_the_known_start(self):
        # Bare "4:45" next to a 17:30 (5:30 PM) start is 4:45 PM, not 4:45 AM.
        self.assertEqual(resolve_bare_clock_before_start("4:45", "17:30"), "16:45")
        # Bare "8:15" next to a 09:00 (9:00 AM) start is 8:15 AM, not 8:15 PM.
        self.assertEqual(resolve_bare_clock_before_start("8:15", "09:00"), "08:15")

    def test_generated_calendar_shows_the_correct_day_varying_warmup(self):
        # Friday's two pools (session 1: 12&Over, session 2: 11&Under) share the SAME warm-up --
        # proving the date-keyed resolution lands correctly on both, despite the flyer's own
        # "Session 1/2/3" numbering (by day) diverging from the timeline's "Session: 1-6"
        # numbering (by day AND pool). Zaffos, Selah (12&Over) and Post, Zoey (11&Under) both swim
        # Friday.
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=HERCULEAN / "2026-herculean-invitational-flyer.pdf",
            psych_pdf=HERCULEAN / "2026-herculean-invitational-psych-sheet.pdf",
            timeline_pdf=HERCULEAN / "2026-herculean-invitational-timeline.pdf",
            swimmer_name="Zaffos, Selah", output_dir=out, state="AZ",
            meet_timezone="America/Phoenix", modes=["daily"], timeline_projected=True,
        )
        ics = (out / "daily.ics").read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
        self.assertIn("Warm-up: 4:45 PM", ics)  # Friday, NOT the naive 4:30 PM (start minus 60min)
        self.assertIn("Warm-up: 8:15 AM", ics)  # Saturday/Sunday, NOT the naive 8:00 AM
        self.assertNotIn("Warm-up: 4:30 PM", ics)
        self.assertNotIn("Warm-up: 8:00 AM", ics)

        out2 = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=HERCULEAN / "2026-herculean-invitational-flyer.pdf",
            psych_pdf=HERCULEAN / "2026-herculean-invitational-psych-sheet.pdf",
            timeline_pdf=HERCULEAN / "2026-herculean-invitational-timeline.pdf",
            swimmer_name="Post, Zoey", output_dir=out2, state="AZ",
            meet_timezone="America/Phoenix", modes=["daily"], timeline_projected=True,
        )
        ics2 = (out2 / "daily.ics").read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
        self.assertIn("Warm-up: 4:45 PM", ics2)


class NoWarmupDataCaseTest(unittest.TestCase):
    def test_meet_with_no_warmup_source_shows_no_window_first_line(self):
        # Narwhal: no warm-up doc, no universal window. The first line is the swimmer name, NOT a
        # warm-up window. (The pre-existing per-session "Warm-up:" line inside the body is unchanged.)
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=NARWHAL / "Narwhal Invite.pdf",
            psych_pdf=NARWHAL / "narwhal final psych again.pdf",
            timeline_pdf=NARWHAL / "narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="AZ", modes=["daily"],
        )
        by_day = daily_warmup_by_weekday(out)
        self.assertTrue(by_day)
        for weekday, first_line in by_day.items():
            self.assertFalse(first_line.startswith("Warm-up:"), f"{weekday}: {first_line}")


if __name__ == "__main__":
    unittest.main()
