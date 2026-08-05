"""A partial name that matches several real swimmers must never merge them into one calendar.

Reported live during the 2026 WZAG meet: searching "Stein" produced a Wednesday calendar anchored
at 6:30 AM with a Snake River warm-up, so Layla Stein's 800 Free (heat 1, which swims with the
evening finals at 6:37 PM) appeared inside a morning block. The cause was not her event at all --
"Stein" is a SUBSTRING of "Steinbis", so the query also matched River and Cam Steinbis (a different
family, Snake River, one a 10 & Under girl and one a 13-14 boy) and all three children's events were
merged under the single label "Stein".

Partial queries are matched as substrings and that counts as an "exact" match, so the distinct-
swimmer ambiguity guard -- which already protected the fuzzy fallback -- never ran on this path.
It now guards both. An unambiguous partial ("Cova") still resolves, since that is a real feature.
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


class SubstringPrefixCollisionTest(unittest.TestCase):
    """The exact/substring path is now guarded, not just the fuzzy fallback."""

    def test_stein_is_refused_and_names_all_three_swimmers(self):
        entries, _, warnings = extract_psych_entries(PSYCH, "Stein")
        self.assertEqual(entries, [])   # nothing merged
        self.assertEqual(len(warnings), 1)
        for expected in ("Layla Stein", "River Steinbis", "Cam Steinbis"):
            self.assertIn(expected, warnings[0])
        self.assertIn("more specific name", warnings[0])

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

    def test_ambiguous_stein_query_produces_no_calendar_at_all(self):
        # Better an explicit "be more specific" than a calendar that would send a family to the
        # wrong session: the merged version started Wednesday at 6:30 AM with an SR warm-up.
        payload = analyze("Stein")
        self.assertEqual(payload["verified_event_count"], 0)
        self.assertTrue(any("matches more than one swimmer" in w for w in payload["warnings"]))

    def test_merged_calendar_no_longer_mixes_other_childrens_events(self):
        payload = analyze("Stein")
        morning_events = {9, 14, 15, 20}   # Steinbis children's Wednesday prelims swims
        self.assertFalse(morning_events & {i["event_number"] for i in payload["items"]})

    def test_payload_flags_ambiguity_so_the_ui_does_not_say_no_events_found(self):
        # Zero events here means "too many matches", not "nothing scheduled". The UI keys off this
        # flag to ask for a first name instead of offering an empty calendar to import.
        self.assertTrue(analyze("Stein")["ambiguous_swimmer_match"])
        self.assertFalse(analyze("Stein, Layla")["ambiguous_swimmer_match"])


if __name__ == "__main__":
    unittest.main()
