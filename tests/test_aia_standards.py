"""AIA (Arizona Interscholastic Association) high-school state-qualifying standards.

A new, separate benchmark path that REPLACES the USA-S motivational tiers for a meet that opts
into it (data/current_meets.json's "standards": {"body": "AIA", "division": "D1", ...}), rather
than layering on top the way AZSI does. AIA cuts are gender+event+division only -- no age, no
course (AIA meets run SCY only), no next-tier bonus ladder: one cut per division, met or not.

Real fixture: meets/2026-higley-knights-spooktacular/input/2026-higley-knights-spooktacular-heat-
sheet.pdf. "Beltran, Christian" swims event 3 (Boys 200 Yard Freestyle, seed 1:48.01) and event 17
(Boys 100 Yard Backstroke, seed 54.50) -- both comfortably faster than their real AIA Boys D1 cuts
(2:07.92 and 1:08.21 respectively, transcribed from page 3 of Knight_Invite_Spooktacular_Program_
13th_Annual_v5.pdf's "AIA STATE CHAMPIONSHIP 2026 Qualifying Standards" table and independently
re-verified against that page directly). "Tolliver, Parker" swims the same event 3 at a real 2:45.00
seed -- slower than the D1 cut -- exercising the "off by" branch on real, not synthetic, data.
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.extract import analyze_uploads, extract_psych_entries  # noqa: E402
from swimtimeline.standards import (  # noqa: E402
    achieved_aia_standard,
    aia_summary_line,
    canonical_event_key,
    event_gender,
    parse_time,
)

try:
    from webapp.server import resolve_current_meet, resolve_current_meet_documents
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc

HIGLEY_DIR = ROOT / "meets/2026-higley-knights-spooktacular/input"
HIGLEY_TIMELINE = HIGLEY_DIR / "2026-higley-knights-spooktacular-timeline.pdf"
HIGLEY_HEAT_SHEET = HIGLEY_DIR / "2026-higley-knights-spooktacular-heat-sheet.pdf"
HERC_DIR = ROOT / "meets/2026-herculean-invitational/input"


class AchievedAiaStandardUnitTest(unittest.TestCase):
    """achieved_aia_standard()/aia_summary_line() against the real data/aia_standards.json table."""

    def test_a_time_faster_than_the_cut_qualifies(self):
        qualified, cut = achieved_aia_standard(127.92, "boys", "200 free", "D1")
        self.assertTrue(qualified)
        self.assertEqual(cut, 127.92)

    def test_a_time_slower_than_the_cut_does_not_qualify(self):
        qualified, cut = achieved_aia_standard(130.0, "boys", "200 free", "D1")
        self.assertFalse(qualified)
        self.assertEqual(cut, 127.92)

    def test_a_time_exactly_at_the_cut_qualifies(self):
        # <=, same convention as achieved_tier()'s own cut comparison.
        qualified, _cut = achieved_aia_standard(127.92, "boys", "200 free", "D1")
        self.assertTrue(qualified)

    def test_an_event_aia_does_not_score_is_not_configured_not_a_false_result(self):
        # AIA's table has no 50 back at all -- must report "not configured", not silently pass
        # or fail against some other event's cut.
        qualified, cut = achieved_aia_standard(30.0, "boys", "50 back", "D1")
        self.assertIsNone(qualified)
        self.assertIsNone(cut)

    def test_a_missing_division_is_not_configured(self):
        qualified, cut = achieved_aia_standard(127.92, "boys", "200 free", None)
        self.assertIsNone(qualified)
        self.assertIsNone(cut)

    def test_summary_line_when_qualified(self):
        self.assertEqual(
            aia_summary_line("boys", "200 free", "D1", 127.92),
            "AIA Boys D1: State qualified (2:07.92)",
        )

    def test_summary_line_when_not_qualified_states_the_margin_not_a_false_qualify(self):
        line = aia_summary_line("boys", "200 free", "D1", 165.0)  # 2:45.00
        self.assertEqual(line, "AIA Boys D1: 37.08s off the D1 cut (2:07.92)")
        self.assertNotIn("qualified", line)

    def test_summary_line_for_girls_uses_the_girls_column(self):
        # Girls D1 200 Free is 2:18.92 (138.92s) -- a materially different cut from Boys'.
        self.assertEqual(
            aia_summary_line("girls", "200 free", "D1", 138.92),
            "AIA Girls D1: State qualified (2:18.92)",
        )

    def test_every_real_event_in_the_table_resolves_for_both_genders_and_all_three_divisions(self):
        events = [
            "200 medley relay", "200 free", "200 im", "50 free", "100 fly", "100 free",
            "500 free", "200 free relay", "100 back", "100 breast", "400 free relay",
        ]
        for gender in ("girls", "boys"):
            for event_key in events:
                for division in ("D1", "D2", "D3"):
                    qualified, cut = achieved_aia_standard(0.0, gender, event_key, division)
                    self.assertIsNotNone(cut, (gender, event_key, division))
                    self.assertTrue(qualified, (gender, event_key, division))  # 0.0s beats anything

    def test_diving_is_not_in_the_table_at_all(self):
        """The source table's 'Diving (11 dives)' row is a judged score, not a time -- confirmed
        excluded, not silently mis-parsed as a time standard."""
        for gender in ("girls", "boys"):
            for division in ("D1", "D2", "D3"):
                qualified, cut = achieved_aia_standard(0.0, gender, "diving", division)
                self.assertIsNone(qualified)
                self.assertIsNone(cut)


class RealHeatSheetAiaEndToEndTest(unittest.TestCase):
    """The real Higley heat sheet, through canonical_event_key()/event_gender() exactly as
    build_swim_events() will call them -- not hand-picked keys."""

    def test_beltrans_faster_than_cut_swims_show_state_qualified(self):
        entries, _page_counts, warnings = extract_psych_entries(HIGLEY_HEAT_SHEET, "Beltran, Christian")
        self.assertEqual(warnings, [])
        by_event = {e.event_number: e for e in entries}
        self.assertEqual(sorted(by_event), [3, 17])
        for number, expected_cut in ((3, "2:07.92"), (17, "1:08.21")):
            entry = by_event[number]
            gender = event_gender(entry.event_name)
            event_key = canonical_event_key(entry.event_name)
            seed_seconds = parse_time(entry.seed_time)
            qualified, cut = achieved_aia_standard(seed_seconds, gender, event_key, "D1")
            self.assertTrue(qualified, entry.event_name)
            self.assertEqual(cut, parse_time(expected_cut), entry.event_name)
            line = aia_summary_line(gender, event_key, "D1", seed_seconds)
            self.assertIn("State qualified", line, entry.event_name)

    def test_a_real_swim_slower_than_the_cut_shows_the_margin_not_a_false_qualify(self):
        entries, _page_counts, warnings = extract_psych_entries(HIGLEY_HEAT_SHEET, "Tolliver, Parker")
        self.assertEqual(warnings, [])
        by_event = {e.event_number: e for e in entries}
        entry = by_event[3]
        self.assertEqual(entry.event_name, "Boys 200 Yard Freestyle")
        self.assertEqual(entry.seed_time, "2:45.00")
        gender = event_gender(entry.event_name)
        event_key = canonical_event_key(entry.event_name)
        seed_seconds = parse_time(entry.seed_time)
        qualified, cut = achieved_aia_standard(seed_seconds, gender, event_key, "D1")
        self.assertFalse(qualified)
        self.assertAlmostEqual(cut, parse_time("2:07.92"))
        line = aia_summary_line(gender, event_key, "D1", seed_seconds)
        self.assertNotIn("qualified", line)
        self.assertIn("off the D1 cut", line)
        self.assertIn("2:07.92", line)


class BuildSwimEventsAiaWiringTest(unittest.TestCase):
    """analyze_uploads() end to end, with meet_standards passed exactly the way
    resolve_current_meet_documents() will pass it for a real meet record."""

    STANDARDS = {"body": "AIA", "division": "D1", "season": "2026"}

    @classmethod
    def setUpClass(cls):
        cls.output_dir = Path(tempfile.mkdtemp())
        cls.payload = analyze_uploads(
            flyer_pdf=None,
            psych_pdf=HIGLEY_HEAT_SHEET,
            timeline_pdf=HIGLEY_TIMELINE,
            swimmer_name="Beltran, Christian",
            output_dir=cls.output_dir,
            state="AZ",
            meet_timezone="America/Phoenix",
            meet_venue="Williams Field High School, Gilbert, AZ",
            modes=["daily"],
            estimate_heat_lanes=False,
            timeline_projected=False,
            meet_standards=cls.STANDARDS,
        )

    def test_both_real_events_carry_the_aia_line_in_the_usa_slot(self):
        by_number = {item["event_number"]: item for item in self.payload["items"]}
        self.assertEqual(sorted(by_number), [3, 17])
        self.assertEqual(
            by_number[3]["benchmarks"]["usa"], "AIA Boys D1: State qualified (2:07.92)"
        )
        self.assertEqual(
            by_number[17]["benchmarks"]["usa"], "AIA Boys D1: State qualified (1:08.21)"
        )

    def test_usa_s_and_azsi_are_skipped_entirely_not_shown_alongside(self):
        for item in self.payload["items"]:
            benchmarks = item["benchmarks"]
            self.assertFalse(benchmarks["lsc"])
            self.assertIsNone(benchmarks["sectional"])
            self.assertIsNone(benchmarks["national"])
            self.assertIsNone(benchmarks["advanced"])
            self.assertFalse(benchmarks["confidence"])
            self.assertEqual(benchmarks["sources"], {})
            # Genuinely absent, not merely empty text -- no stray "USA-S"/"AZSI" substring either.
            self.assertNotIn("USA-S", benchmarks["usa"])
            self.assertNotIn("AZSI", benchmarks["usa"])

    def test_the_calendar_description_text_has_no_lsc_na_line(self):
        """The .ics description builder must skip the LSC line entirely for this meet, not print
        a hollow "LSC: n/a" under the real AIA line."""
        ics_path = self.output_dir / self.payload["files"]["daily_ics"]
        ics_text = ics_path.read_text(encoding="utf-8")
        self.assertNotIn("LSC: n/a", ics_text)
        self.assertIn("AIA Boys D1: State qualified", ics_text)


class MeetWithoutStandardsIsUnaffectedTest(unittest.TestCase):
    """The additive guarantee: a meet record with no "standards" field -- every meet but Higley
    today -- must be completely unaffected, byte-identical to before this feature existed."""

    def test_a_real_non_aia_meet_still_uses_usa_s_azsi_exactly_as_before(self):
        payload = analyze_uploads(
            flyer_pdf=HERC_DIR / "2026-herculean-invitational-flyer.pdf",
            psych_pdf=HERC_DIR / "2026-herculean-invitational-psych-sheet.pdf",
            timeline_pdf=HERC_DIR / "2026-herculean-invitational-timeline.pdf",
            swimmer_name="Cova, Mila",
            output_dir=Path(tempfile.mkdtemp()),
            state="AZ",
            modes=["daily"],
        )  # meet_standards omitted entirely, same as every existing caller before this feature
        self.assertTrue(payload["items"])
        for item in payload["items"]:
            benchmarks = item["benchmarks"]
            self.assertTrue(benchmarks["usa"].startswith("USA-S"), benchmarks["usa"])
            self.assertNotIn("AIA", benchmarks["usa"])

    def test_passing_meet_standards_none_explicitly_is_identical_to_omitting_it(self):
        kwargs = dict(
            flyer_pdf=HERC_DIR / "2026-herculean-invitational-flyer.pdf",
            psych_pdf=HERC_DIR / "2026-herculean-invitational-psych-sheet.pdf",
            timeline_pdf=HERC_DIR / "2026-herculean-invitational-timeline.pdf",
            swimmer_name="Cova, Mila",
            state="AZ",
            modes=["daily"],
        )
        omitted = analyze_uploads(output_dir=Path(tempfile.mkdtemp()), **kwargs)
        explicit_none = analyze_uploads(output_dir=Path(tempfile.mkdtemp()), meet_standards=None, **kwargs)
        self.assertEqual(
            [item["benchmarks"] for item in omitted["items"]],
            [item["benchmarks"] for item in explicit_none["items"]],
        )

    def test_a_meet_record_with_a_non_aia_body_falls_through_to_usa_s_unaffected(self):
        """meet_standards present but body != "AIA" must behave exactly like no override at
        all -- the branch is gated on body == "AIA" specifically, not merely "standards present"."""
        payload = analyze_uploads(
            flyer_pdf=HERC_DIR / "2026-herculean-invitational-flyer.pdf",
            psych_pdf=HERC_DIR / "2026-herculean-invitational-psych-sheet.pdf",
            timeline_pdf=HERC_DIR / "2026-herculean-invitational-timeline.pdf",
            swimmer_name="Cova, Mila",
            output_dir=Path(tempfile.mkdtemp()),
            state="AZ",
            modes=["daily"],
            meet_standards={"body": "SOMETHING-ELSE", "division": "D1"},
        )
        for item in payload["items"]:
            self.assertNotIn("AIA", item["benchmarks"]["usa"])


class CurrentMeetsRegistryWiringTest(unittest.TestCase):
    """The JSON record itself: Higley carries the new "standards" field, and
    resolve_current_meet_documents() (the real server-side plumbing handle_analyze_current and
    the /subscribe.ics feed both go through) surfaces it as meet_standards."""

    def test_higleys_registry_entry_carries_the_aia_standards_field(self):
        meet = resolve_current_meet("2026-higley-knights-spooktacular")
        self.assertEqual(meet.get("standards"), {"body": "AIA", "division": "D1", "season": "2026"})

    def test_resolve_current_meet_documents_surfaces_it_as_meet_standards(self):
        meet = resolve_current_meet("2026-higley-knights-spooktacular")
        docs = resolve_current_meet_documents(meet)
        self.assertEqual(docs["meet_standards"], {"body": "AIA", "division": "D1", "season": "2026"})

    def test_a_meet_without_the_field_surfaces_none(self):
        meet = resolve_current_meet("2026-herculean-invitational")
        docs = resolve_current_meet_documents(meet)
        self.assertIsNone(docs["meet_standards"])


if __name__ == "__main__":
    unittest.main()
