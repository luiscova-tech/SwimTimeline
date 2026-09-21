"""Two real, previously-unhit gaps surfaced by the Higley Knights Spooktacular documents.

Real fixtures: meets/2026-higley-knights-spooktacular/input/2026-higley-knights-spooktacular-
timeline.pdf and .../2026-higley-knights-spooktacular-heat-sheet.pdf -- a real post-seeding heat
sheet (actual lane-by-lane assignments with seed times), not a pre-meet psych sheet.

GAP 1 -- heat-sheet rows with no age column at all. This is a high-school meet: its heat sheet's
"YrName   SchoolLane Seed Time" column prints a GRADE ("FR"/"SO"/"JR"/"SR"), not a numeric age --
and most rows print no grade at all, e.g. "HA-MC 2:02.28Occhiline, Joey4". parse_entry_fields()
required a mandatory 1-2 digit age between seed and name, so every one of these real rows failed
to match and the swim was silently dropped -- a real name search (e.g. "Beltran, Christian")
returned zero results. Fixed by making the age group optional and letting it also match the four
real grade codes (kept strictly uppercase via (?-i:...), so a name starting "Fr"/"So"/"Jr"/"Sr"
is never mistaken for one).

That fix alone would have introduced a SECOND, worse bug: the seed time's own optional trailing
conversion-flag letter ("2:23.23Y") was not anchored to a real separator, so on a row with no
age/grade at all it swallowed the swimmer's own first name letter instead ("1:47.71Patience..."
parsed as seed "1:47.71P", name "atience..."). That ambiguity was latent but harmless while age
was mandatory (the row just failed to match instead of corrupting), so making age optional had to
come with anchoring the flag letter to whitespace-or-end-of-row too (matching the same lookahead
parse_para_psych_line already uses for its own copy of this pattern).

GAP 2 -- a single-day meet whose one included session is labeled "Day of Meet: 2". This meet ran
diving as Meet Manager's "Day of Meet: 1" earlier the SAME calendar day (2026-09-26); the swimming
session in this upload is "Day of Meet: 2" -- a same-day SESSION index, not a second calendar
day (confirmed with the meet's own organizer). parse_timeline()'s session_date = start_date +
timedelta(days=day_of_meet - 1) assumes day_of_meet always tracks calendar days, which computed
2026-09-27 -- a full day off -- for every one of this meet's 22 real events. Every other
single-day fixture in this repo (Croswhite, Cummins) happened to already print "Day of Meet: 1",
so the bug had no fixture to expose it until now. Fixed narrowly: when the meet's OWN confirmed
date range spans exactly one calendar day (start_date == end_date), every session is that one
day, full stop -- day_of_meet is never consulted for the date. A genuinely multi-day meet (see
HerculeanDayOfMeetRegressionGuardTest) always states a real range and is untouched.
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.extract import (  # noqa: E402
    analyze_uploads,
    extract_psych_entries,
    parse_entry_fields,
    parse_timeline,
)

HIGLEY_DIR = ROOT / "meets/2026-higley-knights-spooktacular/input"
HIGLEY_TIMELINE = HIGLEY_DIR / "2026-higley-knights-spooktacular-timeline.pdf"
HIGLEY_HEAT_SHEET = HIGLEY_DIR / "2026-higley-knights-spooktacular-heat-sheet.pdf"
HERC_TIMELINE = ROOT / "meets/2026-herculean-invitational/input/2026-herculean-invitational-timeline.pdf"


class GradeColumnHeatSheetRowTest(unittest.TestCase):
    """parse_entry_fields() against real rows, verbatim from the heat sheet's Event #3 (Boys 200
    Yard Freestyle) and neighboring events."""

    def test_a_row_with_no_age_or_grade_at_all_parses_the_full_name(self):
        # Before the fix: no match at all (age was mandatory). A naive fix that only added the
        # grade alternative, without anchoring the seed's flag letter, would have corrupted this
        # to name "atience, Beckham" -- see the module docstring's second bug.
        row = parse_entry_fields("HIGH 1:47.71Patience, Beckham4", heat=4, round_name="Finals")
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("HIGH", "1:47.71", None))
        self.assertEqual(row.swimmer_name, "Patience, Beckham")
        self.assertEqual(row.lane, 4)

    def test_another_no_age_row_is_not_corrupted_either(self):
        row = parse_entry_fields("ALAGN 1:48.01Beltran, Christian5", heat=4, round_name="Finals")
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("ALAGN", "1:48.01", None))
        self.assertEqual(row.swimmer_name, "Beltran, Christian")
        self.assertEqual(row.lane, 5)

    def test_a_row_with_no_separator_before_the_name_is_not_corrupted(self):
        """No age/grade AND no space before the name -- the seed time is glued straight on."""
        row = parse_entry_fields("HA-MC 2:02.28Occhiline, Joey4", heat=4, round_name="Finals")
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("HA-MC", "2:02.28", None))
        self.assertEqual(row.swimmer_name, "Occhiline, Joey")  # not "cchiline, Joey"

    def test_each_real_grade_code_parses_as_the_age_field_not_the_name(self):
        cases = [
            ("BENJ 2:45.00 FRTolliver, Parker4", "BENJ", "FR", "Tolliver, Parker", 4),
            ("AFHS 2:58.35 SOMoinet Digeronimo, Dominic5", "AFHS", "SO", "Moinet Digeronimo, Dominic", 5),
            ("DRHS 2:20.67 JRRaines, Parker1", "DRHS", "JR", "Raines, Parker", 1),
        ]
        for raw, team, grade, name, lane in cases:
            row = parse_entry_fields(raw, heat=3, round_name="Finals")
            self.assertIsNotNone(row, raw)
            self.assertEqual((row.team, row.age), (team, grade), raw)
            self.assertEqual(row.swimmer_name, name, raw)
            self.assertEqual(row.lane, lane, raw)

    def test_a_name_that_starts_with_a_grade_like_syllable_is_not_misread(self):
        """(?-i:FR|SO|JR|SR) is case-sensitive on purpose: a title-case name starting "Fr"/"So"/
        "Jr"/"Sr" must not lose those two letters to a false-positive grade match."""
        row = parse_entry_fields("BENJ 2:10.00Frost, Jordan6", heat=2, round_name="Finals")
        self.assertIsNotNone(row)
        self.assertIsNone(row.age)
        self.assertEqual(row.swimmer_name, "Frost, Jordan")  # not "ost, Jordan"

    def test_existing_flag_letter_fixtures_are_unaffected(self):
        """The seed pattern's anchored flag letter must still recognize every real NTY/NTL/L case
        this repo already covers (see test_nt_conversion_flag.py) -- spot-checked here too since
        both bugs live in the same regex."""
        cases = [
            ("Whalers-WI NTY 13Perelshteyn, Andrew6", "NTY", "13", "Perelshteyn, Andrew"),
            ("SNS NTL 14Galizio, Carmella B62", "NTL", "14", "Galizio, Carmella B"),
            ("Arizona 39.82L 12Cova, Mila2 B", "39.82L", "12", "Cova, Mila"),
        ]
        for raw, seed, age, name in cases:
            row = parse_entry_fields(raw, heat=1, round_name="Finals")
            self.assertIsNotNone(row, raw)
            self.assertEqual(row.seed, seed, raw)
            self.assertEqual(row.age, age, raw)
            self.assertEqual(row.swimmer_name, name, raw)


class RealHeatSheetNameSearchTest(unittest.TestCase):
    """extract_psych_entries() end to end, against the real document -- not just the line
    parser. Both names are real entries confirmed directly in the PDF text."""

    def test_beltran_christian_resolves_with_correct_seed_heat_and_lane(self):
        entries, _page_counts, warnings = extract_psych_entries(HIGLEY_HEAT_SHEET, "Beltran, Christian")
        self.assertEqual(warnings, [])
        by_event = {e.event_number: e for e in entries}
        self.assertEqual(sorted(by_event), [3, 17])
        self.assertEqual(
            (by_event[3].seed_time, by_event[3].heat, by_event[3].lane),
            ("1:48.01", 4, 5),
        )
        self.assertEqual(
            (by_event[17].seed_time, by_event[17].heat, by_event[17].lane),
            ("54.50", 6, 5),
        )

    def test_patience_beckham_resolves_with_correct_seed_heat_and_lane(self):
        entries, _page_counts, warnings = extract_psych_entries(HIGLEY_HEAT_SHEET, "Patience, Beckham")
        self.assertEqual(warnings, [])
        by_event = {e.event_number: e for e in entries}
        self.assertEqual(sorted(by_event), [3, 9])
        self.assertEqual(
            (by_event[3].seed_time, by_event[3].heat, by_event[3].lane),
            ("1:47.71", 4, 4),
        )
        self.assertEqual(
            (by_event[9].seed_time, by_event[9].heat, by_event[9].lane),
            ("52.82", 3, 3),
        )

    def test_neither_real_swimmer_reports_a_document_reading_warning(self):
        # Before the fix this was 0 entries and no error -- indistinguishable from "not entered
        # in this meet" -- exactly the silent-drop failure mode the module docstring describes.
        for name in ("Beltran, Christian", "Patience, Beckham"):
            entries, _page_counts, warnings = extract_psych_entries(HIGLEY_HEAT_SHEET, name)
            self.assertGreater(len(entries), 0, name)
            self.assertEqual(warnings, [], name)


class SingleDaySessionDateTest(unittest.TestCase):
    """parse_timeline() against the real Higley timeline: a single-day meet (2026-09-26) whose
    one included session is "Day of Meet: 2"."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(HIGLEY_TIMELINE)

    def test_the_session_keeps_its_own_day_of_meet_number(self):
        # day_of_meet is metadata, not overwritten -- only the DATE computed from it changes.
        session = self.sessions["2"]
        self.assertEqual(session.day_of_meet, 2)

    def test_the_session_date_is_the_meets_real_single_day_not_the_day_after(self):
        # Before the fix: 2026-09-27 (start_date + (day_of_meet - 1) days) -- a full day off.
        session = self.sessions["2"]
        self.assertEqual(session.date.isoformat(), "2026-09-26")

    def test_every_real_event_lands_on_the_correct_calendar_day(self):
        self.assertEqual(len(self.events), 22)
        for event in self.events:
            self.assertEqual(event.date.isoformat(), "2026-09-26", event.event_number)
            self.assertEqual(event.start.date().isoformat(), "2026-09-26", event.event_number)

    def test_the_first_and_last_events_match_the_real_document(self):
        first, last = self.events[0], self.events[-1]
        self.assertEqual((first.event_number, first.event_name), (1, "Boys 200 Medley Relay"))
        self.assertEqual(first.start.strftime("%Y-%m-%d %I:%M %p"), "2026-09-26 09:00 AM")
        self.assertEqual((last.event_number, last.event_name), (22, "Girls 400 Freestyle Relay"))
        self.assertEqual(last.start.strftime("%Y-%m-%d %I:%M %p"), "2026-09-26 01:37 PM")

    def test_every_round_is_finals_confirming_the_registered_rules_summary(self):
        self.assertTrue(all(event.round_name == "Finals" for event in self.events))


class HerculeanDayOfMeetRegressionGuardTest(unittest.TestCase):
    """The fix is gated on start_date == end_date, so a genuinely multi-day meet (Herculean: three
    real calendar days, two parallel-pool sessions per day) must be completely unaffected."""

    def test_each_days_two_sessions_still_land_on_their_own_real_calendar_day(self):
        _name, sessions, _events = parse_timeline(HERC_TIMELINE)
        expected = {
            "1": (1, "2026-09-11"), "2": (1, "2026-09-11"),
            "3": (2, "2026-09-12"), "4": (2, "2026-09-12"),
            "5": (3, "2026-09-13"), "6": (3, "2026-09-13"),
        }
        for number, (day_of_meet, iso) in expected.items():
            session = sessions[number]
            self.assertEqual(session.day_of_meet, day_of_meet, number)
            self.assertEqual(session.date.isoformat(), iso, number)


class EndToEndCalendarWithRealHeatLaneTest(unittest.TestCase):
    """analyze_uploads() -- the actual family-page path -- with this meet's real heat sheet and
    no flyer, confirming a real (not estimated) heat/lane reaches the generated calendar."""

    def test_a_real_swimmers_events_carry_a_real_not_estimated_heat_and_lane(self):
        payload = analyze_uploads(
            flyer_pdf=None,
            psych_pdf=HIGLEY_HEAT_SHEET,
            timeline_pdf=HIGLEY_TIMELINE,
            swimmer_name="Beltran, Christian",
            output_dir=Path(tempfile.mkdtemp()),
            state="AZ",
            meet_timezone="America/Phoenix",
            meet_venue="Williams Field High School, Gilbert, AZ",
            modes=["daily"],
            estimate_heat_lanes=False,
            timeline_projected=False,
        )
        self.assertEqual(payload["verified_event_count"], 2)
        by_number = {item["event_number"]: item for item in payload["items"]}
        self.assertEqual(sorted(by_number), [3, 17])
        for item in by_number.values():
            self.assertFalse(item["heat_is_estimated"], item)
            self.assertIsNone(item["estimate_note"], item)
            self.assertEqual(item["sort_start"][:10], "2026-09-26", item)  # not 09-27
        self.assertEqual(by_number[3]["heat"], 4)
        self.assertEqual(by_number[3]["lane"], 5)
        self.assertEqual(by_number[3]["seed_time"], "1:48.01")


if __name__ == "__main__":
    unittest.main()
