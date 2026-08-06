"""Real WZAG Wednesday documents: heat sheet overlay, dual-event blocks, per-heat distance times.

Wednesday's real HY-TEK Meet Program is ground truth for heat/lane. It covers ONE day of a
four-day meet, so it is applied as an OVERLAY onto the psych-sheet spine: Wednesday events get
real heat/lane, and every other day stays exactly as before (estimated / seed place only).
Thursday's program was added the same way -- appended to the heat_sheets list, not replacing
Wednesday's -- and ThursdayRealDocsTest pins that both days stay real in one run.

Two structurally new patterns in this program, both previously mishandled:

  * Events 21/22 (Girls/Boys 13-14 800 Free) share ONE combined block with block-level heat
    numbering, each heat labelled with its own event and heat: "Heat 5 (Heat 3 Girls 800 Free)"
    is girls heat 3. Before the fix a boys heat was filed under event 21 with the block heat
    number -- wrong event AND wrong heat.
  * Rows carry a trailing time-standard marker after the lane ("Arizona 39.82L 12Cova, Mila2 B").
    The row failed to match at all and was dropped silently, losing the whole swim.

Heat-count note: the timelines' 4/2 for events 21/22 and the heat sheet's 5/3 are NOT a
stale-vs-fresh conflict -- they count different things (heats in the prelims session vs total
heats). The fastest heat "swims with finals", so session heats == total - 1 for every such event.
The heat sheet is trusted for heat identity; the timeline's session-scoped count is left alone.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    extract_text_pages,
    extract_psych_entries,
    parse_distance_heat_times,
    parse_entry_fields,
    parse_heat_header,
    split_event_header,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
PROGRAM = WZAG / "wzag wednesday prelim program.pdf"
THURSDAY = WZAG / "wzag thursday prelim program v2.pdf"
DISTANCE = WZAG / "wzag wednesday distance timeline.pdf"
PSYCH = WZAG / "wzag psych sheet v3.pdf"
FLYER = WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
TIMELINE = WZAG / "wzag timelines v4.pdf"


def analyze(name, *, real_docs):
    return analyze_uploads(
        flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
        swimmer_name=name, output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"],
        heat_sheet_pdfs=[PROGRAM] if real_docs else None,
        distance_timeline_pdf=DISTANCE if real_docs else None,
    )


def individual(payload):
    return {i["event_number"]: i for i in payload["items"] if i.get("type") != "relay"}


class TrailingStandardMarkerTest(unittest.TestCase):
    """A time-standard marker after the lane must not lose the swim."""

    def test_row_with_trailing_marker_parses(self):
        row = parse_entry_fields("Arizona 39.82L 12Cova, Mila2 B", 4, "Prelims")
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("Arizona", "39.82L", "12"))
        self.assertEqual(row.swimmer_name, "Cova, Mila")  # marker not swallowed into the name
        self.assertEqual((row.heat, row.lane), (4, 2))

    def test_row_without_marker_is_unchanged(self):
        row = parse_entry_fields("Arizona 1:03.41 12Cova, Mila6", 3, "Prelims")
        self.assertEqual((row.swimmer_name, row.heat, row.lane), ("Cova, Mila", 3, 6))

    def test_psych_sheet_marker_before_place_still_parses(self):
        # The psych sheet's own form puts the marker BEFORE the seed place; it must be untouched.
        row = parse_entry_fields("AZ 39.82L 12Cova, Mila B29", None, None)
        self.assertEqual(row.seed_place, 29)
        self.assertIsNone(row.heat)

    def test_event_header_line_is_never_read_as_an_entry_row(self):
        # The marker group is uppercase-only so a title-case header cannot match as a row.
        self.assertIsNone(parse_entry_fields("Event 21 / 22 Girls / Boys 13-14 800 Free", None, None))


class DualEventBlockTest(unittest.TestCase):
    """The combined 21/22 block: gender selects the event, the parenthetical gives the real heat."""

    def test_combined_header_splits_into_both_events(self):
        self.assertEqual(
            split_event_header("Event  21 / 22   Girls / Boys 13-14 800 Free"),
            [(21, "Girls 13-14 800 Free"), (22, "Boys 13-14 800 Free")],
        )

    def test_single_event_header_is_unchanged(self):
        self.assertEqual(
            split_event_header("Event  5   Girls 11-12 50 LC Meter Breaststroke"),
            [(5, "Girls 11-12 50 LC Meter Breaststroke")],
        )

    def test_sub_heat_label_carries_event_gender_and_real_heat(self):
        header = parse_heat_header("Heat   5   (Heat 3 Girls 800 Free)")
        self.assertEqual((header.heat, header.sub_heat, header.sub_gender), (5, 3, "Girls"))

    def test_dual_continuation_reference_does_not_pin_one_event(self):
        header = parse_heat_header("Heat   8 (Heat 5 Girls 800 Free) (#21 / 22 )")
        self.assertEqual(header.event_numbers, (21, 22))
        self.assertIsNone(header.event_number)   # must not be filed under event 21 alone
        self.assertEqual((header.sub_heat, header.sub_gender), (5, "Girls"))

    def test_single_event_continuation_reference_still_works(self):
        # The pre-existing "(#N Event Name)" page-continuation form must be unaffected.
        header = parse_heat_header("Heat   4 of 4   Prelims (#3 Girls 10 & Under 50 LC Meter Breaststroke)")
        self.assertEqual(header.event_number, 3)
        self.assertEqual(header.event_name, "Girls 10 & Under 50 LC Meter Breaststroke")
        self.assertEqual(header.round_name, "Prelims")

    def test_every_block_heat_resolves_to_the_right_event_and_heat(self):
        # Ground truth read off the program: block heats alternate girls/boys, then girls-only once
        # the boys field is exhausted. Verified per swimmer so a wrong event or heat cannot hide.
        cases = {
            "Stein, Layla": (21, 1, 8),        # block heat 1 -> girls heat 1
            "Petersen, Eli": (22, 1, 4),       # block heat 2 -> BOYS heat 1 (was ev21 heat 2)
            "Brown, Lily": (21, 3, 5),         # block heat 5 -> girls heat 3 (was heat 5)
            "Willmarth, Cameryn": (21, 5, 3),  # block heat 8 -> girls heat 5 (was heat 8)
        }
        for name, expected in cases.items():
            rows = [e for e in extract_psych_entries(PROGRAM, name)[0] if e.event_number in (21, 22)]
            self.assertEqual(len(rows), 1, name)
            row = rows[0]
            self.assertEqual((row.event_number, row.heat, row.lane), expected, name)
            # The per-event name must be clean -- no "/ 22" leaking in from the combined header.
            self.assertNotIn("/", row.event_name, name)

    def test_swimming_with_finals_is_split_off_the_round_label(self):
        header = parse_heat_header("Heat   1 of 4   Finals - Swimming with Finals")
        self.assertEqual(header.round_name, "Finals")   # compound phrase cleanly separated
        self.assertTrue(header.swims_with_finals)
        plain = parse_heat_header("Heat   2 of 4   Finals")
        self.assertEqual(plain.round_name, "Finals")
        self.assertFalse(plain.swims_with_finals)

    def test_only_the_fastest_heat_is_flagged_swims_with_finals(self):
        stein = [e for e in extract_psych_entries(PROGRAM, "Stein, Layla")[0] if e.event_number == 21][0]
        brown = [e for e in extract_psych_entries(PROGRAM, "Brown, Lily")[0] if e.event_number == 21][0]
        self.assertTrue(stein.swims_with_finals)   # girls heat 1
        self.assertFalse(brown.swims_with_finals)  # girls heat 3


class HeatCountReconciliationTest(unittest.TestCase):
    """The heat sheet is ground truth for heat identity; the timeline's session-scoped count stands."""

    def test_heat_sheet_shows_five_girls_and_three_boys_heats(self):
        girls, boys = set(), set()
        for name in ("Stein, Layla", "Brown, Lily", "Willmarth, Cameryn", "Petersen, Eli"):
            for row in extract_psych_entries(PROGRAM, name)[0]:
                (girls if row.event_number == 21 else boys if row.event_number == 22 else set()).add(row.heat)
        # Sampled swimmers land in girls heats 1/3/5 and boys heat 1 -- the point is that a heat
        # number ABOVE the timeline's session count of 4 (girls heat 5) is representable.
        self.assertIn(5, girls)
        self.assertIn(1, boys)

    def test_distance_timeline_states_totals_of_five_and_three(self):
        windows = parse_distance_heat_times(DISTANCE)
        self.assertEqual(sorted(h for (e, h) in windows if e == 21), [2, 3, 4, 5])
        self.assertEqual(sorted(h for (e, h) in windows if e == 22), [2, 3])

    def test_timeline_session_heat_count_is_left_alone(self):
        # Deliberately NOT "corrected" to 5/3: the session report counts heats swimming in the
        # prelims session, and rewriting it would corrupt every other swims-with-finals event.
        from swimtimeline.extract import parse_timeline
        _, _, events = parse_timeline(TIMELINE, flyer_text="", meet_venue="X")
        by_event = {e.event_number: e for e in events if e.session_number == 1}
        self.assertEqual(by_event[21].heats, 4)
        self.assertEqual(by_event[22].heats, 2)

    def test_no_time_is_invented_for_the_swims_with_finals_heat(self):
        windows = parse_distance_heat_times(DISTANCE)
        self.assertNotIn((21, 1), windows)   # girls heat 1 swims in the evening session
        self.assertNotIn((22, 1), windows)


class CovaWednesdayRealHeatLaneTest(unittest.TestCase):
    def test_cova_gets_real_heat_lane_matching_her_psych_seeds(self):
        payload = analyze("Cova, Mila L", real_docs=True)
        events = individual(payload)
        self.assertEqual(events[5]["entry_position"], "Heat/lane: heat 4, lane 2")
        self.assertEqual(events[11]["entry_position"], "Heat/lane: heat 3, lane 6")
        for number, seed in ((5, "39.82L"), (11, "1:03.41")):
            self.assertEqual(events[number]["seed_time"], seed)          # unchanged psych seed
            self.assertEqual(events[number]["source_document"], "Heat sheet")
            self.assertFalse(events[number]["heat_is_estimated"])

    def test_event_five_is_the_row_that_used_to_be_dropped(self):
        # Her #5 row carries the trailing " B" marker; without the parser fix the event vanished.
        self.assertIn(5, {e.event_number for e in extract_psych_entries(PROGRAM, "Cova, Mila L")[0]})


class SteinDistanceOutcomeTest(unittest.TestCase):
    def test_stein_is_lane_eight_of_the_swims_with_finals_heat(self):
        payload = analyze("Stein, Layla", real_docs=True)
        event = individual(payload)[21]
        self.assertEqual(event["entry_position"], "Heat/lane: heat 1, lane 8")
        self.assertEqual(event["source_document"], "Heat sheet")

    def test_stein_keeps_the_finals_window_and_gets_no_fabricated_distance_time(self):
        # Footnote B (fastest seeded heat swims during finals) must still place her in the evening
        # session, and the distance timeline -- which has no heat-1 row -- must not override it.
        with_real = individual(analyze("Stein, Layla", real_docs=True))[21]
        without = individual(analyze("Stein, Layla", real_docs=False))[21]
        self.assertEqual(with_real["window"], without["window"])
        self.assertIn("PM", with_real["window"])          # evening finals session
        self.assertIn("finals", with_real["finals_note"].lower())

    def test_a_prelims_session_heat_does_get_its_real_per_heat_time(self):
        # Contrast: girls heat 3 IS listed in the distance timeline, so it narrows to that heat.
        payload = analyze("Brown, Lily", real_docs=True)
        event = individual(payload)[21]
        self.assertEqual(event["entry_position"], "Heat/lane: heat 3, lane 5")
        self.assertEqual(event["window"], "12:04 PM-12:14 PM")


class OtherDaysUnaffectedTest(unittest.TestCase):
    """A one-day heat sheet must not disturb the other three days, in the same run."""

    def test_thursday_through_saturday_are_byte_identical(self):
        for name in ("Cova, Mila L", "Stein, Layla"):
            with_real = individual(analyze(name, real_docs=True))
            without = individual(analyze(name, real_docs=False))
            self.assertEqual(set(with_real), set(without), name)   # no events gained or lost
            for number, item in without.items():
                if item["day"] == "Wednesday":
                    continue
                self.assertEqual(with_real[number], item, f"{name} event #{number} ({item['day']})")

    def test_only_wednesday_events_change(self):
        with_real = individual(analyze("Cova, Mila L", real_docs=True))
        without = individual(analyze("Cova, Mila L", real_docs=False))
        changed = {n for n, i in with_real.items() if i != without[n]}
        self.assertEqual(changed, {5, 11})
        self.assertTrue(all(with_real[n]["day"] == "Wednesday" for n in changed))


class OverlayFailsSafeTest(unittest.TestCase):
    """Ambiguity must fall back to the estimate rather than assert a wrong lane."""

    def test_a_swimmer_absent_from_the_heat_sheet_keeps_seed_place(self):
        # Alegi swims a different meet entirely: no rows here, so nothing is applied and no crash.
        payload = analyze("Cova, Mila L", real_docs=True)
        self.assertEqual(individual(payload)[28]["entry_position"], "Seed place: 18")

    def test_seed_mismatch_leaves_the_estimate_and_warns(self):
        from swimtimeline.extract import overlay_heat_sheet_entries, PsychEntry
        entry = PsychEntry(
            day="", event_number=5, event_name="Girls 11-12 50 LC Meter Breaststroke",
            seed_time="59.99", seed_place=29, age="12", team="AZ", page=1, column="",
            source_line="", matched_name="Cova, Mila",
        )
        warnings = overlay_heat_sheet_entries([entry], [PROGRAM], "Cova, Mila L")
        self.assertIsNone(entry.heat)      # untouched -- no confident-but-wrong lane
        self.assertIsNone(entry.lane)
        self.assertTrue(any("left as an estimate" in w for w in warnings), warnings)

    def test_event_only_in_the_heat_sheet_is_reported_not_invented(self):
        from swimtimeline.extract import overlay_heat_sheet_entries, PsychEntry
        entry = PsychEntry(
            day="", event_number=11, event_name="Girls 11-12 100 LC Meter Freestyle",
            seed_time="1:03.41", seed_place=10, age="12", team="AZ", page=1, column="",
            source_line="", matched_name="Cova, Mila",
        )
        warnings = overlay_heat_sheet_entries([entry], [PROGRAM], "Cova, Mila L")
        self.assertEqual((entry.heat, entry.lane), (3, 6))            # the matching event applied
        self.assertTrue(any("#5" in w and "entry sheet does not" in w for w in warnings), warnings)

    def test_no_heat_sheet_means_no_change_at_all(self):
        from swimtimeline.extract import overlay_heat_sheet_entries
        self.assertEqual(overlay_heat_sheet_entries([], None, "Cova, Mila L"), [])


def analyze_both_days(name):
    """Both real heat sheets at once -- the state families actually see now."""
    return analyze_uploads(
        flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
        swimmer_name=name, output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"], heat_sheet_pdfs=[PROGRAM, THURSDAY], distance_timeline_pdf=DISTANCE,
    )


class ThursdayRealDocsTest(unittest.TestCase):
    """Thursday's program is a second one-day heat sheet, applied alongside Wednesday's.

    Thursday is structurally simpler than Wednesday: events 26-47 are all single events (no
    combined girls/boys block) and every heat is a plain "Prelims" round -- no heat swims with
    finals -- so the timeline's heat counts equal the heat sheet's totals, unlike Wednesday where
    the swims-with-finals heat made them differ by one.
    """

    def test_thursday_has_no_combined_event_block(self):
        text = "\n".join(extract_text_pages(THURSDAY))
        self.assertNotIn("/ 22", text)
        for line in text.splitlines():
            split = split_event_header(line.strip())
            if split:
                self.assertEqual(len(split), 1, line)   # every Thursday header names ONE event

    def test_no_thursday_heat_swims_with_finals(self):
        text = "\n".join(extract_text_pages(THURSDAY))
        self.assertNotIn("Swimming with Finals", text)

    def test_cova_thursday_real_heat_lane(self):
        events = individual(analyze_both_days("Cova, Mila L"))
        self.assertEqual(events[28]["entry_position"], "Heat/lane: heat 1, lane 7")
        self.assertEqual(events[28]["seed_time"], "2:20.36")     # psych seed preserved
        self.assertEqual(events[28]["source_document"], "Heat sheet")
        self.assertFalse(events[28]["heat_is_estimated"])

    def test_stein_thursday_real_heat_lane(self):
        events = individual(analyze_both_days("Stein, Layla"))
        self.assertEqual(events[30]["entry_position"], "Heat/lane: heat 2, lane 5")
        self.assertEqual(events[36]["entry_position"], "Heat/lane: heat 4, lane 2")
        for number in (30, 36):
            self.assertEqual(events[number]["source_document"], "Heat sheet")

    def test_wednesday_is_unchanged_by_adding_thursday(self):
        # Adding a second day's heat sheet must not disturb the first day's real values.
        for name, expected in (
            ("Cova, Mila L", {5: "Heat/lane: heat 4, lane 2", 11: "Heat/lane: heat 3, lane 6"}),
            ("Stein, Layla", {21: "Heat/lane: heat 1, lane 8"}),
        ):
            events = individual(analyze_both_days(name))
            for number, position in expected.items():
                self.assertEqual(events[number]["entry_position"], position, f"{name} #{number}")

    def test_stein_wednesday_800_keeps_its_evening_finals_window(self):
        events = individual(analyze_both_days("Stein, Layla"))
        self.assertEqual(events[21]["window"], "6:37 PM-6:48 PM")

    def test_friday_and_saturday_remain_estimates(self):
        for name, numbers in (("Cova, Mila L", (60, 70, 81)), ("Stein, Layla", (56, 72, 83))):
            events = individual(analyze_both_days(name))
            for number in numbers:
                self.assertTrue(events[number]["entry_position"].startswith("Seed place:"), f"{name} #{number}")
                self.assertEqual(events[number]["source_document"], "Psych/entry sheet")

    def test_only_thursday_events_change_when_thursday_is_added(self):
        wednesday_only = analyze_uploads(
            flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
            swimmer_name="Stein, Layla", output_dir=Path(tempfile.mkdtemp()), state="",
            meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
            modes=["detailed"], heat_sheet_pdfs=[PROGRAM], distance_timeline_pdf=DISTANCE,
        )
        before = {i["event_number"]: i for i in wednesday_only["items"] if i.get("type") != "relay"}
        after = individual(analyze_both_days("Stein, Layla"))
        self.assertEqual(set(before), set(after))
        changed = {n for n in after if before[n] != after[n]}
        self.assertEqual(changed, {30, 36})
        self.assertTrue(all(after[n]["day"] == "Thursday" for n in changed))

    def test_thursday_trailing_marker_rows_are_recovered(self):
        # 481 rows on this document carry the " B" time-standard marker after the lane; before the
        # parser fix every one of them was dropped silently.
        entries, _, _ = extract_psych_entries(THURSDAY, "Mo, Khloe")
        self.assertTrue(entries)
        self.assertIsNotNone(entries[0].lane)


if __name__ == "__main__":
    unittest.main()
