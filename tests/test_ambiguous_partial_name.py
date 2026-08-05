"""A partial name that matches several real swimmers must never merge them into one calendar.

Reported live during the 2026 WZAG meet: searching "Stein" produced a Wednesday calendar anchored
at 6:30 AM with a Snake River warm-up, so Layla Stein's 800 Free (heat 1, which swims with the
evening finals at 6:37 PM) appeared inside a morning block. The cause was not her event at all --
"Stein" is a SUBSTRING of "Steinbis", so the query also matched River and Cam Steinbis (a different
family, Snake River, one a 10 & Under girl and one a 13-14 boy) and all three children's events were
merged under the single label "Stein".

Two layers fix it. First, a query now matches only at NAME-TOKEN boundaries, so "Stein" no longer
reaches Steinbis at all -- and no longer reaches Abbie Wein|stein| at a meet with no Stein, which was
silently returning a stranger's calendar with no warning (a single match, so no ambiguity guard could
have caught it). Second, when a query still resolves to several real swimmers -- true namesakes like
the two AZ Yang siblings plus Yi Yang -- it refuses and names them instead of merging their events.
The ambiguity guard previously ran only on the fuzzy fallback; a substring hit counts as an exact
match, so it never protected this path. Unambiguous partials ("Cova", "Stein", "Horst") still resolve.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import (
    PsychEntry,
    ambiguous_swimmer_candidates,
    analyze_uploads,
    extract_psych_entries,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
PSYCH = WZAG / "wzag psych sheet v3.pdf"
FLYER = WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
TIMELINE = WZAG / "wzag timelines v4.pdf"
PROGRAM = WZAG / "wzag wednesday prelim program.pdf"
DISTANCE = WZAG / "wzag wednesday distance timeline.pdf"


def psych_entry(matched_name, event_number=5):
    return PsychEntry(
        day="", event_number=event_number, event_name="Girls 11-12 50 LC Meter Freestyle",
        seed_time="30.00", seed_place=1, age="12", team="AZ", page=1, column="",
        source_line="", matched_name=matched_name,
    )


def analyze(name):
    return analyze_uploads(
        flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
        swimmer_name=name, output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["daily"], heat_sheet_pdfs=[PROGRAM], distance_timeline_pdf=DISTANCE,
    )


class AmbiguousCandidatesHelperTest(unittest.TestCase):
    def test_several_distinct_swimmers_are_reported(self):
        entries = [psych_entry("Stein, Layla"), psych_entry("Steinbis, River"), psych_entry("Steinbis, Cam")]
        self.assertEqual(
            ambiguous_swimmer_candidates(entries),
            ["Cam Steinbis", "Layla Stein", "River Steinbis"],
        )

    def test_name_variants_of_one_swimmer_are_not_ambiguous(self):
        # The same real swimmer prints differently per row; those must fold to one person.
        entries = [psych_entry("Stein, Layla B"), psych_entry("Stein, Layla WZAG")]
        self.assertIsNone(ambiguous_swimmer_candidates(entries))

    def test_single_swimmer_is_not_ambiguous(self):
        self.assertIsNone(ambiguous_swimmer_candidates([psych_entry("Cova, Mila")]))


class TokenBoundaryMatchingTest(unittest.TestCase):
    """A query matches only at name-token boundaries, so it can neither merge nor mis-resolve.

    Unanchored substring matching was the underlying defect: "Stein" hit Steinbis (merge) and, at a
    meet with no Stein at all, hit Abbie Wein|stein| (wrong swimmer, single match -- which the
    ambiguity guard cannot catch, because only one swimmer matched).
    """

    def test_stein_now_resolves_to_just_layla_stein(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Stein")
        self.assertEqual(warnings, [])
        self.assertTrue(entries)
        self.assertTrue(all("Stein, Layla" in e.matched_name for e in entries))

    def test_surname_embedded_inside_another_surname_is_not_matched(self):
        # Declan Horst must not be blocked by Tayler Walken|horst|.
        entries, _, warnings = extract_psych_entries(PSYCH, "Horst")
        self.assertEqual(warnings, [])
        self.assertTrue(all("Horst, Declan" in e.matched_name for e in entries))

    def test_a_meet_with_no_such_swimmer_returns_nobody_not_a_lookalike(self):
        # Shark Open has no Stein -- it has Abbie Weinstein. Returning her calendar was silently
        # handing a family a stranger's schedule with no warning at all.
        shark = ROOT / "meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf"
        entries, _, _ = extract_psych_entries(shark, "Stein")
        self.assertEqual(entries, [])

    def test_short_surname_no_longer_matches_dozens_of_swimmers(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Li")
        self.assertEqual(warnings, [])
        self.assertTrue(all(e.matched_name.startswith("Li,") for e in entries))

    def test_cova_resolves_at_a_meet_that_also_has_covault(self):
        az = ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-psych-sheet.pdf"
        entries, _, warnings = extract_psych_entries(az, "Cova")
        self.assertEqual(warnings, [])
        self.assertTrue(all("Cova, Mila" in e.matched_name for e in entries))


class GenuineAmbiguityStillRefusedTest(unittest.TestCase):
    """Real namesakes still refuse -- including two AZ siblings, which anchoring cannot resolve."""

    def test_two_siblings_plus_a_namesake_are_refused(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Yang")
        self.assertEqual(entries, [])
        self.assertEqual(len(warnings), 1)
        for expected in ("Richelle Yang", "Roddy Yang", "Yi Yang"):
            self.assertIn(expected, warnings[0])
        self.assertIn("more specific name", warnings[0])

    def test_a_query_matching_someones_first_name_is_refused(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Carter")
        self.assertEqual(entries, [])
        self.assertIn("Carter Goldthorpe", warnings[0])
        self.assertIn("Izzy Carter", warnings[0])

    def test_a_ten_way_surname_collision_is_refused(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Wang")
        self.assertEqual(entries, [])
        self.assertIn("Amy Wang", warnings[0])
        self.assertIn("Andy Wang", warnings[0])

    def test_full_name_still_resolves_to_just_that_swimmer(self):
        for query in ("Stein, Layla", "Layla Stein"):
            entries, _, warnings = extract_psych_entries(PSYCH, query)
            self.assertEqual(warnings, [], query)
            self.assertTrue(entries, query)
            self.assertTrue(all("Stein, Layla" in e.matched_name for e in entries), query)

    def test_the_other_family_still_resolves(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Steinbis, River")
        self.assertEqual(warnings, [])
        self.assertTrue(entries)

    def test_unambiguous_partial_name_still_works(self):
        # "Cova" is not a prefix of any other surname here, so the partial-name feature is intact.
        entries, _, warnings = extract_psych_entries(PSYCH, "Cova")
        self.assertEqual(warnings, [])
        self.assertEqual(len(entries), 6)


class SteinCalendarIsEveningTest(unittest.TestCase):
    """The reported symptom: her 800 Free must not sit in a morning-anchored Wednesday block."""

    def test_layla_stein_wednesday_is_the_evening_finals_session(self):
        payload = analyze("Stein, Layla")
        wednesday = [i for i in payload["items"] if i.get("type") != "relay" and i["day"] == "Wednesday"]
        self.assertEqual(len(wednesday), 1)
        event = wednesday[0]
        self.assertEqual(event["event_number"], 21)
        self.assertEqual(event["entry_position"], "Heat/lane: heat 1, lane 8")
        self.assertEqual(event["window"], "6:37 PM-6:48 PM")

    def test_bare_surname_stein_now_gives_her_evening_calendar_not_a_merged_one(self):
        # The originally reported query. It no longer merges the Steinbis children, so it resolves
        # to her alone -- and her Wednesday is the evening finals session, never a 6:30 AM block.
        payload = analyze("Stein")
        wednesday = [i for i in payload["items"] if i.get("type") != "relay" and i["day"] == "Wednesday"]
        self.assertEqual([i["event_number"] for i in wednesday], [21])
        self.assertEqual(wednesday[0]["window"], "6:37 PM-6:48 PM")
        # None of the Steinbis children's morning swims leak in.
        self.assertFalse({9, 14, 15, 20} & {i["event_number"] for i in payload["items"]})

    def test_payload_flags_ambiguity_so_the_ui_does_not_say_no_events_found(self):
        # Zero events for a genuinely ambiguous name means "too many matches", not "nothing
        # scheduled". The UI keys off this flag to ask for a first name instead of offering an
        # empty calendar to import.
        self.assertTrue(analyze("Yang")["ambiguous_swimmer_match"])
        self.assertFalse(analyze("Stein, Layla")["ambiguous_swimmer_match"])


if __name__ == "__main__":
    unittest.main()
