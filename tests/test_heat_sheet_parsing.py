"""Regression tests for heat-sheet heat/lane extraction in collect_psych_entries.

HY-TEK Meet Program (heat sheet) PDFs print a "Heat N of M <Round>" header once per
heat, concatenated onto only the first swimmer's row; the rest of the heat follows on
bare rows. The parser carries the heat/round forward with a cursor so every swimmer in a
heat gets its real heat and lane -- not just the first -- while the cursor resets at each
Event header so heat context never leaks into another event or a seeded/distance list.

Heat headers are recognized purely on "Heat N [of M]" structure with an OPAQUE round label,
so wording other than Prelims/Finals (e.g. Icebreaker's A/B/C/D-Final) still works, and the
event is carried across page breaks so continuation-page heats are no longer dropped.

Real fixtures used: Shark Open heat sheet (heated Prelims + seeded distance + continuation
pages), Icebreaker Invitational heat sheet (A/B/C/D-Final wording, Alternates, cross-page
continuation), and Narwhal Invite psych sheet (no heat data). Expected heat/lane values were
cross-checked against the raw PDF text. Tests are labeled REAL-FIXTURE (with the meet) or
SYNTHETIC (hand-constructed, for formats no available fixture exercises, e.g. "Timed Finals").
"""

from pathlib import Path
import unittest

from swimtimeline.extract import extract_psych_entries, parse_heat_header

ROOT = Path(__file__).resolve().parents[1]
SHARK_HEAT_SHEET = ROOT / "meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf"
NARWHAL_PSYCH = ROOT / "meets/2026-narwhal-invite/input/narwhal final psych again.pdf"
ICEBREAKER_HEAT_SHEET = ROOT / "meets/2026-icebreaker-invitational/2026-icebreaker-invitational-heat-sheet.pdf"


def entry_for_event(pdf: Path, name: str, event_number: int):
    entries, _, _ = extract_psych_entries(pdf, name)
    for entry in entries:
        if entry.event_number == event_number:
            return entry
    return None


class HeatSheetHeatLaneTest(unittest.TestCase):
    def test_every_swimmer_in_a_heat_gets_heat_and_lane_not_just_the_first(self):
        # Shark Open Event 3 (Girls 200 IM), Heat 1: Gunn is listed first (carries the
        # "Heat 1 of 6" header), Rigney and Buchmueller follow on bare rows. Before the
        # cursor fix, only Gunn parsed correctly; the others lost heat context and had
        # their lane silently recorded as a seed place.
        for name, lane in [("Gunn, Helaina E", 3), ("Rigney, Evelyn R", 4), ("Buchmueller, Beatrice C", 5)]:
            entry = entry_for_event(SHARK_HEAT_SHEET, name, 3)
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry.document_type, "heat", name)
            self.assertEqual(entry.heat, 1, name)
            self.assertEqual(entry.lane, lane, name)
            self.assertEqual(entry.round_name, "Prelims", name)

    def test_first_in_heat_swimmer_still_correct(self):
        # Domico leads Heat 6 of Event 3 (lane 1) -- the previously-working first-in-heat case.
        entry = entry_for_event(SHARK_HEAT_SHEET, "Domico, Faith O", 3)
        self.assertEqual((entry.document_type, entry.heat, entry.lane), ("heat", 6, 1))

    def test_cursor_advances_across_heats_within_an_event(self):
        # Event 37 (Girls 200 Free): Rigney is a bare row in Heat 1 (lane 5); Gunn is a
        # bare row in Heat 2 (lane 6). Confirms the cursor tracks each heat, not just Heat 1.
        rigney = entry_for_event(SHARK_HEAT_SHEET, "Rigney, Evelyn R", 37)
        gunn = entry_for_event(SHARK_HEAT_SHEET, "Gunn, Helaina E", 37)
        self.assertEqual((rigney.heat, rigney.lane), (1, 5))
        self.assertEqual((gunn.heat, gunn.lane), (2, 6))

    def test_seeded_distance_event_stays_seed_place_no_heat_leak(self):
        # Event 1 (Girls 800 Free) is a seeded timed-final list with no "Heat" headers, so
        # its rows must remain seed-place. This also proves the Event header reset: Gunn's
        # Event 3 is heated, but that heat context must not bleed back into Event 1.
        entry = entry_for_event(SHARK_HEAT_SHEET, "Gunn, Helaina E", 1)
        self.assertEqual(entry.document_type, "psych")
        self.assertIsNone(entry.heat)
        self.assertIsNone(entry.lane)
        self.assertEqual(entry.seed_place, 28)

    def test_single_extraction_isolates_heated_and_seeded_events(self):
        # In one extraction Gunn has a seeded event (#1) and heated events (#3, #37).
        # Each must carry the right shape -- heat context confined to the heated events.
        entries, _, _ = extract_psych_entries(SHARK_HEAT_SHEET, "Gunn, Helaina E")
        by_event = {e.event_number: e for e in entries}
        self.assertEqual(by_event[1].document_type, "psych")
        self.assertEqual((by_event[3].document_type, by_event[3].heat, by_event[3].lane), ("heat", 1, 3))
        self.assertEqual((by_event[37].document_type, by_event[37].heat, by_event[37].lane), ("heat", 2, 6))


class PsychOnlyNoHeatLeakTest(unittest.TestCase):
    def test_narwhal_psych_sheet_has_no_heat_or_lane_anywhere(self):
        entries, _, _ = extract_psych_entries(NARWHAL_PSYCH, "Cova, Mila L")
        self.assertEqual(len(entries), 7)
        for entry in entries:
            self.assertEqual(entry.document_type, "psych", entry.event_number)
            self.assertIsNone(entry.heat, entry.event_number)
            self.assertIsNone(entry.lane, entry.event_number)
            self.assertIsNone(entry.round_name, entry.event_number)
            self.assertGreater(entry.seed_place, 0, entry.event_number)


class SharkOpenContinuationPageTest(unittest.TestCase):
    """REAL-FIXTURE (2026-shark-open heat sheet). An event's heats can spill onto a new page
    that begins with only a "Heat N Prelims (#event ...)" continuation header, not a repeated
    "Event N" line. The page-scoped event lookup used to discard every such row (~92 rows /
    84 swimmers). The event cursor now carries across the page break so they are captured.
    Page 4 top (Event 8, Boys 13 & Over 50 LC Meter Freestyle, Heat 4): Presley lane 1,
    Thorne lane 2, Sultan lane 3 -- cross-checked against the raw PDF."""

    def test_continuation_page_swimmers_are_recovered_with_correct_heat_and_lane(self):
        for name, lane in [("Presley, Charley L", 1), ("Thorne, David", 2), ("Sultan, Ziyad", 3)]:
            entry = entry_for_event(SHARK_HEAT_SHEET, name, 8)
            self.assertIsNotNone(entry, f"{name} event 8 was dropped (continuation-page bug)")
            self.assertEqual(entry.document_type, "heat", name)
            self.assertEqual(entry.heat, 4, name)
            self.assertEqual(entry.lane, lane, name)


class IcebreakerGeneralizedRoundLabelTest(unittest.TestCase):
    """REAL-FIXTURE (2026-icebreaker-invitational heat sheet). This meet's heat headers use
    A/B/C/D-Final round wording, on their own line, with "#N" event headers and "Alternates"
    sections -- none of which match the old Prelims|Finals enum. It verifies that heat headers
    are now recognized purely on "Heat N" structure with an opaque round label. Expected
    heat/lane cross-checked against the raw PDF (Event 5, Women 13 & Over 200 LC Meter Back)."""

    def test_abc_final_round_labels_are_recognized_as_heats(self):
        expected = {
            "Osborne, Riley K": (1, 3, "C - Final"),
            "Isleta, Chloe D": (3, 4, "A - Final"),
            "Ferguson, Emma K": (3, 8, "A - Final"),
        }
        for name, (heat, lane, round_name) in expected.items():
            entry = entry_for_event(ICEBREAKER_HEAT_SHEET, name, 5)
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry.document_type, "heat", name)
            self.assertEqual(entry.heat, heat, name)
            self.assertEqual(entry.lane, lane, name)
            self.assertEqual(entry.round_name, round_name, name)

    def test_alternates_section_is_not_assigned_to_a_heat(self):
        entry = entry_for_event(ICEBREAKER_HEAT_SHEET, "Beckman, Autumn R", 5)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.document_type, "psych")
        self.assertIsNone(entry.heat)
        self.assertIsNone(entry.lane)

    def test_cross_page_continuation_header_carries_event_and_heat(self):
        # Event 9 spans pages: Montanez leads Heat 1 (D-Final) on page 1; Jarecki is in the
        # continuation "Heat 4 (#9 Women 13 & Over 100 LC Meter Breaststroke)" at the top of
        # page 2. Both must resolve to event 9 with correct heat/lane.
        jarecki = entry_for_event(ICEBREAKER_HEAT_SHEET, "Jarecki, Addison", 9)
        self.assertEqual((jarecki.document_type, jarecki.heat, jarecki.lane), ("heat", 4, 1))
        montanez = entry_for_event(ICEBREAKER_HEAT_SHEET, "Montanez, Constance", 9)
        self.assertEqual((montanez.document_type, montanez.heat, montanez.lane, montanez.round_name), ("heat", 1, 1, "D - Final"))


class HeatHeaderStructuralParsingTest(unittest.TestCase):
    """SYNTHETIC (hand-constructed lines, NOT from any committed fixture). Unit tests for
    parse_heat_header covering formats not present in the two real fixtures -- most notably
    the "Timed Finals" round label, which was the original motivating gap and which no
    available fixture exercises. Recognition must depend only on the "Heat N" structure."""

    def test_timed_finals_own_line_synthetic(self):
        hh = parse_heat_header("Heat 3 of 5 Timed Finals")
        self.assertIsNotNone(hh)
        self.assertEqual(hh.heat, 3)
        self.assertEqual(hh.round_name, "Timed Finals")
        self.assertEqual(hh.swimmer_remainder, "")

    def test_timed_finals_concatenated_first_swimmer_synthetic(self):
        # Round word glued to the first swimmer's team, as HY-TEK sometimes lays it out.
        hh = parse_heat_header("Heat 1 of 4 Timed FinalsSYS-FL 1:02.34 15Doe, Jane A3")
        self.assertEqual(hh.heat, 1)
        self.assertEqual(hh.round_name, "Timed Finals")
        self.assertEqual(hh.swimmer_remainder, "SYS-FL 1:02.34 15Doe, Jane A3")

    def test_arbitrary_opaque_round_label_own_line_synthetic(self):
        hh = parse_heat_header("Heat 2 Bonus Final")
        self.assertEqual((hh.heat, hh.round_name, hh.swimmer_remainder), (2, "Bonus Final", ""))

    def test_continuation_event_reference_synthetic(self):
        hh = parse_heat_header("Heat 4 Prelims (#8 Boys 13 & Over 50 LC Meter Freestyle)TEAM-XX 26.60 16Roe, Sam B1")
        self.assertEqual(hh.heat, 4)
        self.assertEqual(hh.event_number, 8)
        self.assertEqual(hh.event_name, "Boys 13 & Over 50 LC Meter Freestyle")
        self.assertEqual(hh.swimmer_remainder, "TEAM-XX 26.60 16Roe, Sam B1")

    def test_non_heat_line_is_not_a_header_synthetic(self):
        self.assertIsNone(parse_heat_header("SYS-FL 2:13.20Y 15Gunn, Helaina E3"))
        self.assertIsNone(parse_heat_header("#5 Women 13 & Over 200 LC Meter Backstroke"))

    def test_bare_HEAT_team_code_is_not_a_header_synthetic(self):
        # Hardening: a swimmer whose team code is literally "HEAT" with the seed glued on must
        # NOT be read as a heat header (the "25" is part of the seed 25.52, not a heat number).
        self.assertIsNone(parse_heat_header("HEAT 25.52 17Waite, Nikki E3"))
        # The real-world variant carries an LSC suffix and was always fine.
        self.assertIsNone(parse_heat_header("HEAT-AZ 2:12.12 16Hauck, Ella L7"))

    def test_concatenated_round_peel_does_not_eat_team_leading_letter_synthetic(self):
        # Hardening: peeling "Swim-off" off a glued "Swim-offSYS-FL" must leave the team intact
        # (an earlier optional trailing "s" swallowed the team's leading "S").
        hh = parse_heat_header("Heat 1 Swim-offSYS-FL 2:13.20 15Doe, Jane A3")
        self.assertEqual(hh.round_name, "Swim-off")
        self.assertEqual(hh.swimmer_remainder, "SYS-FL 2:13.20 15Doe, Jane A3")


if __name__ == "__main__":
    unittest.main()
